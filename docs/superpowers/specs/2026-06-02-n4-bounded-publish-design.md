# N4 Remediation — Bound the Celery Broker Publish — Design

**Status:** Draft for review
**Date:** 2026-06-02
**Remediates:** Audit finding **N4 (Low)** from the [P2P/networking audit](../audits/2026-06-01-network-p2p-audit.md): the Celery broker publish runs synchronously on the web-request thread. With `CC_API_ASYNC_PROCESSING=true`, `queue_post_process` fires the `http_post` signal synchronously in the request thread → `handle_http_post` calls `post_process.delay(...)`, which publishes to the broker inline. On a down/slow broker, Celery's default publish-retry policy (`task_publish_retry=True`, `max_retries=3`, backoff) combined with `broker_connection_timeout=4` stalls the request ~16s before `.delay()` raises — tying up a WSGI worker and coupling node HTTP availability to broker liveness on the synchronous path. **This is the final open finding; closing it brings the networking audit to 0/0/0/0.**

## Problem

`tasks.init_tasks` (`src/cancelchain/tasks.py`) configures Celery with a bare `celery.conf.update(app.config)` — no broker/publish bounding. So the broker publish inherits Celery's unbounded-ish defaults (confirmed by inspection):

- `task_publish_retry = True`, `task_publish_retry_policy = {max_retries: 3, interval_start: 0, interval_max: 1, interval_step: 0.2}`
- `broker_connection_timeout = 4`
- `broker_connection_max_retries = 100`

On a dead broker, `post_process.delay()` (fired in `handle_http_post`, synchronously on the request thread via the `http_post` signal) attempts the publish, retries 3× with backoff, each attempt waiting on a 4s connection timeout — roughly ~16s before raising. The raise propagates to the view's `except Exception: exception_response(e)` → `abort(500)`. So a broker outage turns every gossip POST into a ~16s-blocked worker.

**Severity is Low** because the path is operator-gated (requires `CC_API_ASYNC_PROCESSING=true` *and* a broker that is configured but down/slow), the stall is bounded (~16s) by Celery defaults, the peer cannot induce the broker outage (it is operator config + infra failure), and the post-process work is *best-effort peer-forwarding* of a block/txn that is already persisted locally.

## Goal

Bound the broker publish so a degraded broker fast-fails the enqueue (~2s) instead of stalling the request thread ~16s, and flip the N4 finding closed — taking the networking audit to 0 Critical / 0 High / 0 Medium / 0 Low.

## Approach

A config-only change in `init_tasks`: set bounded publish defaults *before* the operator's `celery.conf.update(app.config)`, so the default is bounded but an operator can still override via config. The publish stays on the request thread (we chose not to move it off-thread — see Alternatives), but a dead-broker publish now fails in ~2s, which frees the worker promptly and keeps the failure honest (the POST 500s rather than silently dropping the forward).

### Component: `init_tasks` (`src/cancelchain/tasks.py`)

```python
def init_tasks(app: Flask) -> Celery:
    # Bounded publish defaults (N4): on a down/slow broker, the synchronous
    # post_process.delay() publish must fail fast (~2s) rather than stalling
    # the web-request thread ~16s on Celery's default publish-retry policy.
    # Applied before app.config so an operator can still override.
    celery.conf.update(
        task_publish_retry=False,
        broker_connection_timeout=2.0,
        broker_connection_max_retries=0,
    )
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
```

- `task_publish_retry=False` — the publish is attempted once, not retried 3× (the dominant contributor to the ~16s).
- `broker_connection_timeout=2.0` — caps a single connection attempt at 2s (was 4s).
- `broker_connection_max_retries=0` — no connection-acquisition retries.

Worst case on a dead broker: one connection attempt, ~2s, no retries → `.delay()` raises in ~2s. `handle_http_post` and `queue_post_process` are unchanged.

### Why not move the publish off-thread

Considered (a thread/executor so the POST returns 202 immediately regardless of broker health). Rejected for this Low finding: it adds threading/executor lifecycle and test-synchronization complexity, and makes a publish failure a silent fire-and-forget drop. The bounded-timeout approach is config-only, keeps the failure honest (no silent drop), and frees the worker in ~2s — proportionate to a Low, operator-gated finding. The full off-thread decoupling can be revisited if async throughput ever makes a ~2s worst-case publish material.

