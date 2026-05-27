# Phase 5b — `requests` → `httpx` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `requests` with `httpx` across `src/cancelchain/` and `tests/`. After this plan completes, no `import requests` remains anywhere in the codebase, `requests-mock` is removed from `[dependency-groups].dev`, `requests` is removed from `[project.dependencies]`, and `httpx>=0.28` is added.

**Architecture:** Transport-layer swap. Six source files touch the wire (`api_client.py`, `node.py`, `miller.py`, `command.py`, `tasks.py`, plus `application.py` indirectly through `ApiClient` construction); five test files consume the fixture surface (`conftest.py`, `test_api.py`, `test_api_client.py`, `test_browser.py`, `test_command.py`). `ApiClient` adopts a persistent `httpx.Client` built through a module-scope `_make_client(base_url, timeout)` factory — the single seam tests monkeypatch to inject `httpx.WSGITransport(app=flask_app)`. The `requests_proxy` / `remote_requests_proxy` fixtures are rewritten to back onto `httpx.WSGITransport(app=...)` (no third-party mocker); fixture *names* are preserved so the ~25 test signatures consuming them stay unchanged. Greenfield posture: no on-wire compat, no migration, no shims.

**Tech Stack:** `httpx>=0.28` (modern Python HTTP client, first-party stubs, `WSGITransport` for in-process test wiring), `json.JSONDecodeError` (stdlib, replaces `requests.exceptions.JSONDecodeError`), `httpx.codes` (status code enum, replaces `requests.codes`).

---

## Prerequisites

- Working directory: the cancelchain repo root (whatever path it lives at). Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 5a fully merged. Verify with `gh pr view 59 --json state --jq .state` → `MERGED`, and `grep -c 'pycryptodome' pyproject.toml` → `0`.
- The branch `main` is clean. This plan creates a new `docs/phase-5b-design` branch and a separate `feat/httpx-swap` branch for the impl PR.
- CI hard-gates `ruff check`, `ruff format --check`, and `mypy` (strict via `[tool.mypy] strict = true` in pyproject.toml; no CLI flag needed — `uv run mypy` honors the config).
- Test baseline: **214 passed, 1 skipped** (post-5a). Phase 5b adds 2 new tests, so the final count is 216 passed, 1 skipped.
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/specs/2026-05-26-phase-5b-requests-to-httpx-design.md`, `docs/superpowers/plans/2026-05-26-phase-5b-requests-to-httpx.md` (both currently uncommitted in working tree) |
| 2 | impl PR | `pyproject.toml`, `uv.lock`, `src/cancelchain/api_client.py`, `src/cancelchain/node.py`, `src/cancelchain/miller.py`, `src/cancelchain/command.py`, `src/cancelchain/tasks.py`, `tests/conftest.py`, `tests/test_api.py`, `tests/test_api_client.py`, `tests/test_browser.py`, `tests/test_command.py` |
| 3 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** Both the design spec and this implementation plan are present in the working tree but not yet on a branch. This task creates the branch, commits both, pushes, and opens the PR.

- [ ] **Step 1: Confirm clean main and unstaged docs**

```bash
git rev-parse --abbrev-ref HEAD
git status docs/superpowers/
git log --oneline -1
```

Expected: branch is `main`; both new files (`2026-05-26-phase-5b-requests-to-httpx-design.md` and `2026-05-26-phase-5b-requests-to-httpx.md`) show as untracked; top commit is the Phase 5a impl squash.

- [ ] **Step 2: Create the docs branch**

```bash
git checkout -b docs/phase-5b-design
```

- [ ] **Step 3: Stage and commit the spec + plan together**

```bash
git add docs/superpowers/specs/2026-05-26-phase-5b-requests-to-httpx-design.md docs/superpowers/plans/2026-05-26-phase-5b-requests-to-httpx.md
git commit -m "$(cat <<'EOF'
docs(phase-5b): add Phase 5b requests → httpx design + plan

Spec scopes the transport-layer swap: ApiClient adopts a persistent
httpx.Client built through a _make_client(base_url, timeout) factory
seam (tests monkeypatch the factory to inject WSGITransport). The
requests_proxy / remote_requests_proxy fixtures are rewritten to use
WSGITransport; names preserved so test signatures stay unchanged.
requests-mock is dropped from dev deps. Sync-only; greenfield posture
(no on-wire compat).

