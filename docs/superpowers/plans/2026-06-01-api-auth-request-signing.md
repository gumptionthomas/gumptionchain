# API Auth: Per-Request Wallet Signatures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the token handshake (challenge/response + HS256 bearer JWT) with stateless per-request wallet signatures (`cc-sig-v1`), closing audit findings A1.a/A2.c/A2.e/A7.a by removal and taking the audit to 0/0/0/0.

**Architecture:** A new `signing.py` module owns the protocol (canonical string, `CC-*` headers, sign, verify) as the single source of canonicalization truth, shared by the server (`authorize()`) and the client (`ApiClient`). The swap is atomic: server verification, client signing, and the test overhaul land together; `TokenView`/`ApiToken`/argon2/PyJWT and `SECRET_KEY`-as-auth are then removed.

**Tech Stack:** Python 3.12, Flask, httpx, pytest. RSA-PKCS1v15-SHA384 via the existing `Wallet.sign`/`validate_signature`. No new dependency; two dependencies (PyJWT, argon2-cffi) removed. `mypy --strict` over `src/`.

Companion spec: `docs/superpowers/specs/2026-06-01-api-auth-request-signing-design.md`.

---

## Prerequisites

- Working directory: cancelchain repo root.
- A3.b is merged: `git log --oneline -1 main` shows `980ba0d` (`fix(a3b): JWT iss/aud node-binding …`) or later.
- The branch `docs/api-auth-request-signing` exists with the spec commits. This plan adds the plan file and ships both as the docs PR.
- Test baseline: **271 passed, 4 xfailed, 1 skipped**.
- CI hard-gates: `ruff check`, `ruff format --check`, `pytest`, `mypy`, `cancelchain db upgrade` + `cancelchain db check`.
- **Review loop** (`feedback_internal_review_then_one_copilot`): internal cross-model review to convergence before the PR (include the regression-impact check), then one Copilot backstop. Copilot does **not** auto-re-review — trigger `gh pr comment <N> --body "/copilot review"` if needed. `wor`/`mwg` are controller work.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-06-01-api-auth-request-signing.md` (this) + spec on branch |
| 2 | impl PR | NEW `src/cancelchain/signing.py`; NEW `tests/test_signing.py` |
| 3 | impl PR | `src/cancelchain/api.py` (authorize + remove TokenView/routes); `src/cancelchain/api_client.py` (sign; remove token methods); `tests/test_api.py`, `tests/test_api_client.py`, `tests/test_auth_audit.py` (overhaul) |
| 4 | impl PR | `src/cancelchain/models.py` (remove ApiToken); regenerate base migration; `pyproject.toml` + `uv.lock` (drop PyJWT, argon2-cffi) |
| 5 | impl PR | NEW `docs/api-auth-protocol.md` |
| 6 | impl PR | `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md` |
| 7 | impl PR | push + open PR |
| 8 | acceptance | none |

---

## Task 1: Ship the docs PR (spec + plan)

- [ ] **Step 1: Confirm branch + spec tracked**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-06-01-api-auth-request-signing-design.md
git status docs/superpowers/plans/2026-06-01-api-auth-request-signing.md
```
Expected: branch `docs/api-auth-request-signing`; spec tracked; plan untracked.

- [ ] **Step 2: Commit the plan**

```bash
git add docs/superpowers/plans/2026-06-01-api-auth-request-signing.md
git commit -m "$(cat <<'EOF'
docs(auth-signing): per-request signature implementation plan

Plan executes the handshake replacement: new signing.py (canonical
string + CC-* headers + sign/verify), atomic swap of authorize() +
ApiClient + tests, removal of TokenView/ApiToken/argon2/PyJWT and
SECRET_KEY-as-auth, base-migration regen, public protocol doc, and
audit close-out to 0/0/0/0.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push + open the docs PR**

```bash
git push -u origin docs/api-auth-request-signing
gh pr create --base main --head docs/api-auth-request-signing --title "docs(auth-signing): per-request wallet-signature auth design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the design spec + implementation plan to replace the token handshake with stateless per-request wallet signatures (`cc-sig-v1`).
- No code changes.

Closes audit findings A1.a/A2.c/A2.e/A7.a by removal (token endpoint + symmetric key gone); preserves A4.a/A3.a/A5.b/A3.b; audit → 0/0/0/0. Versioned scheme (`CC-Sig-Version`) + public protocol doc; RFC 9421 deferred as an additive `v2`. Drops the PyJWT + argon2-cffi deps. No schema (pre-1.0 base-migration regen).

