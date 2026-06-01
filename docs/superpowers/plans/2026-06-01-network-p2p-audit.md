# P2P / Networking Threat-Modeled Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended for this plan — see note) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a threat-modeled security audit of the cancelchain P2P/networking layer — a report at `docs/superpowers/audits/2026-06-01-network-p2p-audit.md` plus strict-xfail demonstration tests in `tests/test_network_audit.py` — driven by a three-phase multi-agent Workflow fan-out.

**Architecture:** A Workflow fans out one analyst agent per adversary category over the in-scope code (discover), adversarially refutes each candidate finding (verify), and synthesizes survivors into a report-ready finding set. The controller then writes the report and one `@pytest.mark.xfail(strict=True)` demonstration per finding (availability findings use a bounded-observation convention that never exhausts real resources). The audit ships as a docs PR; remediation of each finding is separate downstream work.

**Tech Stack:** Python 3.12+, pytest, `@pytest.mark.xfail(strict=True)`, the `requests_proxy`/`remote_requests_proxy` WSGI fixtures, `time_machine`, the Workflow multi-agent tool.

> **Execution note — run inline, not subagent-driven.** The audit's findings are synthesized by the Workflow (invoked by the controller, which holds the user's explicit opt-in) and the report+tests are written directly from that in-context output. Fresh per-task subagents would have to be re-fed the entire finding set, defeating the point. Recommend **inline execution (executing-plans)**: the controller runs Task 2's Workflow and writes up Tasks 3–4 from its result. Tasks remain individually committable.

**Authoritative design:** `docs/superpowers/specs/2026-06-01-network-p2p-audit-design.md`. Read it first — scope razor, adversary categories, severity rubric, and the bounded-observation test convention live there.

---

## File structure

| File | Responsibility | Created/Modified |
|---|---|---|
| `tests/test_network_audit.py` | One strict-xfail demonstration per finding + a shared mock-peer helper | Create (Task 1 scaffold, Task 4 fill) |
| `docs/superpowers/audits/2026-06-01-network-p2p-audit.md` | The audit report (exec summary, traces, findings table, recommendations) | Create (Task 3) |
| `docs/superpowers/ROADMAP.md` | Add "Audit remediation — networking findings" tracking entry | Modify (Task 5) |
| `/tmp/network-audit-findings.json` *(scratch, not committed)* | Workflow's synthesized finding set, consumed by Tasks 3–4 | Transient (Task 2) |

In-scope source (read-only for the audit — no source edits in this plan; remediation is downstream):
`src/cancelchain/node.py`, `src/cancelchain/miller.py`, `src/cancelchain/api.py` (peer-facing POST views + the `queue_post_process`/`handle_http_post` async path), `src/cancelchain/api_client.py`, `src/cancelchain/tasks.py`, and the in-scope DAOs/structures in `src/cancelchain/models.py` (`PendingTxnDAO` ~835, `PendingIOflowDAO` ~893, `ChainFill` ~917, `ChainFillBlock` ~941) and `src/cancelchain/transaction.py` (`PendingTxnSet` ~379).

---

## Task 1: Branch + test scaffold + baseline

**Files:**
- Create: `tests/test_network_audit.py`

- [ ] **Step 1: Branch off main**

```bash
cd /home/gumptionthomas/Development/cancelchain
git checkout main && git pull --ff-only
git checkout -b audit/network-p2p
uv run pytest -q 2>&1 | tail -1   # baseline — record the count (e.g. "286 passed, 1 skipped")
```
If the suite is not green, STOP and report.

- [ ] **Step 2: Create the test scaffold**

Create `tests/test_network_audit.py` with the module docstring, imports, and the shared mock-peer helper. This is the only deterministic test code; the per-finding tests (Task 4) are appended below it.