## Error handling

No new error paths. A dead-broker publish still raises (now in ~2s instead of ~16s), caught by the view's existing `except Exception → exception_response → abort(500)`. The block/txn is already persisted before `queue_post_process` fires; only the best-effort peer-forward is affected, and it fails visibly (500) rather than silently.

## Testing

### Replace the demonstration test (off-thread → bounded-config)

The merged `tests/test_network_audit.py::test_n4_async_publish_blocks_request_thread` asserts the off-thread approach we did not take. Replace it (same `N4` slot) with a config-contract regression `test_n4_broker_publish_is_bounded`:

```python
def test_n4_broker_publish_is_bounded(app):
    """N4: the broker publish is bounded (no unbounded publish/connection
    retries, short connection timeout) so a down/slow broker fast-fails the
    enqueue (~2s) instead of stalling the web-request thread ~16s.
    init_tasks(app) runs during create_app, so celery.conf reflects the fix.
    """
    from cancelchain.tasks import celery

    assert celery.conf.task_publish_retry is False
    assert celery.conf.broker_connection_timeout <= 2.0
    assert celery.conf.broker_connection_max_retries == 0
```

Pre-fix, Celery's defaults (`task_publish_retry=True`, `broker_connection_timeout=4`, `broker_connection_max_retries=100`) fail these assertions — the TDD red. Post-fix, the bounded values pass. The old test's `@pytest.mark.xfail` marker is removed (this is a passing regression after the fix). Drop the now-unused imports the old test pulled in (e.g. `threading`, the `Miller`/`ApiClient` block-POST scaffolding) if no other test in the file uses them.

### Regression suite

Full suite stays green. After this change: `tests/test_network_audit.py` shows **0 xfailed + 8 passed** (3 N1 + 2 N2 + 2 N3 + the new N4 config test); `--runxfail tests/test_network_audit.py` has nothing left to fail. All five CI gates green; `mypy --strict` clean (no signature changes; `tasks.py` already carries its module-level mypy overrides).

## Documentation updates

- **Audit report** (`docs/superpowers/audits/2026-06-01-network-p2p-audit.md`): mark **N4** remediated (✅ on the finding, table row Status, recommendation item 4; past-tense the gap; `(As implemented: …)` note). Update headline **0 Critical / 0 High / 0 Medium / 1 Low → 0 Critical / 0 High / 0 Medium / 0 Low** and state the audit is fully closed. Update the executive summary to reflect all four findings remediated.
- **CLAUDE.md**: in the async post-processing section, note that the broker publish is bounded (`task_publish_retry=False` + a short `broker_connection_timeout`) so a degraded broker fast-fails the enqueue rather than stalling the request thread.
- **Roadmap** (`docs/superpowers/ROADMAP.md`): mark the N4 bullet ✅ with the impl PR number; mark the **P2P/networking audit fully closed (0/0/0/0)** and move the section to the closed-items area (mirroring the verification-pipeline and API-auth closed audits). The indexed-mempool-expiry perf follow-up remains as the one open forward item.

## Out of scope

- Moving the publish off the request thread (the rejected alternative; revisit only if throughput demands).
- The deferred indexed/SQL-filtered mempool expiry perf follow-up (from N2) — separate, still tracked.
- Any change to `handle_http_post`/`queue_post_process` (unchanged) or the Celery worker task body.

## Acceptance criteria

- `init_tasks` sets `task_publish_retry=False`, `broker_connection_timeout<=2.0`, `broker_connection_max_retries=0` (applied before `celery.conf.update(app.config)` so operators can override).
- A dead-broker `post_process.delay()` fails in ~2s, not ~16s (bounded by the above).
- `test_n4_broker_publish_is_bounded` passes; the old off-thread N4 test is removed; full suite green (`tests/test_network_audit.py`: 0 xfailed + 8 passed).
- Audit report headline `0 Critical / 0 High / 0 Medium / 0 Low`, audit fully closed; N4 ✅; CLAUDE.md + roadmap updated (audit moved to closed items).
