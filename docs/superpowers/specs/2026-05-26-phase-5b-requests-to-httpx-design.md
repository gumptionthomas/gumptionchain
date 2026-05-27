# Phase 5b — `requests` → `httpx`

**Status:** Draft for review
**Date:** 2026-05-26
**Scope:** Replace `requests` with `httpx` across `src/cancelchain/` and `tests/`. After this phase: no `import requests` anywhere in the codebase, `requests` is no longer in `[project.dependencies]`, and `requests-mock` is no longer in `[dependency-groups].dev`. `httpx>=0.28` is added to `[project.dependencies]`.

## Goal

Modernize the HTTP client onto `httpx` — the de facto modern Python HTTP library, with first-class type stubs, native test-transport facilities, and a maintained core. Drops `requests` (and its testing dependency `requests-mock`), aligning Phase 5b with the same supply-chain hygiene motivation as Phase 5a's pycryptodome swap.

**Greenfield posture.** No legacy peer endpoints exist that need to round-trip across this swap. No on-wire compat constraint — Python-side library swap only, HTTP/1.1 + JSON over the same paths.

## Non-goals

- **No async path.** All HTTP consumers today are sync (Flask views via `Node` / `Miller`, the Celery `post_process` task, and the CLI). Introducing `httpx.AsyncClient` would force event-loop plumbing through Flask and Celery for no measurable benefit. Defer indefinitely until a concrete async caller exists.
- **No HTTP/2.** `httpx[http2]` is opt-in (adds `h2` to the dep tree). Peer endpoints are plain Flask — no upstream support to leverage.
- **No retry/backoff middleware.** The current code does one inline 401-retry loop in `ApiClient.get` / `post` (to refresh an expired JWT) and otherwise lets `RequestException` propagate. Stay behavior-equivalent — keep the 401 loop, no other retries.
- **No event hooks / structured-logging refactor.** Logging stays exactly where it is today.
- **No change to peer URL or `Peer-Hosts` header semantics.** Wire format identical to today.
- **No `urllib.parse.urljoin` rewrite.** httpx supports `base_url` on the client natively, but the current `urljoin(self.host, path)` semantics work fine and tests bind to them. Adopt `base_url` where it falls out naturally inside `ApiClient` (since the client is per-host); keep `urljoin` everywhere it's used outside the client (none, currently).
- **No removal of `command.py`'s `http_error_message` shape.** The function keeps returning `str | None` — just rebuilt against `httpx.HTTPStatusError`.
- **No `ApiClient` API redesign.** All public methods keep their signatures, return types stay `Response`-like (`httpx.Response` in place of `requests.Response`) — callers only touch `.status_code`, `.json()`, `.raise_for_status()`, `.text`, `.headers`, which are identical across the two libraries.
- **No `Wallet`-level or DAO-level changes.** This is a transport-layer swap.

## Decisions taken during brainstorming