```python
"""Demonstration tests for the 2026-06-01 P2P/networking threat-model audit.

Each test below demonstrates one audit finding and is marked
``@pytest.mark.xfail(strict=True)`` — strict mode means the test MUST fail
today (the gap is real) and forces the marker's removal when the finding is
remediated (the xfail would otherwise "unexpectedly pass" and error the
suite). See docs/superpowers/audits/2026-06-01-network-p2p-audit.md.

Availability findings use a *bounded-observation* convention: drive the
uncapped behavior only up to a small, safe bound and assert the missing cap
is observable. No test exhausts real memory, disk, or wall-clock.
"""

import pytest

from cancelchain.block import Block
from cancelchain.node import Node

# Shared fixtures (app, *_wallet, requests_proxy, remote_requests_proxy,
# mill_block, host, time_stepper) come from tests/conftest.py.


def staged_chain_fill_count(app):
    """Count ChainFillBlock rows currently staged — used by availability
    tests that assert fill_chain stages an attacker-controlled number of
    blocks with no depth cap.

    NB: this project uses SQLAlchemy 2.0 with a plain ``DeclarativeBase``
    (``SQLAlchemy(model_class=Base)``), NOT ``db.Model`` — so the legacy
    ``Model.query`` attribute does NOT exist here (it raises AttributeError).
    Use the 2.0 count idiom (mirrors ``tests/_sa_helpers._count``).
    """
    from cancelchain.database import db
    from cancelchain.models import ChainFillBlock

    with app.app_context():
        return (
            db.session.scalar(
                db.select(db.func.count()).select_from(ChainFillBlock)
            )
            or 0
        )
```

- [ ] **Step 3: Verify the scaffold imports cleanly and doesn't change counts**

```bash
uv run pytest tests/test_network_audit.py -q 2>&1 | tail -3   # "no tests ran" is fine
uv run pytest -q 2>&1 | tail -1                                # same baseline as Step 1
uv run ruff check tests/test_network_audit.py
```
Expected: collection succeeds, full-suite count unchanged, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_network_audit.py
git commit -m "test(audit): scaffold network-p2p demonstration test module"
```

---

## Task 2: Run the audit Workflow (discover → verify → synthesize)

> Run by the controller (holds the multi-agent Workflow opt-in). This task produces the confirmed finding set consumed by Tasks 3–4. No files are committed in this task; the Workflow's structured return is saved to `/tmp/network-audit-findings.json` for the next tasks.

- [ ] **Step 1: Invoke the Workflow with the script below**

Call the `Workflow` tool with this exact `script`. It pipelines each adversary category through an analyst (discover) and an adversarial refuter per candidate (verify), then synthesizes survivors.

```javascript
export const meta = {
  name: 'network-p2p-audit',
  description: 'Threat-model the cancelchain P2P/networking layer (discover -> verify -> synthesize)',
  phases: [
    { title: 'Discover', detail: 'one analyst per adversary category' },
    { title: 'Verify', detail: 'adversarially refute each candidate finding' },
    { title: 'Synthesize', detail: 'dedupe + assign severity + report-ready set' },
  ],
}

const SCOPE = `
IN SCOPE (read these files):
- src/cancelchain/node.py  (Node: send/receive_transaction, send/receive_block,
  process_block, add_block, create_chain, request_block, request_latest_blocks,
  fill_peer, fill_chain, discard_expired_pending_txns)
- src/cancelchain/miller.py (Miller: pending_txns_gen, update_pending_txns,
  pending_chain_txns, create_block, poll_latest_blocks, mill_block)
- src/cancelchain/api.py  (PEER-FACING POST views: BlockView.post, TxnView.post,
  the /process endpoints; and the async path queue_post_process ->
  handle_http_post -> tasks.post_process)
- src/cancelchain/api_client.py (ApiClient gossip: _send, get/post, post_block,
  post_transaction, get_block, timeouts/retries)
- src/cancelchain/tasks.py (post_process Celery task)
- src/cancelchain/models.py: PendingTxnDAO, PendingIOflowDAO, ChainFill,
  ChainFillBlock (staging lifecycle)
- src/cancelchain/transaction.py: PendingTxnSet (mempool set)

TRUSTED BOUNDARIES — do NOT report findings that reduce to these (they are
already audited and closed). Cross-reference only:
- The validate_* verification pipeline (block/chain/transaction VALIDITY).
- The cc-sig-v1 / authorize() auth + role layer (authn/authz).

THE SCOPE RAZOR — classify every candidate:
- "malformed/invalid block or txn is accepted" -> verification audit, NOT here.
- "unauthenticated/under-authorized request honored" -> auth audit, NOT here.
- "valid, authenticated peer input (or a sequence/volume/depth/timing of them)
  makes the node exhaust resources, amplify traffic, loop, wedge, or corrupt
  its orchestration/staging state" -> THIS audit. Assume inputs are
  individually valid and authenticated but HOSTILE in pattern.

OUT OF SCOPE: browser.py / web UI, the CLI, wallet-file handling, PoW soundness
of an individual block, and threats from a malicious operator of THIS node.
`