## Test plan
- [x] Spec + plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 2: The `signing.py` module (additive, green)

**Files:** Create `src/cancelchain/signing.py`, `tests/test_signing.py`.

Branch off main after the docs PR merges:
```bash
git checkout main && git pull --ff-only
git checkout -b feat/api-auth-request-signing
uv run pytest -q 2>&1 | tail -1   # baseline: 271 passed, 4 xfailed, 1 skipped
```

- [ ] **Step 1: Write `tests/test_signing.py` (failing — module doesn't exist)**

```python
import time

import pytest

from cancelchain import signing
from cancelchain.wallet import Wallet

REQ = dict(
    method='POST',
    path='/api/block/abc',
    query='earliest=1',
    body=b'{"x":1}',
    node_host='localhost:8080',
)


def test_sign_then_verify_roundtrip():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    assert headers[signing.H_VERSION] == signing.SIG_VERSION
    assert headers[signing.H_ADDRESS] == w.address
    addr = signing.verify(headers, **REQ)
    assert addr == w.address


def test_verify_rejects_tampered_path():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'path': '/api/block/DIFFERENT'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_body():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'body': b'{"x":2}'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_wrong_node():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'node_host': 'peer.node:8888'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_stale_timestamp():
    w = Wallet()
    old = int(time.time()) - (signing.FRESHNESS_SECONDS + 1)
    headers = signing.sign_headers(w, timestamp=old, **REQ)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_future_timestamp():
    w = Wallet()
    future = int(time.time()) + (signing.FRESHNESS_SECONDS + 1)
    headers = signing.sign_headers(w, timestamp=future, **REQ)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_pubkey_address_mismatch():
    w = Wallet()
    other = Wallet()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_PUBKEY] = other.public_key_b64  # pubkey != address
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_missing_header():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    del headers[signing.H_SIGNATURE]
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_unknown_version():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_VERSION] = '999'
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)
```

- [ ] **Step 2: Run — verify it fails (ImportError)**

```bash
uv run pytest tests/test_signing.py -q 2>&1 | tail -5
```
Expected: collection error / ImportError (`cancelchain.signing` doesn't exist).

- [ ] **Step 3: Create `src/cancelchain/signing.py`**

```python
from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

from cancelchain.exceptions import InvalidKeyError
from cancelchain.wallet import Wallet

SIG_VERSION = '1'  # CC-Sig-Version header value (dispatch key)
SIG_SCHEME = 'cc-sig-v1'  # scheme id bound into the signed canonical
FRESHNESS_SECONDS = 300

H_VERSION = 'CC-Sig-Version'
H_ADDRESS = 'CC-Address'
H_PUBKEY = 'CC-Public-Key'
H_TIMESTAMP = 'CC-Timestamp'
H_SIGNATURE = 'CC-Signature'


class SignatureError(Exception):
    """A signed request failed verification (treated as 401 by the API)."""


def _canonical(
    *,
    method: str,
    path: str,
    query: str,
    body: bytes | None,
    node_host: str,
    timestamp: str,
    address: str,
) -> bytes:
    body_digest = hashlib.sha256(body or b'').hexdigest()
    return '\n'.join(
        [
            SIG_SCHEME,
            method.upper(),
            path,
            query,
            body_digest,
            node_host,
            timestamp,
            address,
        ]
    ).encode()


def sign_headers(
    wallet: Wallet,
    *,
    method: str,
    path: str,
    query: str,
    body: bytes | None,
    node_host: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = str(int(timestamp if timestamp is not None else time.time()))
    canonical = _canonical(
        method=method,
        path=path,
        query=query,
        body=body,
        node_host=node_host,
        timestamp=ts,
        address=wallet.address,
    )
    return {
        H_VERSION: SIG_VERSION,
        H_ADDRESS: wallet.address,
        H_PUBKEY: wallet.public_key_b64,
        H_TIMESTAMP: ts,
        H_SIGNATURE: wallet.sign(canonical),
    }


def verify(
    headers: Mapping[str, Any],
    *,
    method: str,
    path: str,
    query: str,
    body: bytes | None,
    node_host: str,
    now: int | None = None,
) -> str:
    """Verify a `cc-sig-v1` signed request; return the authenticated
    address or raise SignatureError.
    """
    if headers.get(H_VERSION) != SIG_VERSION:
        raise SignatureError('unsupported signature version')
    address = headers.get(H_ADDRESS)
    pubkey = headers.get(H_PUBKEY)
    ts = headers.get(H_TIMESTAMP)
    sig = headers.get(H_SIGNATURE)
    if not (address and pubkey and ts and sig):
        raise SignatureError('missing signature headers')
    try:
        ts_val = int(ts)
    except (TypeError, ValueError) as e:
        raise SignatureError('malformed timestamp') from e
    current = int(now if now is not None else time.time())
    if abs(current - ts_val) > FRESHNESS_SECONDS:
        raise SignatureError('stale or future timestamp')
    try:
        wallet = Wallet(b64ks=pubkey)
    except InvalidKeyError as e:
        raise SignatureError('invalid public key') from e
    if wallet.address != address:
        raise SignatureError('public key does not match address')
    canonical = _canonical(
        method=method,
        path=path,
        query=query,
        body=body,
        node_host=node_host,
        timestamp=ts,
        address=address,
    )
    if not wallet.validate_signature(canonical, sig):
        raise SignatureError('signature verification failed')
    return address
```

- [ ] **Step 4: Run — verify the suite passes**

```bash
uv run pytest tests/test_signing.py -q 2>&1 | tail -3
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```
Expected: all `test_signing` pass; ruff + mypy clean. (`InvalidKeyError` is in `cancelchain.exceptions`; `validate_signature` returns False on bad b64/sig, so a garbage signature → SignatureError, not a crash.)

- [ ] **Step 5: Commit**

```bash
git add src/cancelchain/signing.py tests/test_signing.py
git commit -m "$(cat <<'EOF'
feat(signing): cc-sig-v1 request-signing module (canonical + sign/verify)

Single source of canonicalization truth for the new per-request auth:
_canonical() builds the signed string (scheme/method/path/query/
body-digest/node-host/timestamp/address); sign_headers() emits the CC-*
headers via Wallet.sign; verify() checks version, freshness (±300s),
pubkey->address self-certification, and the RSA signature, returning the
authenticated address or raising SignatureError. Additive — not yet wired.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: The atomic swap — server verify + client sign + test overhaul

**Files:** `src/cancelchain/api.py`, `src/cancelchain/api_client.py`, `tests/test_api.py`, `tests/test_api_client.py`, `tests/test_auth_audit.py`.

This is one coherent change: the server must verify signatures, the client must produce them, and the auth tests must move to the signature model — the suite is only green once all three agree. Work to green, then a single commit.

- [ ] **Step 1: Rewrite `authorize()` (`src/cancelchain/api.py`)**

Add imports near the other `cancelchain` imports:
```python
from cancelchain import signing
from cancelchain.util import ciso_2_dt, host_address, now, now_iso  # host_address already imported; ensure it's present
```
Replace the entire `authorize()` `wrapper` body (the current signed-decode block) with:
```python
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                node_host = host_address(current_app.config['NODE_HOST'])[0]
                address = signing.verify(
                    request.headers,
                    method=request.method,
                    path=request.path,
                    query=request.query_string.decode(),
                    body=request.get_data(),
                    node_host=node_host,
                )
            except signing.SignatureError:
                abort(401)
            except Exception as e:
                current_app.logger.exception(e)
                abort(401)
            role = Role.address_role(address)
            if role is None or role.value < required_role.value:
                abort(403)
            kwargs['_address'] = address
            kwargs['_role'] = role
            return func(*args, **kwargs)
```
Then delete the now-dead JWT imports/uses in `api.py`: remove `import jwt`, and `API_TOKEN_SECONDS` if only the token path used it (grep to confirm before removing). Keep `Role`, `Role.address_role`, `Role.validate_config`, `abort`, `current_app`, `request`.

- [ ] **Step 2: Remove `TokenView` and its routes (`src/cancelchain/api.py`)**

Delete the entire `class TokenView(MethodView):` and the `blueprint.add_url_rule('/token/<address:address>', view_func=TokenView.as_view('token'), methods=['GET', 'POST'])` block. (Grep `token` in `api.py` afterward to confirm none remain.)

- [ ] **Step 3: Rewrite `ApiClient` to sign every request (`src/cancelchain/api_client.py`)**

Remove `request_token`, `get_token`, `reset_token`, `auth_header`, the `self.token` field, and the unused `OK`/`UNAUTHORIZED`/`json` imports if now unused (keep what's still referenced). Add `from cancelchain import signing`. Replace `get()` and `post()` with a shared `_send` that signs the *exact* request httpx will transmit (canonicalization-safe — derive `path`/`query` from the built request, not re-derived):

```python
    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        content: str | bytes | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        timeout_v: int | float = self.timeout if timeout is None else timeout
        body = content.encode() if isinstance(content, str) else (content or b'')
        req = self._client.build_request(
            method,
            path,
            headers=headers,
            params=params,
            content=content,
            timeout=timeout_v,
        )
        sig_headers = signing.sign_headers(
            self.wallet,
            method=method,
            path=req.url.path,
            query=req.url.query.decode(),
            body=body,
            node_host=self.host,
        )
        req.headers.update(sig_headers)
        r = self._client.send(req)
        if raise_for_status:
            r.raise_for_status()
        return r

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self._send(
            'GET',
            path,
            headers=headers,
            params=params,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        data: str | bytes | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self._send(
            'POST',
            path,
            headers=headers,
            content=data,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
```
Notes: `self.host` is the normalized netloc (`host_address(host)[0]`) — matches the server's `host_address(NODE_HOST)[0]`. The 401-retry loop is gone (a 401 is a real failure now). `req.url.path`/`req.url.query` are exactly what httpx sends, so the server's `request.path`/`request.query_string` match. The `Peer-Hosts` and `Content-Type` headers (passed via `headers=`) ride along unsigned. The `get_*`/`post_*`/`post_block`/`post_transaction` convenience methods are unchanged.

- [ ] **Step 4: Run the impact check — see the whole auth surface move**

```bash
uv run pytest tests/test_api.py tests/test_api_client.py tests/test_auth_audit.py -q 2>&1 | tail -25
```
Expected: lots of churn. ApiClient-based happy-path tests (`test_roles`, balance/support/pending, gossip) should PASS (they now sign automatically). FAILS/ERRORS will be: handshake tests (`test_post_token_*`), hand-built-`Authorization: Bearer` tests, and the dissolved-finding tests referencing `ApiToken`/`/api/token`. Steps 5-7 resolve them.

- [ ] **Step 5: Overhaul `tests/test_api.py`**

- **Delete** `test_post_token_none` and `test_post_token_invalid` (no token endpoint).
- The role/access tests (`test_roles`, `test_no_role`, `test_non_app_wallet`, the balance/support/pending/transfer tests, and the live-role + audience tests `test_authorize_insufficient_live_role_forbidden`/`test_authorize_honors_live_downgrade`) drive auth through `ApiClient`, which now signs — keep them; they should pass unchanged. **Remove** `test_authorize_rejects_wrong_audience_token` (it hand-mints a JWT — superseded by the signature negative tests below).
- **Add** signature negative/positive tests. They hand-build headers via `signing.sign_headers` and inject a defect, sending through `requests_proxy` (raw httpx into the app, `host_address(host)[0]` == app's `NODE_HOST` netloc == `localhost:8080`). Add `from cancelchain import signing` and `from cancelchain.util import host_address` imports:
```python
def _node(host):
    return host_address(host)[0]


def test_signed_request_accepted(app, host, mill_block, requests_proxy, reader_wallet):
    with app.app_context():
        mill_block(reader_wallet)  # reader in READER_ADDRESSES, on chain
        headers = signing.sign_headers(
            reader_wallet, method='GET', path='/api/block', query='',
            body=b'', node_host=_node(host),
        )
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.OK


def test_unsigned_request_rejected(app, host, mill_block, requests_proxy, reader_wallet):
    with app.app_context():
        mill_block(reader_wallet)
        r = requests_proxy.get('/api/block', timeout=60)  # no CC-* headers
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_tampered_path_rejected(app, host, mill_block, requests_proxy, reader_wallet):
    with app.app_context():
        mill_block(reader_wallet)
        headers = signing.sign_headers(
            reader_wallet, method='GET', path='/api/block', query='',
            body=b'', node_host=_node(host),
        )
        # signed for /api/block, sent to a different protected path
        r = requests_proxy.get('/api/transaction/pending', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_stale_timestamp_rejected(app, host, mill_block, requests_proxy, reader_wallet):
    with app.app_context():
        mill_block(reader_wallet)
        old = int(now().timestamp()) - (signing.FRESHNESS_SECONDS + 5)
        headers = signing.sign_headers(
            reader_wallet, method='GET', path='/api/block', query='',
            body=b'', node_host=_node(host), timestamp=old,
        )
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_pubkey_address_mismatch_rejected(app, host, mill_block, requests_proxy, reader_wallet):
    with app.app_context():
        mill_block(reader_wallet)
        headers = signing.sign_headers(
            reader_wallet, method='GET', path='/api/block', query='',
            body=b'', node_host=_node(host),
        )
        headers[signing.H_PUBKEY] = Wallet().public_key_b64  # pubkey != address
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED
```

- [ ] **Step 6: Overhaul `tests/test_api_client.py`**

Delete the token-handshake tests (anything exercising `request_token`/`get_token`/`auth_header`/the cipher dance). Keep/repoint the functional client tests (they call `ApiClient.get_*`/`post_*`, which now sign — they should pass). Add one assertion that `ApiClient.get`/`post` attach the `CC-*` headers (e.g. capture the request via the existing requests_proxy seam, or assert a round-trip 200 against a signed endpoint). If a test asserted on `.token`/`auth_header`, delete that assertion.

- [ ] **Step 7: Overhaul `tests/test_auth_audit.py`**

- **Delete** the dissolved-finding tests (their gap no longer exists): `test_a1_a_*`, `test_a2_c_*`, `test_a2_e_*`, `test_a7_a_*`. Remove now-unused imports (`ApiToken`, `create_app`, etc.) flagged by ruff.
- **Re-express the survivors** against signed requests (drop any `xfail`; they are passing regressions):
  - `test_a3_a_*` → a validly-signed request from a READER-only address (`reader_wallet`) to a MILLER endpoint (`POST /api/block/<hash>`) → **403** (live-role gate). Build headers with `signing.sign_headers` for the exact path/body and send via `requests_proxy`.
  - `test_a3_b_*` → a request signed for node A's `node_host` (`_node(host)`) sent to `remote_app` via `remote_requests_proxy` → **401** (the `remote_app` reconstructs `node_host = peer.node:8888`, so the signature fails). Replaces the old JWT cross-node test.
  - `test_a5_b_*` → sign as `miller_wallet`, then `app.config['MILLER_ADDRESSES'] = []`, then a signed MILLER-endpoint request → **403** (live role now `None`).
  - `test_a4_a_*` → unchanged (it tests `Role.validate_config`, independent of the transport).
- If the module is left with only re-expressed passing tests and no `xfail`, that's correct — the audit demonstrations are now regressions.

- [ ] **Step 8: Full suite + gates**

```bash
uv run pytest 2>&1 | tail -2
uv run ruff check src tests
uv run ruff format --check src tests   # run `uv run ruff format src tests` if it wants changes
uv run mypy
```
Expected: green, **0 xfailed** (all auth-audit demos are now passing regressions or deleted). The passed count differs from baseline (handshake + dissolved tests removed, signature tests added) — re-derive from output; do not hand-tune. Investigate any FAIL rather than papering over (a signature failure on a happy-path test usually means a canonicalization mismatch in `_send` — compare `req.url.path`/`req.url.query` to the server's `request.path`/`request.query_string`).

- [ ] **Step 9: Commit**

```bash
git add src/cancelchain/api.py src/cancelchain/api_client.py tests/test_api.py tests/test_api_client.py tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
feat(auth): replace token handshake with per-request wallet signatures

authorize() now verifies a cc-sig-v1 signature via signing.verify
(version, freshness, pubkey->address, RSA sig, node-binding) then the
live Role.address_role gate; no token decode. ApiClient signs every
request through _send() using the exact path/query httpx will transmit
(canonicalization-safe); request_token/get_token/auth_header/token and
the 401-retry loop are removed. TokenView and the /api/token routes are
deleted. Test suite moved to the signature model: handshake + dissolved-
finding tests (A1.a/A2.c/A2.e/A7.a) removed; A3.a/A3.b/A5.b re-expressed
as signed-request regressions; A4.a unchanged; new signature
negative/positive coverage. (ApiToken/argon2/dep removal follows.)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Remove `ApiToken`, regenerate the base migration, drop deps

**Files:** `src/cancelchain/models.py`, `src/cancelchain/migrations/versions/`, `pyproject.toml`, `uv.lock`.

- [ ] **Step 1: Remove `ApiToken` + argon2 from `models.py`**

Delete the entire `class ApiToken(Base):` definition, the `_PASSWORD_HASHER = PasswordHasher()` line, and the `from argon2 import PasswordHasher` / `from argon2.exceptions import InvalidHashError, VerifyMismatchError` imports. Grep `ApiToken|argon2|_PASSWORD_HASHER` across `src/` to confirm no remaining references (the api.py uses were removed in Task 3).

- [ ] **Step 2: Verify the suite still passes (ApiToken now unused)**

```bash
uv run pytest 2>&1 | tail -2
uv run ruff check src tests && uv run mypy
```
Expected: still green (nothing references `ApiToken` after Task 3). If a test imports `ApiToken`, it was missed in Task 3 — fix it.

- [ ] **Step 3: Regenerate the base migration (pre-1.0 convention)**

Per `project_pre_1_0_regenerate_base_migration` (single initial migration, no legacy installs): regenerate so `api_token` is no longer created.
```bash
rm src/cancelchain/migrations/versions/cc6afda9f01b_initial_schema.py
# regenerate against the current (ApiToken-free) models, from a clean throwaway DB:
rm -f /tmp/_ccgen.sqlite
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:////tmp/_ccgen.sqlite" uv run cancelchain db migrate -m "initial schema"
```
Then **hand-review** the new `src/cancelchain/migrations/versions/*_initial_schema.py`: confirm it creates all 11 surviving models + the `block_transaction` association table and **does not** create `api_token`; match the existing file's style (naming convention prefixes). (Alembic autogenerate is good but not perfect — verify CHECK constraints / server defaults as the existing base did.)

- [ ] **Step 4: Verify the migration matches the models (the CI gate)**

```bash
rm -f /tmp/_ccchk.sqlite
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:////tmp/_ccchk.sqlite" uv run cancelchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:////tmp/_ccchk.sqlite" uv run cancelchain db check
```
Expected: `upgrade` applies cleanly; `db check` reports no diff (models == migration). If `db check` shows a diff, hand-edit the migration to match.

- [ ] **Step 5: Drop the PyJWT + argon2-cffi dependencies**

Edit `pyproject.toml` `[project.dependencies]`: remove the `"pyjwt>=2.9"` and `"argon2-cffi>=23.1"` lines. Re-resolve the lock (authoritative, tracked):
```bash
uv lock
git diff --stat uv.lock   # should show pyjwt + argon2-cffi (and transitive deps) removed
```
Then confirm nothing imports them:
```bash
grep -rn "import jwt\|argon2" src/ tests/ || echo "clean: no jwt/argon2 imports"
uv run pytest 2>&1 | tail -2   # full suite still green with the deps gone
```

- [ ] **Step 6: Commit**

```bash
git add src/cancelchain/models.py src/cancelchain/migrations/versions/ pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
refactor(auth): remove ApiToken/argon2, regenerate base migration, drop deps

ApiToken and its argon2 hasher are gone (the signature auth needs no
server-side token state). Regenerated the single base migration so it no
longer creates api_token (pre-1.0, no legacy installs). Dropped the now-
unused PyJWT and argon2-cffi dependencies and re-resolved uv.lock.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Public protocol documentation

**Files:** Create `docs/api-auth-protocol.md`.

- [ ] **Step 1: Write the external-facing `cc-sig-v1` protocol doc**

Create `docs/api-auth-protocol.md` with these sections (precise enough that a third-party client author can implement it without reading the source):
- **Overview** — every API request (except the unauthenticated browser layer) is authenticated by a per-request signature made with the caller's wallet RSA private key and verified against the address's public key; no tokens, stateless.
- **Versioning** — the `CC-Sig-Version` header selects the scheme; this document specifies `1` (`cc-sig-v1`). Future schemes (e.g. RFC 9421) will be added as new versions accepted side-by-side.
- **Canonical string (`cc-sig-v1`)** — the exact newline-joined fields in order: `cc-sig-v1`, uppercased method, request path, raw query string (`""` if none), lowercase-hex SHA-256 of the raw body (SHA-256 of empty for no body), the node host (`host:port` of the target node), the unix-seconds timestamp, the CC address. State that path/query are the server-decoded forms and must match byte-for-byte.
- **Headers** — `CC-Sig-Version: 1`, `CC-Address`, `CC-Public-Key` (base64 DER SubjectPublicKeyInfo), `CC-Timestamp` (unix seconds), `CC-Signature` (base64 RSASSA-PKCS1-v1_5 over SHA-384 of the canonical bytes).
- **Verification & errors** — server checks version, freshness (±300s), that the public key hashes to the claimed address, the signature, and node-binding; any failure → `401`; insufficient role → `403`.
- **Worked example** — a `GET /api/block` and a `POST /api/block/<hash>` with the canonical string shown and the resulting headers (use placeholder values clearly marked as illustrative).
- **Algorithm note** — RSA-2048, PKCS1v15, SHA-384 (the chain's wallet signing alg); base64 is standard (not URL-safe).

Keep it accurate to `src/cancelchain/signing.py` (cite the header constants).

- [ ] **Step 2: Commit**

```bash
git add docs/api-auth-protocol.md
git commit -m "$(cat <<'EOF'
docs(auth): public cc-sig-v1 request-signing protocol spec

External-facing, versioned protocol doc so third-party client authors
can implement cc-sig-v1 without reading the source (canonical string,
CC-* headers, verification/error semantics, worked example, the
CC-Sig-Version evolution contract). SigV4-style: bespoke but documented.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Documentation close-out (audit 0/0/0/0, CLAUDE.md, roadmap)

**Files:** `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md`. Use `#PRNUM` for the impl PR number (filled after Task 7 opens the PR).

**Anti-drift rule:** after editing, `grep -niE "ApiToken|/api/token|SECRET_KEY|argon2|2 Medium|A2.c|A7.a|A1.a|A2.e" CLAUDE.md docs/superpowers/audits/2026-05-31-api-authentication-audit.md` and confirm each hit is either removed or correctly past-tensed/closed.

- [ ] **Step 1: CLAUDE.md — rewrite the API-auth description**

`grep -n "JWT\|token\|handshake\|RSA+AES\|ApiToken\|SECRET_KEY\|address_role\|authenticat" CLAUDE.md`. Replace the API-authentication paragraph (the two-step JWT handshake description) with: API requests are authenticated by a per-request wallet signature (`cc-sig-v1`) over a canonical string (version/method/path/query/body-digest/node-host/timestamp/address), sent in `CC-*` headers and verified against the address's self-certifying public key; stateless, ±300s freshness, node-bound; no token, no `SECRET_KEY` for auth. Link to `docs/api-auth-protocol.md`. Remove the now-false sentences about the GET/POST `/api/token` challenge, `ApiToken`, and `API_TOKEN_SECONDS`.

- [ ] **Step 2: Audit report — close to 0/0/0/0**

In `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`:
- **Exec summary headline** → `0 Critical / 0 High / 0 Medium / 0 Low` with a one-line note: the handshake was replaced with per-request wallet signatures (PR #PRNUM), dissolving the token-endpoint and symmetric-key findings.
- **Findings-table intro** → `0/0/0/0`; mark A2.c, A7.a, A1.a, A2.e rows `✅ (remediated by protocol replacement, PR #PRNUM)` and past-tense their descriptions.
- **Per-adversary sections** for A2.c, A7.a, A1.a, A2.e: prepend `✅ Remediated (PR #PRNUM). ` to each Finding, past-tense the gap, and append `(As implemented: the token handshake was replaced with stateless per-request wallet signatures; the /api/token endpoint, ApiToken table, argon2, and the symmetric SECRET_KEY auth secret are gone.)`
- **Cross-cutting observations**: update #2 (claim hygiene — moot, no JWT), #3 (unauthenticated-state+argon2 — gone), #6 (SECRET_KEY — retired from auth), #7 (roll-your-own challenge vs unused Wallet.sign — resolved: Wallet.sign now IS the auth primitive). Note `authorize_admin` housekeeping still stands if unaddressed.
- **Recommendations / targeted-vs-replacement**: record that the replacement (option per-request-signature) was implemented in PR #PRNUM; the audit is fully closed.

- [ ] **Step 3: Roadmap**

In `docs/superpowers/ROADMAP.md`: close the "API auth protocol replacement (design cycle)" entry as ✅ (PR #PRNUM), and mark the A2.c/A7.a + A1.a/A2.e remediation entries closed-by-replacement. Note the audit is fully closed `0/0/0/0`. Add a forward entry: "RFC 9421 as an additive `v2` auth scheme — deferred until third-party-client demand (the `cc-sig-v1` scheme is versioned for this)."

- [ ] **Step 4: Verify**

```bash
grep -n "0 Critical / 0 High / 0 Medium / 0 Low\|0/0/0/0" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "per-request|cc-sig-v1|wallet signature" CLAUDE.md
uv run pytest 2>&1 | tail -2
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```
Expected: audit headline 0/0/0/0; CLAUDE.md describes signature auth; suite green; gates clean.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
docs(auth): close out — audit 0/0/0/0, CLAUDE.md, roadmap

Audit fully closed: A2.c/A7.a/A1.a/A2.e remediated-by-protocol-replacement
(token endpoint + symmetric key removed); cross-cutting roll-your-own /
claim-hygiene / SECRET_KEY observations resolved. CLAUDE.md rewritten to
the per-request signature model with a pointer to docs/api-auth-protocol.md.
Roadmap closes the replacement cycle and records RFC 9421 as a deferred
additive v2. PR number placeholder #PRNUM.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Push + open impl PR

- [ ] **Step 1: Push**

```bash
git push -u origin feat/api-auth-request-signing
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "feat(auth): per-request wallet-signature auth (replace token handshake)" --body "$(cat <<'EOF'
## Summary
Replaces the challenge/response + HS256 bearer-JWT handshake with stateless per-request wallet signatures (`cc-sig-v1`).

- New `signing.py` owns the protocol (canonical string + `CC-*` headers + sign/verify), shared by `authorize()` and `ApiClient`.
- `authorize()` verifies a per-request signature (version, ±300s freshness, pubkey→address self-cert, RSA sig, node-binding), then the live `Role.address_role` gate. `ApiClient` signs every request.
- Removed: `TokenView` + `/api/token` routes, the `ApiToken` model/table (base migration regenerated), argon2, the **PyJWT + argon2-cffi** dependencies, and `SECRET_KEY`-as-auth.
- Versioned scheme + a public `docs/api-auth-protocol.md`; RFC 9421 deferred as an additive `v2`.

## Audit
Closes A1.a / A2.c / A2.e / A7.a **by removal** (token endpoint + symmetric key gone); preserves A4.a / A3.a / A5.b / A3.b (role config, live re-check, node-binding). **Audit → 0 Critical / 0 High / 0 Medium / 0 Low.**

## Test plan
- [x] `uv run pytest` green, **0 xfailed** (auth-audit demos are now passing regressions or removed).
- [x] New `tests/test_signing.py` (sign/verify round-trip + tamper/stale/wrong-node/pubkey-mismatch/missing-header/bad-version).
- [x] Signature negative/positive coverage in `tests/test_api.py`; A3.a/A3.b/A5.b re-expressed for signed requests.
- [x] `cancelchain db upgrade` + `db check` green against the regenerated migration; `ruff`/`mypy` clean.
- [x] Internal cross-model review converged.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Fill the PR number** — replace `#PRNUM` in the audit report + roadmap with the real number, commit, push:
```bash
sed -i 's/#PRNUM/#<actual>/g' docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(auth): fill impl PR number into audit report + roadmap"
git push
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 8: Acceptance (after the impl PR merges)

- [ ] **Step 1: Sync + confirm**

```bash
git checkout main && git pull --ff-only
git log --oneline -5
```

- [ ] **Step 2: Behavior on main**

```bash
grep -q "signing.verify" src/cancelchain/api.py && echo "ok: authorize verifies signatures"
grep -q "class TokenView" src/cancelchain/api.py && echo "FAIL: TokenView remains" || echo "ok: TokenView gone"
grep -q "class ApiToken" src/cancelchain/models.py && echo "FAIL: ApiToken remains" || echo "ok: ApiToken gone"
grep -nE "pyjwt|argon2-cffi" pyproject.toml && echo "FAIL: deps remain" || echo "ok: deps dropped"
ls docs/api-auth-protocol.md && echo "ok: protocol doc present"
```

- [ ] **Step 3: Suite + gates**

```bash
uv run pytest 2>&1 | tail -2          # green, 0 xfailed
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
rm -f /tmp/_ccacc.sqlite
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:////tmp/_ccacc.sqlite" uv run cancelchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:////tmp/_ccacc.sqlite" uv run cancelchain db check
```

- [ ] **Step 4: Docs**

```bash
grep -n "0 Critical / 0 High / 0 Medium / 0 Low\|0/0/0/0" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "cc-sig-v1|per-request" CLAUDE.md
```
Expected: audit `0/0/0/0`; CLAUDE.md describes the signature model; protocol doc present; deps gone; suite green with 0 xfailed.