- **Sync-only.** Every caller is sync. `httpx.Client` everywhere; no `AsyncClient`.
- **Test transport = `httpx.WSGITransport(app=...)`.** Drop `requests-mock` from dev deps entirely. The existing `requests_proxy` / `remote_requests_proxy` fixtures (which do URL pattern matching with `requests_mock` and forward to `app.test_client()`) get rewritten to back onto `httpx.WSGITransport(app=...)`. Fixture *names* are preserved (the ~25 test signatures that consume them by name stay unchanged); only the fixture *mechanism* changes. Each fixture now yields an `httpx.Client(transport=WSGITransport(app=...), base_url=host)` — previously yielded `None`. This is a real WSGI bridge — closer to production than a URL-matching mocker.
- **`ApiClient` holds a persistent `httpx.Client`.** Instantiated in `__init__` via a module-scope `_make_client(base_url, timeout)` factory (the test seam — see below), reused for all GET/POST. Connection pooling matters because `app.clients` is built once at app boot (`application.py::create_clients`) and reused across the entire app lifetime. Add `close()` and `__enter__` / `__exit__` so the CLI's `host_api_client` consumer can use `with`. App-lifetime clients (the ones in `app.clients`) rely on process exit + httpx's own cleanup — no `teardown_appcontext` plumbing in this PR (Phase 6 territory).
- **Return type stays `httpx.Response`.** Update annotations and callers; no wrapper class.
- **Status code constants migrate to `httpx.codes`.** `requests.codes.ok` → `httpx.codes.OK`, `requests.codes.unauthorized` → `httpx.codes.UNAUTHORIZED`. The module-level `OK` / `UNAUTHORIZED` constants in `api_client.py` stay (just sourced from httpx).
- **Exception mapping:**
  | requests | httpx |
  |---|---|
  | `requests.RequestException` (base of all `requests` exceptions, **including** `HTTPError`) | `httpx.HTTPError` (base of all `httpx` exceptions, **including** `HTTPStatusError`) |
  | `requests.ConnectionError` / `requests.Timeout` (network/transport only) | `httpx.RequestError` (network/transport only — sibling of `HTTPStatusError`, not its parent) |
  | `requests.HTTPError` (4xx/5xx) | `httpx.HTTPStatusError` (raised by `Response.raise_for_status()`) |
  | `requests.exceptions.JSONDecodeError` | `json.JSONDecodeError` (httpx delegates `.json()` to stdlib) |

  Naming-only mapping is a trap: `RequestException` is *not* equivalent to `RequestError` despite the similar names. `RequestException` was the broad catch-everything base; `RequestError` is narrower (network only). The correct broad-base counterpart in httpx is `HTTPError`.