const ADVERSARIES = [
  { key: 'exhaust', name: 'Resource-exhaustion peer',
    lens: 'Unbounded fill_chain depth (one request_block + one ChainFillBlock row per ancestor, no cap); fill_peer unbounded blocks list + the delay>10 sleep back-off; unbounded request/response payload size (note: grep confirms NO MAX_CONTENT_LENGTH anywhere); unbounded pending_txns mempool admission; ChainFillBlock row-count growth. Trace each: what one cheap peer request costs the node.' },
  { key: 'eclipse', name: 'Eclipse / chain-feeding peer',
    lens: 'Serve a valid-but-fake deep or longer chain to drive reorg/fill cost, pin the node inside fill_chain/fill_peer, or dominate request_latest_blocks / poll_latest_blocks. Focus on orchestration cost and getting-wedged, NOT on whether a block is valid (trusted).' },
  { key: 'loop', name: 'Gossip-loop / amplification abuser',
    lens: 'Spoof, omit, or oversize visited_hosts / the Peer-Hosts header to induce gossip loops, defeat the loop-guard, or amplify fan-out across the peer set. Trace send_transaction/send_block/receive_* and how visited_hosts is built/trusted.' },
  { key: 'framing', name: 'Protocol / framing abuser',
    lens: 'Oversized or slow-streamed bodies, content-type tricks, txid/block_hash mismatch handling, and abuse of the 202 async enqueue path (enqueue without doing work; self-/process recursion; broker mis/unconfigured behavior). NOTE: if an oversized/slow body triggers resource expenditure BEFORE authorize() runs (i.e. unauthenticated request processing), that straddles the auth boundary — cross-reference it to the cc-sig-v1 auth audit rather than claiming it as a new networking finding. A networking finding here is one where an AUTHENTICATED peer drives the cost.' },
  { key: 'race', name: 'Race / concurrency',
    lens: 'Concurrent fill_chain/receive_block against the same chain prefix; ChainFill orphan rows on crash or interleave (cf. observation A5.c); mempool add/discard races. Orchestration/staging corruption only, not consensus.' },
  { key: 'async', name: 'Async post-process path',
    lens: 'The Celery queue_post_process -> worker -> self-/process loop as an amplification, wedging, or unbounded-retry surface; behavior when CELERY_BROKER_URL is unset or the worker repeatedly fails.' },
]

const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'title', 'attack', 'code_path', 'precondition', 'impact', 'severity'],
        properties: {
          id: { type: 'string', description: 'short slug, e.g. "fillchain-unbounded-depth"' },
          title: { type: 'string' },
          attack: { type: 'string', description: 'concrete step-by-step attack' },
          code_path: { type: 'string', description: 'file:line references for the vulnerable path' },
          precondition: { type: 'string', description: 'role/config needed (e.g. any authenticated peer, MILLER, async enabled)' },
          impact: { type: 'string' },
          severity: { type: 'string', enum: ['Critical', 'High', 'Medium', 'Low'] },
          test_sketch: { type: 'string', description: 'how a bounded-observation or correctness xfail test would demonstrate it' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['real', 'reason', 'severity'],
  properties: {
    real: { type: 'boolean', description: 'true only if the impact survives refutation AND is in-scope per the razor' },
    reason: { type: 'string', description: 'why it survives, or what trusted-boundary control / httpx-Flask default / timeout / existing guard kills it' },
    severity: { type: 'string', enum: ['Critical', 'High', 'Medium', 'Low'], description: 'corrected severity if the analyst over/under-rated' },
    out_of_scope: { type: 'boolean', description: 'true if it reduces to a trusted boundary (verification/auth) or an out-of-scope area' },
  },
}

const SYNTH_SCHEMA = {
  type: 'object',
  required: ['headline', 'findings', 'cross_cutting', 'recommendations'],
  properties: {
    headline: { type: 'string', description: 'e.g. "0 Critical / 0 High / 3 Medium / 2 Low"' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'adversary', 'severity', 'title', 'attack', 'code_path', 'impact', 'test_sketch'],
        properties: {
          id: { type: 'string' }, adversary: { type: 'string' }, severity: { type: 'string' },
          title: { type: 'string' }, attack: { type: 'string' }, code_path: { type: 'string' },
          impact: { type: 'string' }, test_sketch: { type: 'string' },
        },
      },
    },
    cross_cutting: { type: 'array', items: { type: 'string' } },
    recommendations: { type: 'array', items: { type: 'string' } },
  },
}