Plan covers the docs PR, the single impl PR (pyproject + lock + 6 source
files + 5 test files + 2 new lifecycle tests), and post-merge acceptance.
Test count grows 214 → 216.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/phase-5b-design
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/phase-5b-design --title "docs(phase-5b): Phase 5b requests → httpx design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 5b design spec (\`docs/superpowers/specs/2026-05-26-phase-5b-requests-to-httpx-design.md\`).
- Adds the Phase 5b implementation plan (\`docs/superpowers/plans/2026-05-26-phase-5b-requests-to-httpx.md\`).
- No code changes.

Phase 5b ships as a single implementation PR after this docs PR lands. Transport-layer swap across \`src/cancelchain/\` and \`tests/\`. \`requests-mock\` dropped from dev deps in favor of httpx's native \`WSGITransport\`. Greenfield posture — no backward-compat shims.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Phase 5b impl — swap `requests` to `httpx`

**Files:**
- Modify: `pyproject.toml` (drop `requests`, drop `requests-mock`, add `httpx`)
- Modify: `uv.lock` (regenerated)
- Modify: `src/cancelchain/api_client.py` (full HTTP-layer rewrite + `_make_client` seam)
- Modify: `src/cancelchain/node.py` (exception type rename, 4 catch sites)
- Modify: `src/cancelchain/miller.py` (exception type rename, 1 catch site)
- Modify: `src/cancelchain/command.py` (exception type rename, 7 catch sites; JSON-decode error swap)
- Modify: `src/cancelchain/tasks.py` (`requests.post` → `httpx.post`)
- Modify: `tests/conftest.py` (fixture rewrite)
- Modify: `tests/test_api.py` (exception + codes + direct call rewrite)
- Modify: `tests/test_api_client.py` (exception + codes + 2 new lifecycle tests)
- Modify: `tests/test_browser.py` (codes rename)
- Modify: `tests/test_command.py` (fixture name rename only)

### Step 1: Branch off main

```bash
git checkout main && git pull --ff-only
git checkout -b feat/httpx-swap
```

### Step 2: Update `pyproject.toml` dependencies

Edit `pyproject.toml`. In `[project] dependencies`, remove `"requests>=2.32",` and add `"httpx>=0.28",` in alphabetical position. Case-insensitive alphabetical sort puts `httpx` between `gunicorn` and `humanfriendly`.

Before (around line 39–48):
```toml
  "gunicorn>=23",
  "humanfriendly>=10.0",
  "millify>=0.1.1",
  "pg8000>=1.31",
  "pydantic>=2.10",
  "pyjwt>=2.9",
  "pymerkle>=5",
  "python-dotenv>=1.0",
  "requests>=2.32",
  "rich>=13.7",
```

After:
```toml
  "gunicorn>=23",
  "httpx>=0.28",
  "humanfriendly>=10.0",
  "millify>=0.1.1",
  "pg8000>=1.31",
  "pydantic>=2.10",
  "pyjwt>=2.9",
  "pymerkle>=5",
  "python-dotenv>=1.0",
  "rich>=13.7",
```

In `[dependency-groups].dev`, remove `"requests-mock>=1.12",`:

Before:
```toml
[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-cov>=5.0",
  "pytest-dotenv>=0.5",
  "requests-mock>=1.12",
  "time-machine>=2.14",
  ...
```

After:
```toml
[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-cov>=5.0",
  "pytest-dotenv>=0.5",
  "time-machine>=2.14",
  ...
```

No change to `[[tool.mypy.overrides]]` (httpx ships first-party stubs and is already mypy-clean; `requests` had no override either).

### Step 3: Lock and install

```bash
uv lock --upgrade-package requests --upgrade-package requests-mock --upgrade-package httpx
uv sync --group dev
uv run python -c "from importlib.metadata import version; print('httpx', version('httpx'))"
uv run python -c "import requests" 2>&1 | head -3
uv run python -c "import requests_mock" 2>&1 | head -3
```

Expected:
- `httpx 0.28.x` or newer.
- `ModuleNotFoundError: No module named 'requests'`.
- `ModuleNotFoundError: No module named 'requests_mock'`.

If `uv lock` keeps `requests` or `requests-mock` in the lockfile (because some transitive dep pulls them in), STOP and investigate. Nothing else in this codebase should reach for them.

### Step 4: Rewrite `src/cancelchain/api_client.py`

Replace the entire contents of `src/cancelchain/api_client.py` with:

```python
from __future__ import annotations

import datetime
import json
from types import TracebackType
from typing import Self
from urllib.parse import urljoin

import httpx

from cancelchain.block import Block
from cancelchain.transaction import Transaction
from cancelchain.util import dt_2_ciso, host_address
from cancelchain.wallet import Wallet

OK = httpx.codes.OK
UNAUTHORIZED = httpx.codes.UNAUTHORIZED
PEER_HOST_HEADER = 'Peer-Hosts'
ADDRESS_MISMATCH_MSG = 'Address/wallet mismatch'


def _make_client(base_url: str, timeout: float) -> httpx.Client:
    """Module-scope factory so tests can monkeypatch a single seam to
    inject httpx.WSGITransport(app=flask_app). Production callers never
    touch this directly.
    """
    return httpx.Client(base_url=base_url, timeout=timeout)


def json_header(headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = headers or {}
    headers['Content-Type'] = 'application/json'
    return headers


def peer_header(
    visited_hosts: list[str] | None,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = headers or {}
    if visited_hosts:
        headers[PEER_HOST_HEADER] = ','.join(visited_hosts)
    return headers


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
        self._client = _make_client(self.host, float(self.timeout))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def request_token(self, rfs: bool = True) -> str | None:  # noqa: FBT001
        r = self._client.get(
            f'/api/token/{self.wallet.address}', timeout=self.timeout
        )
        if rfs:
            r.raise_for_status()
        if r.status_code == OK:
            secret = self.wallet.decrypt(r.json().get('cipher')).decode()
            r = self._client.post(
                f'/api/token/{self.wallet.address}',
                headers=json_header(),
                content=json.dumps({'challenge': secret}),
                timeout=self.timeout,
            )
            if rfs:
                r.raise_for_status()
            if r.status_code == OK:
                token: str | None = r.json().get('token')
                return token
        return None

    def get_token(self, rfs: bool = True) -> str | None:  # noqa: FBT001
        if self.token is None:
            self.token = self.request_token(rfs=rfs)
        return self.token

    def reset_token(self) -> None:
        self.token = None

    def auth_header(
        self,
        headers: dict[str, str] | None = None,
        rfs: bool = True,  # noqa: FBT001
    ) -> dict[str, str]:
        headers = headers or {}
        token = self.get_token(rfs=rfs)
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        timeout_v: int | float = self.timeout if timeout is None else timeout
        r: httpx.Response
        for _i in range(2):
            headers = self.auth_header(headers=headers, rfs=raise_for_status)
            r = self._client.get(
                path,
                headers=headers,
                params=params,
                timeout=timeout_v,
            )
            if r.status_code == UNAUTHORIZED:
                self.reset_token()
            else:
                break
        if raise_for_status:
            r.raise_for_status()
        return r

    def post(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        data: str | bytes | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        timeout_v: int | float = self.timeout if timeout is None else timeout
        r: httpx.Response
        for _i in range(2):
            headers = self.auth_header(headers=headers, rfs=raise_for_status)
            r = self._client.post(
                path,
                headers=headers,
                content=data,
                timeout=timeout_v,
            )
            if r.status_code == UNAUTHORIZED:
                self.reset_token()
            else:
                break
        if raise_for_status:
            r.raise_for_status()
        return r

    def get_transfer_transaction(
        self,
        public_key: str,
        amount: int,
        address: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/transfer',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'address': address,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/subject',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_forgive_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/forgive',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_support_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/support',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post_transaction(
        self,
        txn: Transaction,
        visited_hosts: list[str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        headers = peer_header(visited_hosts, headers=json_header())
        return self.post(
            f'/api/transaction/{txn.txid}',
            data=txn.to_json(),
            headers=headers,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_pending_transactions(
        self,
        earliest: datetime.datetime | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        params: dict[str, str] | None = None
        if earliest is not None:
            params = {'earliest': dt_2_ciso(earliest)}
        return self.get(
            '/api/transaction/pending',
            params=params,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_block(
        self,
        block_hash: str | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/block/{block_hash}' if block_hash else '/api/block',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post_block(
        self,
        block: Block,
        visited_hosts: list[str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        headers = peer_header(visited_hosts, headers=json_header())
        return self.post(
            f'/api/block/{block.block_hash}',
            data=block.to_json(),
            headers=headers,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_wallet_balance(
        self,
        address: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/wallet/{address}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_balance(
        self,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/subject/{subject}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_support(
        self,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/subject/{subject}/support',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
```

Key behavioral notes (verify against the rewrite before committing):
- `OK` / `UNAUTHORIZED` constants now resolve to `httpx.codes.OK` / `httpx.codes.UNAUTHORIZED` (still integers `200` / `401` — `httpx.codes` is an `IntEnum`).
- `_client.post(..., content=data)` replaces `requests.post(..., data=data)` — in `httpx`, `data=` is for form fields, `content=` is for raw bytes/str (which is what the JSON-string payload here is).
- Paths passed to `self._client.get/post(...)` are absolute (`/api/...`); the client's `base_url` joins them under RFC 3986 semantics — identical to `urljoin(self.host, path)`.
- `urljoin` is dropped from imports (the client's `base_url` handles all joins inside `ApiClient`). If `urljoin` is referenced anywhere else inside `api_client.py`, leave the import in place — `grep -n urljoin src/cancelchain/api_client.py` after the rewrite should return zero results; if not, remove only the unused references.
- The `request_token` method still uses absolute `/api/token/...` paths; no change there.

### Step 5: Verify the `api_client.py` swap is clean

```bash
grep -n 'requests' src/cancelchain/api_client.py
```

Expected: empty.

### Step 6: Update `src/cancelchain/node.py`

Replace `import requests` with `import httpx`, and replace all 4 `except requests.RequestException as re:` lines with `except httpx.RequestError as re:`.

```bash
sed -i 's/^import requests$/import httpx/' src/cancelchain/node.py
sed -i 's/except requests.RequestException as re:/except httpx.RequestError as re:/g' src/cancelchain/node.py
grep -n 'requests' src/cancelchain/node.py
```

Expected: empty after grep. If `grep` shows any remaining `requests` references, inspect manually — there should be none.

### Step 7: Update `src/cancelchain/miller.py`

Same swap, 1 catch site.

```bash
sed -i 's/^import requests$/import httpx/' src/cancelchain/miller.py
sed -i 's/except requests.RequestException as re:/except httpx.RequestError as re:/g' src/cancelchain/miller.py
grep -n 'requests' src/cancelchain/miller.py
```

Expected: empty.

### Step 8: Update `src/cancelchain/tasks.py`

Replace `import requests` with `import httpx` and the single `requests.post(...)` call with `httpx.post(...)`.

Before:
```python
import requests
...

@celery.task()
def post_process(
    url: str, data: str | bytes | None, headers: dict[str, str] | None = None
) -> None:
    r = requests.post(url, headers=headers, data=data, timeout=360)
    r.raise_for_status()
```

After:
```python
import httpx
...

@celery.task()
def post_process(
    url: str, data: str | bytes | None, headers: dict[str, str] | None = None
) -> None:
    r = httpx.post(url, headers=headers, content=data, timeout=360)
    r.raise_for_status()
```

Note `data=data` → `content=data`. Same rationale as Step 4 (raw bytes/str payload, not form fields).

Verify:
```bash
grep -n 'requests' src/cancelchain/tasks.py
```

Expected: empty.

### Step 9: Update `src/cancelchain/command.py`

Three changes:
1. `import requests` → `import httpx`, and add `import json` (place alphabetically near the top — after `import os` and before `from datetime import timedelta`).
2. All 7 `except requests.HTTPError as e:` → `except httpx.HTTPStatusError as e:`.
3. `except (AttributeError, requests.exceptions.JSONDecodeError):` → `except (AttributeError, json.JSONDecodeError):`.
4. `http_error_message(e: requests.HTTPError) -> str | None:` signature → `http_error_message(e: httpx.HTTPStatusError) -> str | None:`.

```bash
sed -i 's/^import requests$/import httpx/' src/cancelchain/command.py
sed -i 's/except requests.HTTPError as e:/except httpx.HTTPStatusError as e:/g' src/cancelchain/command.py
sed -i 's/except (AttributeError, requests.exceptions.JSONDecodeError):/except (AttributeError, json.JSONDecodeError):/g' src/cancelchain/command.py
sed -i 's/def http_error_message(e: requests.HTTPError) -> str | None:/def http_error_message(e: httpx.HTTPStatusError) -> str | None:/g' src/cancelchain/command.py
```

Then open `src/cancelchain/command.py` and add `import json` at the top of the imports if not already present. Check with:
```bash
grep -n '^import json' src/cancelchain/command.py
```

If empty, add it (after `import os`, before `from datetime import timedelta` — the file uses one-import-per-line stdlib style).

Verify final state:
```bash
grep -n 'requests' src/cancelchain/command.py
```

Expected: empty.

### Step 10: Update `tests/conftest.py` — replace the proxy fixtures

Open `tests/conftest.py`. Three edits:

**Edit A: Add httpx import.** Near the top imports (after `import re` and before `from tempfile`), add:
```python
import httpx
```

Verify the existing `from unittest.mock import patch` line is already present (it is — used elsewhere in conftest.py).

**Edit B: Replace the `requests_proxy` fixture.** Find lines 441–456 (the `requests_proxy` fixture):

Before:
```python
@pytest.fixture
def requests_proxy(app, host, requests_mock, test_client):
    def test_client_proxy(request, context):
        if request.method == 'GET':
            r = test_client.get(request.url, headers=dict(request.headers))
        elif request.method == 'POST':
            r = test_client.post(
                request.url, headers=dict(request.headers), data=request.body
            )
        context.headers = r.headers
        context.status_code = r.status_code
        return r.data

    matcher = re.compile(f'{host}/.*')
    requests_mock.get(matcher, content=test_client_proxy)
    requests_mock.post(matcher, content=test_client_proxy)
```

After:
```python
@pytest.fixture
def requests_proxy(app, host):
    """WSGITransport-backed httpx client that routes outbound HTTP from
    ApiClient into the Flask test app. Named `requests_proxy` for
    backward-compatibility with the ~25 tests that consume the fixture
    by name; the underlying mechanism is httpx + WSGITransport.

    Side effect: rebuilds app.clients under the active _make_client
    monkeypatch so peer-gossip code in Node / Miller routes through
    WSGI too.
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
        for c in list(app.clients.values()):
            c.close()
        app.clients = create_clients(app)
        with httpx.Client(transport=transport, base_url=host) as client:
            yield client
        for c in list(app.clients.values()):
            c.close()
```

**Edit C: Replace the `remote_requests_proxy` fixture** (lines 459–478). Same pattern, using `remote_app` / `remote_host`:

Before:
```python
@pytest.fixture
def remote_requests_proxy(
    remote_app, remote_host, requests_mock, remote_test_client
):
    def remote_test_client_proxy(request, context):
        if request.method == 'GET':
            r = remote_test_client.get(
                request.url, headers=dict(request.headers)
            )
        elif request.method == 'POST':
            r = remote_test_client.post(
                request.url, headers=dict(request.headers), data=request.body
            )
        context.headers = r.headers
        context.status_code = r.status_code
        return r.data

    matcher = re.compile(f'{remote_host}/.*')
    requests_mock.get(matcher, content=remote_test_client_proxy)
    requests_mock.post(matcher, content=remote_test_client_proxy)
```

After:
```python
@pytest.fixture
def remote_requests_proxy(remote_app, remote_host):
    """Counterpart to `requests_proxy` for the second Flask app used in
    peer-gossip tests. See `requests_proxy` for mechanism.
    """
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
        for c in list(remote_app.clients.values()):
            c.close()
```

The fixture keeps the name `requests_proxy` / `remote_requests_proxy` so the existing test signatures don't change. The fixture's *return value* changes: tests now receive an `httpx.Client` (where the old fixture returned `None`).

If `import re` is no longer used anywhere in `tests/conftest.py` after these edits, remove it. Check with `grep -n '\bre\.' tests/conftest.py` — `re.compile` was used only by the two old fixtures. Other `re` references would keep the import. The file's existing `re.compile` usage is exclusively in those two old fixtures, so this drops cleanly.

Also remove now-unused `from unittest.mock import patch` ONLY if no other consumer in the file uses it; check with `grep -n 'patch(' tests/conftest.py`. Likely still used elsewhere — keep the import then.

### Step 11: Update `tests/test_api_client.py`

Replace 4 references:
- Line 2: `import requests` → `import httpx`.
- Line 14: `requests.exceptions.HTTPError` → `httpx.HTTPStatusError`.
- Lines 37, 40, 46: `requests.codes.ok` → `httpx.codes.OK`.

```bash
sed -i 's/^import requests$/import httpx/' tests/test_api_client.py
sed -i 's/requests\.exceptions\.HTTPError/httpx.HTTPStatusError/g' tests/test_api_client.py
sed -i 's/requests\.codes\.ok/httpx.codes.OK/g' tests/test_api_client.py
grep -n 'requests' tests/test_api_client.py
```

Expected: empty after grep.

### Step 12: Update `tests/test_api.py`

This is the largest test-file change. Six categories of swap:

1. `import requests` → `import httpx`.
2. `requests.exceptions.HTTPError` → `httpx.HTTPStatusError` (used in `pytest.raises(...)` arguments).
3. `requests.codes.ok` → `httpx.codes.OK`.
4. `requests.codes.unauthorized` → `httpx.codes.UNAUTHORIZED`.
5. `requests.codes.bad_request` → `httpx.codes.BAD_REQUEST`.
6. `requests.codes.not_found` → `httpx.codes.NOT_FOUND`.
7. `requests.codes.created` → `httpx.codes.CREATED`.
8. Direct module-level calls `requests.get(url, ...)` / `requests.post(url, ...)` in `test_post_token_none` and `test_post_token_invalid` and `test_no_auth` — replace with calls through the `requests_proxy` fixture (the fixture now yields an httpx client). Each test that does this gains `requests_proxy` as a parameter binding the client; the call becomes `requests_proxy.get(path, timeout=TIMEOUT)` or `requests_proxy.post(path, ...)`. Drop the `urljoin(host, ...)` wrapping (the client's `base_url` handles it).

For categories 1–7, sed batches:
```bash
sed -i 's/^import requests$/import httpx/' tests/test_api.py
sed -i 's/requests\.exceptions\.HTTPError/httpx.HTTPStatusError/g' tests/test_api.py
sed -i 's/requests\.codes\.ok/httpx.codes.OK/g' tests/test_api.py
sed -i 's/requests\.codes\.unauthorized/httpx.codes.UNAUTHORIZED/g' tests/test_api.py
sed -i 's/requests\.codes\.bad_request/httpx.codes.BAD_REQUEST/g' tests/test_api.py
sed -i 's/requests\.codes\.not_found/httpx.codes.NOT_FOUND/g' tests/test_api.py
sed -i 's/requests\.codes\.created/httpx.codes.CREATED/g' tests/test_api.py
```

For category 8, edit manually. The affected tests:

**`test_post_token_none`** (lines 18–22): the fixture is already in the signature. Replace the direct call:

Before:
```python
def test_post_token_none(app, host, requests_proxy, wallet):
    response = requests.post(
        urljoin(host, f'/api/token/{wallet.address}'), timeout=TIMEOUT
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED
```

After:
```python
def test_post_token_none(app, requests_proxy, wallet):
    response = requests_proxy.post(
        f'/api/token/{wallet.address}', timeout=TIMEOUT
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED
```

(Drop `host` from the signature since it's no longer referenced; drop `urljoin` from the call.)

**`test_post_token_invalid`** (lines 25–34):

Before:
```python
def test_post_token_invalid(app, host, requests_proxy, wallet):
    headers = {'Content-Type': 'application/json'}
    url = urljoin(host, f'/api/token/{wallet.address}')
    _ = requests.get(url, timeout=TIMEOUT)
    response = requests.post(url, data='foo', headers=headers, timeout=TIMEOUT)
    assert response.status_code == httpx.codes.BAD_REQUEST
    response = requests.post(
        url, data='{"challenge": "foo"}', headers=headers, timeout=TIMEOUT
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED
```

After:
```python
def test_post_token_invalid(app, requests_proxy, wallet):
    headers = {'Content-Type': 'application/json'}
    path = f'/api/token/{wallet.address}'
    _ = requests_proxy.get(path, timeout=TIMEOUT)
    response = requests_proxy.post(
        path, content='foo', headers=headers, timeout=TIMEOUT
    )
    assert response.status_code == httpx.codes.BAD_REQUEST
    response = requests_proxy.post(
        path,
        content='{"challenge": "foo"}',
        headers=headers,
        timeout=TIMEOUT,
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED
```

(httpx uses `content=` for raw payloads; `data=` would be interpreted as form fields.)

**`test_no_auth`** (lines 125–127):

Before:
```python
def test_no_auth(app, host, requests_proxy, wallet):
    response = requests.get(urljoin(host, '/api/block'), timeout=TIMEOUT)
    assert response.status_code == httpx.codes.UNAUTHORIZED
```

After:
```python
def test_no_auth(app, requests_proxy, wallet):
    response = requests_proxy.get('/api/block', timeout=TIMEOUT)
    assert response.status_code == httpx.codes.UNAUTHORIZED
```

If `from urllib.parse import urljoin` is no longer used anywhere in `test_api.py` after these edits, remove that import. Check with `grep -n 'urljoin' tests/test_api.py`.

Final verification:
```bash
grep -n 'requests' tests/test_api.py
```

Expected: empty.

### Step 13: Update `tests/test_browser.py`

This file only references `requests.codes.*`. No proxy fixture, no direct calls. Pure codes rename.

```bash
sed -i 's/^import requests$/import httpx/' tests/test_browser.py
sed -i 's/requests\.codes\.ok/httpx.codes.OK/g' tests/test_browser.py
sed -i 's/requests\.codes\.not_found/httpx.codes.NOT_FOUND/g' tests/test_browser.py
grep -n 'requests' tests/test_browser.py
```

Expected: empty.

### Step 14: Update `tests/test_command.py`

This file doesn't import `requests` directly. It only references the `requests_proxy` fixture by name in test signatures. Since the fixture name stays `requests_proxy` (per Step 10), no change is needed in `test_command.py` at all. Confirm:

```bash
grep -n '^import\|requests' tests/test_command.py
```

Expected: no `import requests` line and no other `requests` references besides the `requests_proxy` fixture parameter usages — which stay as-is.

### Step 15: Add the 2 new `ApiClient` lifecycle tests

Append to the end of `tests/test_api_client.py`:

```python


def test_api_client_close_releases_underlying_client(app, host, wallet):
    """ApiClient.close() releases the wrapped httpx.Client."""
    with app.app_context():
        c = ApiClient(host, wallet)
        assert c._client.is_closed is False
        c.close()
        assert c._client.is_closed is True


def test_api_client_context_manager_closes_on_exit(app, host, wallet):
    """`with ApiClient(...) as c:` closes the wrapped httpx.Client on exit."""
    with app.app_context():
        with ApiClient(host, wallet) as c:
            assert c._client.is_closed is False
        assert c._client.is_closed is True
```

Both tests touch `ApiClient._client.is_closed` — `httpx.Client.is_closed` is a documented public attribute (True after `.close()`, False otherwise). These tests don't need the `requests_proxy` fixture; they just verify lifecycle.

### Step 16: Verify all gates

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

All four must exit 0. Test count: 214 → 216 (2 new lifecycle tests).

If `mypy` flags new errors:
- `httpx.WSGITransport(app=...)` may want a stricter `app:` parameter type. The kwarg is `app: WSGIApplication` from `wsgiref.types`. If mypy complains in `conftest.py`, narrow by ignoring at that line (`# type: ignore[arg-type]`) — tests aren't type-checked under strict by default, but conftest.py may be. Limit to one ignore.
- `_make_client(base_url, timeout)` — ensure return type annotation `-> httpx.Client` is present (it is in Step 4's code).

If `ruff` flags `B008` ("do not perform function call in argument defaults") on the fixture parameters or `S101` (asserts) anywhere, those are already ignored project-wide (S101 for tests). B008 would be a real issue — investigate.

If `pytest` fails on:
- A test asserting `r.status_code == 200` via `httpx.codes.OK` — verify `httpx.codes.OK` evaluates to `200` (it does — `httpx.codes` is an `IntEnum`).
- `test_post_token_invalid` — verify `content='foo'` semantics in the new fixture-yielded httpx client; httpx sends the raw bytes/string body as-is, matching what `requests.post(..., data='foo')` did.
- Anywhere the `httpx.HTTPStatusError`'s `match='401'` regex on `pytest.raises(httpx.HTTPStatusError, match='401')` doesn't match — httpx's `HTTPStatusError.__str__` includes the status text. Run a single test to inspect: `uv run pytest tests/test_api.py::test_no_role -v --tb=long`. If the regex doesn't match, widen the `match=` pattern (e.g., `match='401|Unauthorized'`).

### Step 17: Commit

```bash
git add pyproject.toml uv.lock src/cancelchain/api_client.py src/cancelchain/node.py src/cancelchain/miller.py src/cancelchain/command.py src/cancelchain/tasks.py tests/conftest.py tests/test_api.py tests/test_api_client.py tests/test_browser.py tests/test_command.py
git commit -m "$(cat <<'EOF'
feat(deps): swap requests → httpx across cancelchain

Phase 5b. Transport-layer swap. Greenfield posture (no production
deploy, no on-wire compat constraint): no backward-compat shims.

src/cancelchain/api_client.py:
- ApiClient now holds a persistent httpx.Client built via the
  module-scope _make_client(base_url, timeout) factory (single test
  seam — fixtures monkeypatch it to inject httpx.WSGITransport).
- Adds close() and __enter__/__exit__ (context-manager support for
  CLI consumers via host_api_client).
- All 8 GET/POST helpers route through self._client; absolute paths
  resolve against the client's base_url.
- requests.codes.* → httpx.codes.* (still IntEnum integers).
- Return type changes from requests.Response to httpx.Response —
  identical surface for the .status_code / .json() / .text /
  .raise_for_status() / .headers attributes that callers use.

src/cancelchain/node.py, src/cancelchain/miller.py:
- requests.RequestException → httpx.RequestError catches.

src/cancelchain/command.py:
- requests.HTTPError → httpx.HTTPStatusError catches.
- requests.exceptions.JSONDecodeError → json.JSONDecodeError
  (httpx delegates Response.json() to stdlib).

src/cancelchain/tasks.py:
- requests.post(url, ..., data=...) → httpx.post(url, ..., content=...)
  (httpx uses content= for raw bytes/str payloads; data= is for form
  fields).

tests/conftest.py:
- requests_proxy / remote_requests_proxy fixtures rewritten to back
  onto httpx.WSGITransport(app=...). Same fixture names, same test
  signatures across the suite — only the fixture mechanism changes.
  Now yields an httpx.Client (previously yielded None); tests that
  used to call requests.get/post(host, ...) directly now call
  fixture.get/post(path, ...) against the base_url-bound client.
- Existing app.clients entries are rebuilt under the active
  _make_client monkeypatch so peer-gossip routes through WSGI too.
- requests_mock import removed.

tests/test_api.py:
- requests.exceptions.HTTPError → httpx.HTTPStatusError.
- requests.codes.{ok,unauthorized,bad_request,not_found,created}
  → httpx.codes.{OK,UNAUTHORIZED,BAD_REQUEST,NOT_FOUND,CREATED}.
- Three tests previously calling requests.get/post(host, ...) directly
  (test_post_token_none, test_post_token_invalid, test_no_auth) now
  call the requests_proxy fixture client with relative paths.
  urljoin imports dropped where no longer used.

tests/test_api_client.py:
- Same exception + codes renames.
- Adds 2 new lifecycle tests verifying ApiClient.close() and the
  context-manager protocol both close the underlying httpx.Client.

tests/test_browser.py:
- requests.codes.* → httpx.codes.*.

tests/test_command.py:
- No change (consumed requests_proxy by name only; name preserved).

pyproject.toml:
- "requests>=2.32" removed from [project.dependencies].
- "httpx>=0.28" added in alphabetical position.
- "requests-mock>=1.12" removed from [dependency-groups].dev.

Test count: 214 → 216.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 18: Push and open PR

```bash
git push -u origin feat/httpx-swap
gh pr create --base main --title "feat(deps): swap requests → httpx across cancelchain" --body "$(cat <<'EOF'
## Summary
- Replaces \`requests\` with \`httpx\` across \`src/cancelchain/\` and \`tests/\` (transport-layer swap; no API or wire-format changes).
- Drops \`requests>=2.32\` from \`[project.dependencies]\`, adds \`httpx>=0.28\`.
- Drops \`requests-mock>=1.12\` from \`[dependency-groups].dev\` — replaced by httpx's native \`WSGITransport\`.
- \`ApiClient\` now holds a persistent \`httpx.Client\` built via a module-scope \`_make_client(base_url, timeout)\` factory — the single seam fixtures monkeypatch to inject \`WSGITransport(app=flask_app)\`. Adds \`close()\` / context-manager support.
- Exception swaps: \`requests.RequestException\` → \`httpx.RequestError\`, \`requests.HTTPError\` → \`httpx.HTTPStatusError\`, \`requests.exceptions.JSONDecodeError\` → \`json.JSONDecodeError\`.
- 2 new tests in \`tests/test_api_client.py\` covering \`ApiClient\` lifecycle (\`close()\` and context-manager close).
- Fixture names \`requests_proxy\` / \`remote_requests_proxy\` preserved (mechanism changed; signatures unchanged) so the existing ~25 test sites stay as-is.

**Greenfield posture**: no production deploy, no on-wire compat constraint, no peer endpoints to coordinate with. Transport-layer swap only — wire format identical.

Phase 5b. Spec/plan merged in the preceding docs PR.

## Test plan
- [x] \`uv run mypy\` exits 0.
- [x] \`uv run pytest\` passes (214 → 216).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [x] \`uv run python -c "import requests"\` raises ModuleNotFoundError.
- [x] \`uv run python -c "import requests_mock"\` raises ModuleNotFoundError.
- [x] \`grep -rn 'import requests\\\\|from requests' src/cancelchain/ tests/\` returns nothing.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 19: Stop — controller handles wor + mwg + sync

---

## Task 3: Phase 5b acceptance verification

**Files:** none modified. Final verification after the impl PR lands.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

Expected: top two commits are the docs PR squash and the impl PR squash.

- [ ] **Step 2: Fresh sync**

```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```

Expected: Python 3.12.x and a fresh venv.

- [ ] **Step 3: requests absent**

```bash
grep -rn 'import requests\|from requests' src/cancelchain/ tests/
grep -c 'requests' pyproject.toml
grep -ci '"requests"' uv.lock
uv run python -c "import requests" 2>&1 | head -3
uv run python -c "import requests_mock" 2>&1 | head -3
```

Expected: first grep returns nothing; second grep returns `0` or only matches inside unrelated strings (verify by eye); third grep returns `0`; both Python imports raise `ModuleNotFoundError`.

- [ ] **Step 4: httpx present**

```bash
uv run python -c "from importlib.metadata import version; print('httpx', version('httpx'))"
uv run python -c "import httpx; print(httpx.WSGITransport)"
```

Expected: prints `httpx 0.28.x` (or newer) and the `WSGITransport` class repr.

- [ ] **Step 5: Hard CI gates pass**

```bash
uv run ruff check src tests; echo "ruff check exit: $?"
uv run ruff format --check src tests; echo "ruff format exit: $?"
uv run mypy; echo "mypy exit: $?"
```

All three exit 0.

- [ ] **Step 6: Tests pass on 3.12 and 3.13**

```bash
uv run --python 3.12 pytest 2>&1 | tail -3
uv run --python 3.13 pytest 2>&1 | tail -3
```

Expected: both print `216 passed, 1 skipped` (or whatever the new count is — should be 2 more than 214).

- [ ] **Step 7: CLI smoke**

```bash
uv run cancelchain --help
```

Expected: prints the full command tree.

- [ ] **Step 8: ApiClient smoke**

```bash
uv run python <<'PY'
from cancelchain.api_client import ApiClient, _make_client
import httpx

# Factory returns a vanilla httpx.Client in production.
c = _make_client('http://example.test', 10.0)
assert isinstance(c, httpx.Client)
print('_make_client returns httpx.Client OK')
c.close()

# OK / UNAUTHORIZED constants resolve to the integer status codes.
from cancelchain.api_client import OK, UNAUTHORIZED
assert OK == 200, f'expected 200, got {OK}'
assert UNAUTHORIZED == 401, f'expected 401, got {UNAUTHORIZED}'
print(f'OK={OK}, UNAUTHORIZED={UNAUTHORIZED}')
PY
```

Expected: prints `_make_client returns httpx.Client OK` and `OK=200, UNAUTHORIZED=401`.

- [ ] **Step 9: Docker build smoke**

```bash
docker build --target builder -t cc-phase5b-final .
```

Expected: succeeds.

- [ ] **Step 10: Acceptance complete**

If Steps 1–9 all pass, Phase 5b is done. No commit.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 2) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory).
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend) and post a `/copilot review` comment on the PR — Copilot's auto-review only fires on the initial push; subsequent rounds need the manual trigger (per the user's memory).

---

## Risks and watchpoints

### Risk: httpx `data=` vs `content=` semantics

`requests.post(url, data=str_or_bytes)` sends the raw payload as the body. `httpx.post(url, data=str_or_bytes)` interprets `data` as form fields and will encode it as `application/x-www-form-urlencoded` (or fail). The equivalent in httpx is `content=str_or_bytes`. The rewrite uses `content=` everywhere a raw JSON string body was being sent.

If anywhere in the rewrite still passes `data=` to httpx, you'll see one of two failures: a `TypeError` (httpx expects dict-like for `data`) or the wrong Content-Type on the wire. `grep -n 'self._client.\(get\|post\)(' src/cancelchain/api_client.py | grep -v 'content=\|params=\|headers='` should not show suspicious `data=` usage; if it does, fix before committing.

### Risk: `httpx.HTTPStatusError`'s `match=` regex in `pytest.raises`

The existing tests use `pytest.raises(requests.exceptions.HTTPError, match='401')` — `requests.HTTPError.__str__` includes the status code as text. httpx's `HTTPStatusError.__str__` looks like:

```
Client error '401 Unauthorized' for url 'http://localhost:8080/api/block'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401
```

The substring `'401'` IS in that text, so `match='401'` continues to match. Same for `match='403'`, `match='404'`, `match='405'`. If any single test fails on the `match=` regex after the swap, widen to `match='40[0-9]|Unauthorized|Forbidden|Not Found'` for that test specifically — do NOT do a bulk-find-replace.

### Risk: `httpx.WSGITransport` and Flask request context

WSGITransport invokes the Flask WSGI app stack directly, which means real middleware and error handlers run. The current `requests_proxy` fixture also invokes the full Flask path (via `test_client.get/post`), so behavior should match — but watch for:

- Tests that depended on `requests_mock.last_request` or `requests_mock.adapter` metadata. `grep -rn 'requests_mock\.' tests/` should return nothing after the swap; any hit is a tell.
- Tests that wrap multiple HTTP calls in a single `with app.app_context():` block. WSGITransport synthesizes a fresh request environ per call (just like `test_client` does), so app contexts behave the same way.

### Risk: `urljoin` and `base_url` edge cases

httpx's `Client(base_url='http://localhost:8080').get('/api/foo')` resolves to `http://localhost:8080/api/foo` per RFC 3986. The existing `urljoin('http://localhost:8080', '/api/foo')` does the same. Edge cases worth verifying with the test suite:

- Trailing slash on `base_url` (`http://localhost:8080/`) — httpx and urljoin both strip and re-add as needed. The existing host fixtures use no trailing slash.
- Path with query string (`/api/transaction/pending?earliest=...`) — httpx accepts these correctly; `params=` is the idiomatic way.
- Absolute URL passed where relative was expected — httpx accepts both; `base_url` is ignored if a full URL is given.

If `test_host_address` fails (the one that exercises `http://address@host` URLs), inspect the `httpx.URL` parsing — httpx may strip the userinfo component (which is what `host_address` is supposed to do anyway). Verify by running `uv run pytest tests/test_api_client.py::test_host_address -v`.

### Risk: `_make_client` monkeypatch leaking between tests

The `requests_proxy` / `remote_requests_proxy` fixtures use `with patch(...)` so the monkeypatch reverts cleanly at fixture teardown. But: if a test imports `cancelchain.api_client._make_client` BEFORE the fixture activates the patch, the imported reference points to the unpatched function. The fix is to NEVER `from cancelchain.api_client import _make_client` in test code — always reference it as `cancelchain.api_client._make_client` (the conftest fixture's patch target string). The plan does not introduce any direct imports of `_make_client` in test files, so this risk is contained — flag it in the spec / plan but no action required at implementation time.

### Risk: `httpx.Client.is_closed` semantics

The 2 new lifecycle tests assert `client.is_closed is False/True`. `httpx.Client.is_closed` is a documented public boolean attribute (not a method). If httpx 0.28+ changes the API, the tests catch the regression. If httpx ships a version where `is_closed` doesn't exist on the sync Client (unlikely — it's been stable since 0.19), substitute the equivalent check (e.g., calling `.get(...)` after close should raise; that's also a valid lifecycle assertion).

### Risk: leftover `urljoin` references

After the rewrites in Steps 4, 11, and 12, the `urljoin` import in `tests/test_api.py` may be orphaned. `grep -n urljoin tests/test_api.py` after Step 12 should return at most the `from urllib.parse import urljoin` line and zero call sites — in which case remove the import. ruff will flag the unused import; the formatter run in Step 16 will catch it.

### Risk: `requests-mock` lingering as a transitive dep

After Step 3, verify with `grep -ni requests-mock uv.lock` and `grep -n requests-mock pyproject.toml`. The first should return nothing (regenerated lockfile). The second should return nothing (Step 2's edit). If anything lingers, something's holding a reference — investigate before merging.