- **`tasks.py` Celery task** uses a one-shot module-level `httpx.post(url, ..., timeout=360)`. The task fires once per invocation and exits — no persistent client warranted.
- **Tests that today call `requests.get(host, ...)` / `requests.post(...)` directly** (three tests in `test_api.py`: `test_post_token_none`, `test_post_token_invalid`, `test_no_auth`) become `requests_proxy.get(path, ...)` / `requests_proxy.post(path, ...)` against the fixture-provided client. Drops the need to build a full URL with `urljoin(host, ...)` in tests — `base_url` on the client handles it. `test_browser.py` only references `requests.codes.*` (no direct calls — it uses Flask's `test_client` already).
- **Greenfield + supply-chain motivation.** Same posture as Phase 5a — no migration tool, no compat shims, no shipped peer endpoints to coordinate with.

## Changes

### Files

**Source (`src/cancelchain/`)**
- Modify: `src/cancelchain/api_client.py` — full rewrite of HTTP layer; adopt persistent `httpx.Client`. ~315 lines, edit-in-place. All 8 GET/POST call sites updated.
- Modify: `src/cancelchain/node.py` — `import requests` → `import httpx`; 4 `except requests.RequestException` → `except httpx.HTTPError` (broad-base catch that includes both network errors *and* 4xx/5xx, matching the prior `RequestException` semantics).
- Modify: `src/cancelchain/miller.py` — same swap, 1 catch site.
- Modify: `src/cancelchain/command.py` — `import requests` → `import httpx`; 7 `except requests.HTTPError` → `except httpx.HTTPStatusError`; `requests.exceptions.JSONDecodeError` → `json.JSONDecodeError` (add `import json`); `http_error_message` signature.
- Modify: `src/cancelchain/tasks.py` — `import requests` → `import httpx`; one-shot `httpx.post(...)`.

**Tests (`tests/`)**
- Modify: `tests/conftest.py` — rewrite `requests_proxy` / `remote_requests_proxy` to back onto `httpx.WSGITransport(app=...)` (fixture names preserved; mechanism changed). Both fixtures yield an `httpx.Client(transport=WSGITransport(app=...), base_url=host)` so tests that today call HTTP directly can use `.get(path)` / `.post(path, ...)`. Drop the `requests_mock` import.
- Modify: `tests/test_api_client.py` — `requests.exceptions.HTTPError` → `httpx.HTTPStatusError`, `requests.codes.ok` → `httpx.codes.OK`. No fixture-name change (`requests_proxy` is preserved as the fixture name).
- Modify: `tests/test_api.py` — same exception + codes renames; replace direct `requests.get/post(host, …)` calls (three tests: `test_post_token_none`, `test_post_token_invalid`, `test_no_auth`) with `requests_proxy.get/post(path, ...)` against the fixture-yielded client. No fixture-name change.
- Modify: `tests/test_browser.py` — replace `requests.codes.*` with `httpx.codes.*`. This file doesn't consume the proxy fixtures.
- No change: `tests/test_command.py` — consumes `requests_proxy` by name only; the name is preserved so no edits are needed.
- Add: 2 new tests in `tests/test_api_client.py` for `ApiClient` lifecycle — context-manager close, and connection reuse across two GETs (`assert client._client is unchanged across calls`).

**Config**
- Modify: `pyproject.toml` — drop `requests>=2.32` from `[project.dependencies]`; drop `requests-mock>=1.12` from `[dependency-groups].dev`; add `httpx>=0.28` to `[project.dependencies]`.
- Modify: `uv.lock` — regenerated via `uv lock`.
- No change expected to `[tool.mypy.overrides]` (httpx ships first-party stubs).

### New `ApiClient` shape

```python
def _make_client(base_url: str, timeout: float) -> httpx.Client:
    """Module-scope factory so tests can monkeypatch a single seam to
    inject httpx.WSGITransport(app=flask_app). Production callers never
    touch this directly.
    """
    return httpx.Client(base_url=base_url, timeout=timeout)


class ApiClient:
    def __init__(
        self,
        host: str,
        wallet: Wallet,
        timeout: int | float | None = None,
    ) -> None:
        host, address = host_address(host)
        if address and address != wallet.address:
            raise ValueError(ADDRESS_MISMATCH_MSG)
        self.host = host
        self.wallet = wallet
        self.token: str | None = None
        self.timeout: int | float = timeout if timeout is not None else 10
        self._client = _make_client(self.host, self.timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> 'ApiClient':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
```

GET / POST helpers call `self._client.get(path, ...)` / `self._client.post(path, ...)` — paths stay absolute (`/api/...`), `base_url` handles host. The `request_token` flow uses the same client.

### Fixture rewrite (illustrative)

```python
import httpx
from unittest.mock import patch

@pytest.fixture
def requests_proxy(app, host):
    """WSGITransport-backed httpx client. Name preserved for the ~25
    existing test signatures; only the mechanism changes.
    """
    transport = httpx.WSGITransport(app=app)

    def _wsgi_make_client(base_url, timeout):
        return httpx.Client(
            transport=transport, base_url=base_url, timeout=timeout
        )

    from cancelchain.application import create_clients
    with patch(
        'cancelchain.api_client._make_client',
        side_effect=_wsgi_make_client,
    ):
        # Rebuild app.clients under the active patch so every ApiClient
        # in scope (including app-boot-time clients) routes WSGI.
        for c in list(app.clients.values()):
            c.close()
        app.clients = create_clients(app)
        with httpx.Client(transport=transport, base_url=host) as client:
            yield client


@pytest.fixture
def remote_requests_proxy(remote_app, remote_host):
    """Counterpart for the second Flask app used in peer-gossip tests."""
    transport = httpx.WSGITransport(app=remote_app)

    def _wsgi_make_client(base_url, timeout):
        return httpx.Client(
            transport=transport, base_url=base_url, timeout=timeout
        )

    from cancelchain.application import create_clients
    with patch(
        'cancelchain.api_client._make_client',
        side_effect=_wsgi_make_client,
    ):
        for c in list(remote_app.clients.values()):
            c.close()
        remote_app.clients = create_clients(remote_app)
        with httpx.Client(
            transport=transport, base_url=remote_host
        ) as client:
            yield client
```

The fixture *both* (a) makes outbound HTTP from `ApiClient` resolve to the Flask app (since `app.clients[peer]` was built with the same host string), and (b) gives the test a client it can use directly. To wire (a): `ApiClient` opens its own `httpx.Client` with a real transport — but in tests, the WSGITransport binding has to be applied to *that* client too. Two options:

1. **Add a `_make_client(base_url, timeout)` factory function at module scope in `api_client.py`** and have `ApiClient.__init__` call it. Tests monkeypatch the factory to return a WSGITransport-backed `httpx.Client`. Single seam; zero test-call-site churn; production surface unchanged.
2. **Add a `transport` kwarg to `ApiClient.__init__`** (optional, default `None`). Tests pass `transport=WSGITransport(app=app)`; production passes nothing. Explicit, but every inline `ApiClient(host, wallet)` call in tests (~30 sites across `test_api.py` and `test_api_client.py`) grows a `transport=` kwarg, and each containing test function grows a fixture parameter.

**Decision: option 1.** The `_make_client` factory is a named, importable, deliberately test-facing seam — not a side-effect monkeypatch on private internals. It produces zero test-call-site churn (the ~30 inline `ApiClient(...)` sites stay unchanged), and the production surface is *also* cleaner than option 2 since `create_clients` doesn't need to thread a `transport` kwarg through.

The conftest fixture then becomes responsible for two things:
- Yielding an `httpx.Client` for direct test use (the same shape as today's per-test direct HTTP calls).
- Activating the `_make_client` monkeypatch so peer-gossip code in `Node` / `Miller` routes through WSGITransport-backed `ApiClient`s. Existing `app.clients` entries (built at app init before the patch was active) get rebuilt inside the fixture by re-calling `create_clients(app)` under the active patch — so every `ApiClient` in scope during a test is WSGI-bound.

### Test plan

- Replace `requests_proxy` / `remote_requests_proxy` with WSGI-transport-backed `httpx.Client` fixtures.
- ~30 existing tests that consume the fixture get a rename + a few `requests.codes.*` → `httpx.codes.*` + `requests.exceptions.HTTPError` → `httpx.HTTPStatusError` substitutions. No behavior change expected.
- Add 2 new `ApiClient` lifecycle tests: context-manager `close()`, and connection reuse across two GETs (asserts `client._client` is unchanged between calls).
- Test count: 214 (post-5a) → 216 (+2 new lifecycle tests).
- Verify `uv run pytest` is green; verify `uv run pytest --runmulti` is green; verify `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy` are all clean.

## Acceptance

- `grep -rn 'import requests\|from requests' src/cancelchain/ tests/` returns nothing.
- `grep -i '^requests\b' pyproject.toml` and `grep -i '^requests-mock\b' pyproject.toml` return nothing (dependency removal).
- `grep -i 'name = "requests"\|name = "requests-mock"' uv.lock` returns nothing.
- `uv run python -c "import requests"` raises `ModuleNotFoundError`.
- `uv run python -c "import httpx; print(httpx.__version__)"` succeeds.
- `uv run mypy` exits 0.
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run pytest` exits 0; test count grows by 2 (214 → 216).
- `uv run pytest --runmulti` exits 0.
- `uv run cancelchain --help` works.
- `docker build --target builder -t cc-phase5b .` succeeds.

## Risks

- **WSGITransport behavioral parity vs `requests-mock`.** WSGITransport actually invokes the Flask WSGI app stack (so middleware, error handlers, and the full request pipeline run); `requests-mock` short-circuits at the URL pattern level and the current fixture re-invokes the test client. The current `requests_proxy` already runs the full Flask test-client path, so behavior should match — but watch for tests that depend on `requests_mock`-specific metadata (e.g., `requests_mock.last_request.qs`). `grep -rn 'requests_mock\.' tests/` should return nothing once the migration is complete; any hit is a tell.
- **`base_url` join semantics.** httpx's `base_url` + absolute path uses RFC 3986 reference resolution: an absolute path (`/api/...`) replaces the path component of `base_url` cleanly. All paths in `api_client.py` are absolute, so behavior should match `urljoin(host, '/api/...')`. Verify by running the full test suite, especially `test_api_client.py::test_host_address` which exercises edge URLs.
- **`Connection: close` behavior in tests.** WSGITransport synthesizes per-request environs and doesn't pool. A test that calls `requests_proxy.get(...)` twice in a row should not depend on a single underlying socket — and doesn't today (HTTP semantics are stateless).
- **Connection pool teardown in production.** App-lifetime clients in `app.clients` close on process exit. If the test runner ever runs the production app code path without using the fixture override, lingering sockets could theoretically leak — but pytest's fixture teardown chain handles this by closing the patched-in clients. No production server-side change.
- **JSON decode error in `command.py::http_error_message`.** Today catches `requests.exceptions.JSONDecodeError`. httpx delegates `Response.json()` to stdlib, so the new catch is `json.JSONDecodeError`. Add `import json` to `command.py`. `json.JSONDecodeError` is a `ValueError` subclass — the broader `ValueError` catch is **not** acceptable because it would mask other failures; the narrow catch stays explicit.
- **Streaming responses.** Not used today; httpx requires explicit `client.stream(...)` for streaming, but no caller streams. No risk unless someone adds streaming later (out of scope).

## Open decisions

None at design time. The brainstorming round resolved:
- Sync vs async → sync-only.
- Test transport → `httpx.WSGITransport` (no third-party mocker).
- ApiClient lifecycle → per-instance persistent `httpx.Client`, app-lifetime in production / context-managed in CLI.
- Test wiring → monkeypatch a single `_make_client(base_url, timeout)` factory at module scope in `api_client.py`. Tests don't change `ApiClient` construction; the seam handles WSGI binding transparently.

## Translation reference (quick lookup for the implementer)

| Concept | requests | httpx |
|---|---|---|
| Module import | `import requests` | `import httpx` |
| GET / POST (module-level) | `requests.get(url, ...)` / `requests.post(url, ...)` | `httpx.get(url, ...)` / `httpx.post(url, ...)` |
| Persistent client | `requests.Session()` | `httpx.Client()` |
| Response type | `requests.Response` | `httpx.Response` |
| Raise on 4xx/5xx | `r.raise_for_status()` | `r.raise_for_status()` |
| Status code constants | `requests.codes.ok` | `httpx.codes.OK` |
| Status code constants | `requests.codes.unauthorized` | `httpx.codes.UNAUTHORIZED` |
| Catch-all base (network + status errors) | `requests.RequestException` | `httpx.HTTPError` |
| Network/transport only | `requests.ConnectionError` / `requests.Timeout` | `httpx.RequestError` |
| 4xx/5xx error | `requests.HTTPError` | `httpx.HTTPStatusError` |
| JSON decode error | `requests.exceptions.JSONDecodeError` | `json.JSONDecodeError` |
| Test mocker | `requests_mock` | `httpx.WSGITransport(app=app)` (no third-party dep) |
| Timeout | `timeout=10` (kwarg per call) | `timeout=10` (per call) or `Client(timeout=10)` |
| Base URL | n/a (use `urljoin`) | `Client(base_url='http://host')` |
| Context manager | `with requests.Session() as s:` | `with httpx.Client() as c:` |
| WSGI direct | n/a | `httpx.Client(transport=WSGITransport(app=flask_app))` |

## What comes next (Phase 6+)

- **Phase 6 — query modernization.** Migrate `Model.query` / `db.session.query(...)` to `db.session.execute(db.select(...))` per the SQLAlchemy 2.0 idiom.
- **Untyped `Wallet.key` attributes.** Tighten to `rsa.RSAPrivateKey` / `rsa.RSAPublicKey` (deferred from Phase 5a).
- **Async peer gossip.** Only if a concrete performance case emerges; not currently warranted.