function discoverPrompt(adv) {
  return `You are a security analyst tracing one adversary category against the cancelchain P2P/networking layer. Work in /home/gumptionthomas/Development/cancelchain. Read the in-scope files and trace CONCRETE attacks for your category. Report ONLY real, in-scope candidate findings (the verify phase will refute weak ones; do not pad).

${SCOPE}

YOUR ADVERSARY: ${adv.name}
LENS: ${adv.lens}

For each candidate: give a concrete step-by-step attack, the exact vulnerable code path (file:line), the precondition (role/config), the impact, a proposed severity, and a one-line sketch of how a bounded-observation (availability) or correctness xfail test would demonstrate it. Be specific and skeptical — if a control already bounds it (a timeout, an existing cap, a trusted-boundary check), don't report it.`
}

function verifyPrompt(f) {
  return `You are an adversarial verifier. Try to REFUTE this candidate finding from the cancelchain P2P/networking audit. Work in /home/gumptionthomas/Development/cancelchain; read the cited code.

${SCOPE}

CANDIDATE FINDING:
- id: ${f.id}
- title: ${f.title}
- attack: ${f.attack}
- code_path: ${f.code_path}
- precondition: ${f.precondition}
- impact: ${f.impact}
- proposed severity: ${f.severity}

Refute it. Set real=false if: the impact is already bounded (an httpx/Flask default, a configured timeout, an existing cap or guard, an upstream early-return), OR it reduces to a trusted boundary (verification validity / auth) — set out_of_scope=true in that case — OR the attack does not actually reach the cited code. Set real=true ONLY if a concrete, in-scope, currently-unbounded/unguarded impact survives. Correct the severity if the analyst over- or under-rated it per the rubric (amplification x reachability x persistence). Default to skepticism: if uncertain whether a control exists, READ the code to confirm rather than assuming.`
}

phase('Discover')
const perAdversary = await pipeline(
  ADVERSARIES,
  // No agentType override: the discover analysts inherit the strong
  // main-loop model. (Don't use the Haiku-powered 'Explore' agent here —
  // it's tuned for fast code search, not thorough adversarial tracing with
  // the 7-field-per-finding FINDINGS_SCHEMA, where a structured-output miss
  // would silently drop a whole category's findings.)
  adv => agent(discoverPrompt(adv), { label: `discover:${adv.key}`, phase: 'Discover', schema: FINDINGS_SCHEMA }),
  (found, adv) => parallel(((found && found.findings) || []).map(f => () =>
    agent(verifyPrompt(f), { label: `verify:${adv.key}:${f.id}`, phase: 'Verify', schema: VERDICT_SCHEMA })
      .then(v => ({ ...f, adversary: adv.name, verdict: v }))
  ))
)
const candidates = perAdversary.flat().filter(Boolean)
const confirmed = candidates.filter(f => f.verdict && f.verdict.real && !f.verdict.out_of_scope)
  .map(f => ({ ...f, severity: f.verdict.severity || f.severity }))
log(`discovered ${candidates.length} candidates; ${confirmed.length} survived refutation`)

phase('Synthesize')
const synthesis = await agent(
  `You are the audit lead. Here are the refutation-surviving findings (JSON). Dedupe across adversary categories, finalize severities per the rubric (amplification x reachability x persistence), and produce a report-ready set plus cross-cutting observations and prioritized recommendations. Findings:\n\n${JSON.stringify(confirmed, null, 2)}`,
  { phase: 'Synthesize', schema: SYNTH_SCHEMA }
)
return { confirmed, synthesis }
```

- [ ] **Step 2: Persist the result for the write-up tasks**

From the Workflow's return value, write the `synthesis` object (headline, findings, cross_cutting, recommendations) and the raw `confirmed` array to `/tmp/network-audit-findings.json` (scratch, not committed). This is the single source for Tasks 3 and 4. Sanity-check: every `synthesis.findings[]` entry has a `code_path` that points at an in-scope file, and the `headline` counts match the finding list.

- [ ] **Step 3: Controller gut-check (no commit)**

Read each surviving finding against the scope razor one more time yourself. Drop any that slipped through as a verification/auth restatement. If the fan-out produced **zero** findings, that is a valid outcome — proceed to Task 3 and write a clean report (headline `0/0/0/0`, "no findings") with the adversary traces showing what was checked and why each surface is bounded.

---

## Task 3: Write the audit report

**Files:**
- Create: `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`

- [ ] **Step 1: Write the report from `/tmp/network-audit-findings.json`**

Use this skeleton (same shape as `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`). Fill every `{{...}}` from the synthesized findings; do not leave any placeholder.

```markdown
# P2P / Networking Threat-Modeled Audit

**Date:** 2026-06-01
**Scope:** Node/Miller orchestration + peer-facing API views. Trusted boundaries: verification pipeline + cc-sig-v1 auth. See [design](../specs/2026-06-01-network-p2p-audit-design.md).

## Executive summary

**{{headline, e.g. "0 Critical / 0 High / 3 Medium / 2 Low"}}.** {{1-2 sentence framing: availability-led audit of the node-coordination layer; what the dominant risk class turned out to be}}.

## Findings table

| ID | Adversary | Severity | Description | Status | Demonstration test |
|---|---|---|---|---|---|
{{one row per finding; Status = ⏳ open (xfail) until remediated; test = test_network_audit.py::<name>}}

## Adversary traces

{{For EACH of the six adversary categories: a subsection. If it produced findings, give the attack trace(s) with code_path. If it produced none, state what was checked and why the surface is bounded (the refutation reasons) — a clean category is a result, not a gap.}}

## Cross-cutting observations

{{synthesis.cross_cutting as a numbered list}}

## Recommendations

{{synthesis.recommendations, prioritized; note which findings share a root cause and could be remediated together}}
```

- [ ] **Step 2: Verify internal consistency**

The headline counts MUST equal the findings-table row count by severity. Every findings-table row MUST have a matching demonstration-test name that Task 4 will create. Re-read the scope razor: no row restates a verification/auth finding.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/audits/2026-06-01-network-p2p-audit.md
git commit -m "docs(audit): P2P/networking audit report — <headline>"
```

---

## Task 4: Write the demonstration tests

**Files:**
- Modify: `tests/test_network_audit.py` (append one test per finding)

- [ ] **Step 1: For each finding, append a strict-xfail demonstration**

Use the matching convention. Below are two **worked examples** showing the exact shape — adapt per finding, keep the names identical to the report's findings-table column.

**(a) Availability / bounded-observation example** — demonstrates an unbounded `fill_chain` depth (a hostile peer serves a fake ancestor chain; the node stages every block with no cap). Drives only a small bound (`DEPTH = 5`), asserts no cap rejected it, and is `xfail` until a depth cap lands:

```python
@pytest.mark.xfail(
    strict=True,
    reason='AUDIT <ID>: fill_chain stages an attacker-controlled number of '
    'ancestor blocks with no depth cap (availability). Remove this marker '
    'when a cap rejects/stops the walk at the configured threshold.',
)
def test_<id>_fill_chain_has_no_depth_cap(
    app, host, miller_wallet, requests_proxy, remote_requests_proxy
):
    # A bounded mock peer serves a short fake ancestor chain. The test asserts
    # the node stages ALL of them (no cap). Bound is small and deterministic —
    # it never exhausts real resources. When a cap is added, the staged count
    # is clamped (or the walk raises), flipping this to a passing regression.
    DEPTH = 5
    with app.app_context():
        node = Node(
            host=app.config['NODE_HOST'],
            peers=app.config['PEERS'],
            clients=app.clients,
            logger=app.logger,
        )
        last_block = _make_fake_ancestor_chain(node, depth=DEPTH)  # helper per finding
        node.fill_chain(last_block)
        # No-cap behavior: every requested ancestor got staged.
        assert staged_chain_fill_count(app) >= DEPTH
```

**(b) Correctness example** — demonstrates an orchestration-state bug (e.g. a gossip loop-guard that trusts a spoofable `Peer-Hosts` header). Asserts the buggy behavior under `xfail`; flips to a passing regression when fixed:

```python
@pytest.mark.xfail(
    strict=True,
    reason='AUDIT <ID>: <one-line gap>. Remove this marker when <fix> lands.',
)
def test_<id>_<short_name>(app, host, transactor_wallet, requests_proxy):
    with app.app_context():
        ...  # arrange the hostile-but-valid input
        result = ...  # exercise the in-scope path
        assert <the buggy observable behavior>
```

Each test's helper (e.g. `_make_fake_ancestor_chain`) is defined alongside it in the file. Helpers must use the existing fixtures and `Block`/`Transaction` factories from `conftest.py`; they fabricate *valid-but-hostile* inputs (the validity is assumed/trusted), never malformed ones. **Important for sync-path tests:** `fill_chain`/`fill_peer`/`request_block` make real HTTP calls to peers, so a helper feeding a fake ancestor chain must make those blocks *served by the mock peer through the `requests_proxy`/`remote_requests_proxy` WSGI fixtures* (e.g. persist them in the remote app's DB so its `GET /api/block/<hash>` returns them, or intercept via `requests_mock`) — constructing `Block` objects in memory alone is insufficient, because `request_block` will 404 and the walk aborts before demonstrating the gap.

- [ ] **Step 2: Verify every demonstration FAILS as an xfail (the gap is real)**

```bash
uv run pytest tests/test_network_audit.py -q 2>&1 | tail -5
```
Expected: every new test reports `xfailed` (NOT `xpassed` — an `xpassed` under `strict=True` errors the suite and means the test doesn't actually demonstrate the gap; fix the test so it genuinely exercises the unguarded behavior).

- [ ] **Step 3: Confirm the demonstrations genuinely fail when forced**

```bash
uv run pytest --runxfail tests/test_network_audit.py -q 2>&1 | tail -5
```
Expected: every test FAILS (proving the gap exists). If any PASSES under `--runxfail`, the behavior is actually bounded — that finding is a false positive; remove it from BOTH the report and the test file and note it in the report's "refuted candidates" if useful.

- [ ] **Step 4: Full-suite + lint gates**

```bash
uv run pytest -q 2>&1 | tail -2     # baseline count + N xfailed (one per finding); 0 xpassed; 0 failed
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Run `uv run ruff format src tests` if format asks. Investigate any real failure.

- [ ] **Step 5: Commit**

```bash
git add tests/test_network_audit.py
git commit -m "test(audit): strict-xfail demonstrations for network-p2p findings"
```

---

## Task 5: Roadmap entry + open the docs PR

**Files:**
- Modify: `docs/superpowers/ROADMAP.md`

- [ ] **Step 1: Add the tracking entry**

In `docs/superpowers/ROADMAP.md`, add a new section under the audit-remediation area (above the "Closed items" block), mirroring the existing "Audit remediation — verification pipeline findings" / "API authentication findings" entries:

```markdown
## Audit remediation — P2P/networking findings

The 2026-06-01 [P2P/networking audit](audits/2026-06-01-network-p2p-audit.md)
produced **{{headline}}**. Each finding has a `@pytest.mark.xfail(strict=True)`
demonstration in `tests/test_network_audit.py`; remediation flips it to a
passing regression. Open items:

{{one bullet per finding: - ⏳ **<ID> (<severity>) — <title>** — <one-line>. Test: `<test name>`.}}

Originating report:
- [P2P/networking audit — Findings table + Recommendations](audits/2026-06-01-network-p2p-audit.md)
```

If the audit found nothing, instead add a one-line closed entry: `✅ P2P/networking audit — 0/0/0/0, no findings (PR #<n>)`.

- [ ] **Step 2: Commit, push, open the PR**

```bash
git add docs/superpowers/ROADMAP.md
git commit -m "docs(audit): track network-p2p audit findings in roadmap"
uv run pytest -q 2>&1 | tail -2     # final gate
git push -u origin audit/network-p2p
gh pr create --base main --title "docs(audit): P2P/networking threat-model audit — <headline>" --body "<summary: scope, headline, per-adversary coverage, demonstration tests, link to design spec>"
```

- [ ] **Step 3: Internal review, then the single Copilot backstop**

Per the project's review model: run the internal cross-model review loop on the report+tests (a different-model reviewer checking the scope razor was honored, the demonstrations genuinely fail under `--runxfail`, severities are defensible, and no finding restates a trusted-boundary audit) to convergence BEFORE leaning on Copilot. Then address Copilot's single backstop on the PR. `mwg` once green.

---

## Self-review checklist (controller, before execution)

- **Spec coverage:** scope/trust-boundaries → Task 2 SCOPE razor; six adversary categories → Task 2 ADVERSARIES; methodology (discover→verify→synthesize) → Task 2 Workflow; report format → Task 3 skeleton; strict-xfail + bounded-observation conventions → Task 4 worked examples; close-out/roadmap → Task 5. ✓
- **No real resource exhaustion:** Task 4 bounds every availability demonstration (`DEPTH = 5`, single modest body, handful of txns). ✓
- **Findings-dependent, not placeholder:** the per-finding tests/report rows are necessarily parameterized by the audit's output (inherent to an audit); their SHAPE is fully specified by the skeleton + two worked examples, and every deterministic artifact (Workflow script, report skeleton, gate commands) is complete. ✓
- **Zero-findings path:** explicitly handled in Task 2 Step 3, Task 3 Step 1, Task 5 Step 1. ✓
