# Phase 5a — `pycryptodome` → `cryptography` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `pycryptodome` with `cryptography` (pyca) in `src/cancelchain/wallet.py`. After this plan completes, no `Crypto.*` imports remain anywhere in the codebase and `pycryptodome` is removed from `[project.dependencies]` and `uv.lock`.

**Architecture:** Single-file swap — every `pycryptodome` consumer lives in `wallet.py` (208 lines). Public `Wallet` API surface stays identical; internal RSA / AES / hash primitives swap out. Greenfield posture: no backward-compat shims, no migration tool, no preservation of pycryptodome's PKCS#1 DER format. The four downstream wire-shape commitments — public-key DER, address mill_hash, signature determinism, and JWT challenge ciphertext (persisted briefly in `ApiToken.cipher`, auto-refreshed every 60s on expiry, no production deploy to migrate) — are preserved by choosing standard cryptographic primitives (SubjectPublicKeyInfo DER, PKCS1v1.5+SHA384) that produce byte-identical output to pycryptodome for the same input.

**Tech Stack:** `cryptography>=44` (pyca, Rust-backed), `os.urandom` for nonces/session keys, `AES-GCM` for the symmetric AEAD (replaces pycryptodome's `AES-EAX`), `OAEP-SHA256` for asymmetric session-key wrapping (replaces pycryptodome's `OAEP-SHA1` default).

---

## Prerequisites

- Working directory: the cancelchain repo root (whatever path it lives at). Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 4 fully merged. Verify with `gh pr view 57 --json state --jq .state` → `MERGED`, and `grep -c 'marshmallow' pyproject.toml` → `0`.
- The branch `docs/phase-5a-design` exists locally with the design spec already committed. This plan adds the second commit on that branch and ships both as the docs PR.
- CI hard-gates `ruff check`, `ruff format --check`, and `mypy` (strict via `[tool.mypy] strict = true` in pyproject.toml; no CLI flag needed — `uv run mypy` honors the config).
- Test baseline: **205 passed, 1 skipped**. Phase 5a adds ~8-9 new tests, so the final count is ~213-214 passed, 1 skipped.
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-26-phase-5a-pycryptodome-to-cryptography.md` (this file) |
| 2 | impl PR | `pyproject.toml`, `src/cancelchain/wallet.py`, `tests/conftest.py`, `tests/test_wallet.py`, `uv.lock` |
| 3 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** Modify: nothing tracked yet. The design spec is already committed on `docs/phase-5a-design`. This task adds the implementation plan and ships them together.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-26-phase-5a-pycryptodome-to-cryptography-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/phase-5a-design`; spec file is tracked (the path is echoed back by `git ls-files`); commit count above main is `1`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-26-phase-5a-pycryptodome-to-cryptography.md
git status docs/superpowers/plans/
```

Expected: file exists, shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-26-phase-5a-pycryptodome-to-cryptography.md
git commit -m "$(cat <<'EOF'
docs(phase-5a): add Phase 5a pycryptodome → cryptography implementation plan

Spells out the single-PR impl: branch off main, swap wallet.py
internals, regenerate WALLET_PRIVATE_KEY_B58 fixture, add 8 new
round-trip / public-key-only tests, drop pycryptodome from deps +
mypy overrides + uv.lock.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/phase-5a-design
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/phase-5a-design --title "docs(phase-5a): Phase 5a pycryptodome → cryptography design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 5a design spec (\`docs/superpowers/specs/2026-05-26-phase-5a-pycryptodome-to-cryptography-design.md\`).
- Adds the Phase 5a implementation plan (\`docs/superpowers/plans/2026-05-26-phase-5a-pycryptodome-to-cryptography.md\`).
- No code changes.

Phase 5a ships as a single implementation PR after this docs PR lands. Single-file swap contained to \`src/cancelchain/wallet.py\` (greenfield posture — no backward-compat shims).

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Phase 5a impl — swap `wallet.py` to `cryptography`

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/cancelchain/wallet.py`
- Modify: `tests/conftest.py` (regenerate `WALLET_PRIVATE_KEY_B58`)
- Modify: `tests/test_wallet.py` (add 8 new tests)
- Modify: `uv.lock` (regenerated)

### Step 1: Branch off main

```bash
git checkout main && git pull --ff-only
git checkout -b feat/cryptography-swap
```

### Step 2: Update `pyproject.toml` dependencies

Edit `pyproject.toml`. In `[project] dependencies`, replace `"pycryptodome>=3.20",` with `"cryptography>=44",`. Position alphabetically — between `"celery>=5.4"` and `"click>=8.1.7"` (cryptography sorts before click).

Before:
```toml
  "celery>=5.4",
  "click>=8.1.7",
  ...
  "pg8000>=1.31",
  "pycryptodome>=3.20",
  "pydantic>=2.10",
```

After:
```toml
  "celery>=5.4",
  "cryptography>=44",
  "click>=8.1.7",
  ...
  "pg8000>=1.31",
  "pydantic>=2.10",
```

Drop the `[[tool.mypy.overrides]]` block at line 162-164:

Before:
```toml
[[tool.mypy.overrides]]
module = ["Crypto", "Crypto.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["base58check", "pymerkle", "pymerkle.*"]
ignore_missing_imports = true
```

After:
```toml
[[tool.mypy.overrides]]
module = ["base58check", "pymerkle", "pymerkle.*"]
ignore_missing_imports = true
```

(`cryptography` ships its own type stubs in the package — no override needed.)

### Step 3: Lock and install

```bash
uv lock --upgrade-package pycryptodome --upgrade-package cryptography
uv sync --group dev
uv run python -c "from importlib.metadata import version; print('cryptography', version('cryptography'))"
uv run python -c "import Crypto" 2>&1 | head -3
```

Expected:
- `cryptography 44.x.x` or newer.
- `ModuleNotFoundError: No module named 'Crypto'`.

If `uv lock` keeps pycryptodome around (because something else still depends on it), STOP and investigate — nothing else in this codebase should pull it in.

### Step 4: Rewrite `src/cancelchain/wallet.py`

Replace the entire contents of `src/cancelchain/wallet.py` with:

```python
from __future__ import annotations

import json
import os
from base64 import standard_b64decode, standard_b64encode
from collections.abc import Generator
from typing import Any

import base58check
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cancelchain.exceptions import InvalidKeyError, NoPrivateKeyError
from cancelchain.milling import mill_hash_bin

ADDRESS_TAG = 'CC'
KEY_SIZE = 2048
GCM_NONCE_SIZE = 12
AES_SESSION_KEY_SIZE = 16


def b58decode(s: str) -> bytes:
    return base58check.b58decode(s.encode())  # type: ignore[no-any-return]


def b58encode(b: bytes) -> str:
    return base58check.b58encode(b).decode()  # type: ignore[no-any-return]


def b64decode(s: str) -> bytes:
    return standard_b64decode(s.encode())


def b64encode(b: bytes) -> str:
    return standard_b64encode(b).decode()


def export_binary_key(key: Any, passphrase: str | None = None) -> bytes:
    if isinstance(key, RSAPublicKey):
        return key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    # RSAPrivateKey
    encryption: serialization.KeySerializationEncryption
    if passphrase is None:
        encryption = serialization.NoEncryption()
    else:
        encryption = serialization.BestAvailableEncryption(
            passphrase.encode()
        )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def import_key(ks: bytes | str, passphrase: str | None = None) -> Any | None:
    """Load an RSA key from PEM or DER bytes. Accepts both private and
    public keys (api.py / schema.py / models.py construct Wallet with
    a peer's public key alone for signature verification).
    """
    try:
        if isinstance(ks, str):
            ks = ks.encode()
        password = passphrase.encode() if passphrase is not None else None
        is_pem = b'-----BEGIN' in ks[:30]
        # Private-key path first (the common case for wallet load flows)
        try:
            if is_pem:
                return serialization.load_pem_private_key(ks, password)
            return serialization.load_der_private_key(ks, password)
        except Exception:
            pass
        # Public-key fallback (peer-public-key wrap path)
        if is_pem:
            return serialization.load_pem_public_key(ks)
        return serialization.load_der_public_key(ks)
    except Exception:
        return None


def import_b58_key(ks: str, passphrase: str | None = None) -> Any | None:
    try:
        return import_key(b58decode(ks), passphrase=passphrase)
    except Exception:
        return None


def import_b64_key(ks: str, passphrase: str | None = None) -> Any | None:
    try:
        return import_key(b64decode(ks), passphrase=passphrase)
    except Exception:
        return None


class Wallet:
    def __init__(
        self,
        b64ks: str | None = None,
        b58ks: str | None = None,
        ks: bytes | str | None = None,
        passphrase: str | None = None,
    ) -> None:
        if b64ks is not None:
            self.key: Any = import_b64_key(b64ks, passphrase=passphrase)
        elif b58ks is not None:
            self.key = import_b58_key(b58ks, passphrase=passphrase)
        elif ks is not None:
            self.key = import_key(ks, passphrase=passphrase)
        else:
            self.key = rsa.generate_private_key(
                public_exponent=65537, key_size=KEY_SIZE
            )
        if not (
            isinstance(self.key, (RSAPrivateKey, RSAPublicKey))
            and self.key.key_size == KEY_SIZE
        ):
            raise InvalidKeyError()

    @property
    def private_key(self) -> Any | None:
        return self.key if isinstance(self.key, RSAPrivateKey) else None

    @property
    def public_key(self) -> Any:
        return (
            self.private_key.public_key()
            if self.private_key is not None
            else self.key
        )

    @property
    def private_key_b58(self) -> str:
        return self.export_private_key_b58()

    @property
    def public_key_b64(self) -> str:
        return b64encode(export_binary_key(self.public_key))

    @property
    def address(self) -> str:
        aks = b58encode(
            mill_hash_bin(export_binary_key(self.public_key))
        )
        return f'{ADDRESS_TAG}{aks}{ADDRESS_TAG}'

    def export_private_key_pem(self, passphrase: str | None = None) -> bytes:
        if self.private_key is None:
            raise NoPrivateKeyError()
        encryption: serialization.KeySerializationEncryption
        if passphrase is None:
            encryption = serialization.NoEncryption()
        else:
            encryption = serialization.BestAvailableEncryption(
                passphrase.encode()
            )
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )

    def export_private_key_b58(self, passphrase: str | None = None) -> str:
        if self.private_key is None:
            raise NoPrivateKeyError()
        return b58encode(
            export_binary_key(self.private_key, passphrase=passphrase)
        )

    def sign(self, data: bytes) -> str:
        if self.private_key is None:
            raise NoPrivateKeyError()
        sig = self.private_key.sign(
            data, padding.PKCS1v15(), hashes.SHA384()
        )
        return b64encode(sig)

    def validate_signature(
        self, data: bytes, signature: str | None
    ) -> bool:
        if not (data and signature):
            return False
        try:
            self.public_key.verify(
                b64decode(signature),
                data,
                padding.PKCS1v15(),
                hashes.SHA384(),
            )
        except (InvalidSignature, ValueError, TypeError):
            # InvalidSignature: pyca raises this on a bad signature.
            # ValueError: bad b64 padding, malformed signature bytes.
            # TypeError: wrong types from caller.
            return False
        return True

    def encrypt(self, data: bytes) -> str:
        session_key = os.urandom(AES_SESSION_KEY_SIZE)
        enc_session_key = self.public_key.encrypt(
            session_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        nonce = os.urandom(GCM_NONCE_SIZE)
        ciphertext_with_tag = AESGCM(session_key).encrypt(
            nonce, data, None
        )
        return b64encode(enc_session_key + nonce + ciphertext_with_tag)

    def decrypt(self, msg: str) -> bytes:
        if self.private_key is None:
            raise NoPrivateKeyError()
        raw = b64decode(msg)
        key_size_bytes = self.private_key.key_size // 8
        enc_session_key = raw[:key_size_bytes]
        nonce = raw[key_size_bytes : key_size_bytes + GCM_NONCE_SIZE]
        ciphertext = raw[key_size_bytes + GCM_NONCE_SIZE :]
        session_key = self.private_key.decrypt(
            enc_session_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return AESGCM(session_key).decrypt(nonce, ciphertext, None)

    def to_dict(self) -> dict[str, str]:
        return {'private_key': self.private_key_b58}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_file(
        self, walletdir: str | None = None, passphrase: str | None = None
    ) -> str:
        filename = f'{self.address}.pem'
        if walletdir:
            filename = os.path.join(walletdir, filename)
        with open(filename, 'wb') as f:
            f.write(self.export_private_key_pem(passphrase=passphrase))
        return filename

    def __repr__(self) -> str:
        return f'Wallet({self.address})'

    __hash__: None = None  # type: ignore[assignment]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Wallet):
            return NotImplemented
        return bool(self.key == other.key)

    @classmethod
    def from_dict(cls, wallet_dict: dict[str, Any]) -> Wallet:
        return cls(b58ks=wallet_dict.get('private_key'))

    @classmethod
    def from_json(cls, wallet_json: str) -> Wallet:
        return cls.from_dict(json.loads(wallet_json))

    @classmethod
    def from_file(
        cls, filename: str, passphrase: str | None = None
    ) -> Wallet:
        with open(filename, 'rb') as f:
            return cls(ks=f.read(), passphrase=passphrase)
```

Notes:
- The `Generator` import is no longer used (the old `decrypt` had an inner `msg_parts` generator helper; the new `decrypt` uses straight slicing). Keep the import only if other code in the file references `Generator`. Check with `grep Generator src/cancelchain/wallet.py` after pasting — if no other references, ruff will flag it as unused and the formatter will remove it; or remove it manually. Same for any other unused imports (`base58check` IS still used; double-check).
- The `Crypto.*` imports are gone.
- `Wallet.key` is now an `Any` typed attribute that may hold either `RSAPrivateKey` or `RSAPublicKey`. The constructor's isinstance check validates both forms.

### Step 5: Verify the swap is clean

```bash
grep -n 'Crypto' src/cancelchain/wallet.py
grep -rn 'pycryptodome' src/cancelchain/
grep -rn 'Crypto\.' src/cancelchain/
```

Expected: all three return empty. Two separate `grep`s because POSIX `grep` treats `\b` as a literal backspace, not a word boundary — `Crypto\.` (with a literal dot) catches the import shapes pycryptodome uses (`from Crypto.Cipher`, `from Crypto.PublicKey`, etc.) without false positives on unrelated strings containing "Crypto".

### Step 6: Regenerate `WALLET_PRIVATE_KEY_B58` in `tests/conftest.py`

The fixture's b58-encoded private key was generated under pycryptodome's PKCS#1 DER format. The new code reads PKCS#1 DER input fine (cryptography's `load_der_private_key` handles both PKCS#1 and PKCS#8), but exports as PKCS#8 DER, so a round-trip through `Wallet.private_key_b58` produces a different string. To keep `test_create_from_key` passing, the constant in conftest.py must be regenerated to the new PKCS#8 DER b58 of the same underlying RSA key.

Run this one-shot script in the venv:

```bash
uv run python <<'PY'
from base58check import b58decode, b58encode
from cryptography.hazmat.primitives import serialization

# The OLD pycryptodome-era b58 (copy from current conftest.py).
# Open tests/conftest.py and read the multi-line string WALLET_PRIVATE_KEY_B58 = (...).
import re, pathlib
src = pathlib.Path('tests/conftest.py').read_text()
m = re.search(r'WALLET_PRIVATE_KEY_B58 = \((.+?)\)', src, re.DOTALL)
assert m, 'could not locate WALLET_PRIVATE_KEY_B58 literal'
literal = m.group(1)
old_b58 = ''.join(re.findall(r"'([^']+)'", literal))
print(f'old_b58 length: {len(old_b58)}')

# Decode to the old PKCS#1 DER bytes
old_der = b58decode(old_b58.encode())

# Load that key under cryptography (handles PKCS#1 DER fine)
key = serialization.load_der_private_key(old_der, password=None)

# Re-export as PKCS#8 DER
new_der = key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)

# Re-encode b58
new_b58 = b58encode(new_der).decode()
print(f'new_b58 length: {len(new_b58)}')
print('---')
print(new_b58)
PY
```

Expected: prints two lengths and the new b58 string. Copy that new b58 string.

Open `tests/conftest.py`. Find the `WALLET_PRIVATE_KEY_B58 = (...)` literal. Replace its contents with the new b58 string, formatted as a multi-line concatenation in the same style as the original (split into ~60-character chunks if you want to match the existing formatting; or one single string — ruff will reformat as needed).

### Step 7: Verify the fixture regeneration is correct

```bash
uv run python <<'PY'
from cancelchain.wallet import Wallet
import sys
sys.path.insert(0, 'tests')
from conftest import (
    WALLET_PRIVATE_KEY_B58,
    WALLET_PUBLIC_KEY_B64,
    WALLET_ADDRESS,
    WALLET_SIGNATURE_DATA,
    WALLET_SIGNATURE,
)

w = Wallet(b58ks=WALLET_PRIVATE_KEY_B58)
assert w.public_key_b64 == WALLET_PUBLIC_KEY_B64, (
    f'public_key_b64 mismatch:\n  got: {w.public_key_b64}\n  exp: {WALLET_PUBLIC_KEY_B64}'
)
assert w.address == WALLET_ADDRESS, (
    f'address mismatch:\n  got: {w.address}\n  exp: {WALLET_ADDRESS}'
)
sig = w.sign(WALLET_SIGNATURE_DATA.encode())
assert sig == WALLET_SIGNATURE, (
    f'signature mismatch:\n  got: {sig}\n  exp: {WALLET_SIGNATURE}'
)
# Round-trip the new b58 → re-export should match
assert w.private_key_b58 == WALLET_PRIVATE_KEY_B58, (
    'private_key_b58 round-trip mismatch'
)
print('OK — all 4 fixture invariants hold')
PY
```

Expected: `OK — all 4 fixture invariants hold`. If any assertion fails, the regeneration script in Step 6 went wrong — STOP and debug.

### Step 8: Add new tests to `tests/test_wallet.py`

The existing test file has 12 tests; we append 8 more.

Read the current end of `tests/test_wallet.py`:

```bash
tail -20 tests/test_wallet.py
```

Append the following test functions to the end of `tests/test_wallet.py`. Match the existing style (top-level functions, no class wrapping, fixtures from conftest where available):

```python


def test_wallet_address_round_trips_through_pem(tmp_path):
    """Freshly generated wallet → write PEM → read back → same address."""
    w1 = Wallet()
    path = w1.to_file(walletdir=str(tmp_path))
    w2 = Wallet.from_file(path)
    assert w1.address == w2.address


def test_wallet_address_round_trips_through_b58():
    """Freshly generated wallet → b58 → read back → same address."""
    w1 = Wallet()
    w2 = Wallet(b58ks=w1.private_key_b58)
    assert w1.address == w2.address


def test_wallet_sign_verify_happy_path():
    w = Wallet()
    sig = w.sign(b'hello world')
    assert w.validate_signature(b'hello world', sig) is True


def test_wallet_verify_rejects_mutated_payload():
    w = Wallet()
    sig = w.sign(b'hello world')
    assert w.validate_signature(b'hello WORLD', sig) is False


def test_wallet_verify_rejects_garbage_signature():
    w = Wallet()
    assert w.validate_signature(b'data', 'garbagebase64==') is False


def test_wallet_encrypt_decrypt_round_trip():
    w = Wallet()
    plaintext = b'session-challenge-payload'
    ciphertext = w.encrypt(plaintext)
    assert w.decrypt(ciphertext) == plaintext


def test_wallet_encrypted_pem_round_trip(tmp_path):
    """Encrypted PEM with a passphrase round-trips."""
    w1 = Wallet()
    path = w1.to_file(walletdir=str(tmp_path), passphrase='hunter2')
    w2 = Wallet.from_file(path, passphrase='hunter2')
    assert w1.address == w2.address


def test_wallet_public_key_only_constructs(wallet):
    """Wallet(b64ks=public_key_b64) accepts a peer's public key alone.

    Used by api.py / schema.py / models.py to wrap a remote party's
    public key for signature verification. Private operations
    (sign, decrypt, export_private_key_*) raise NoPrivateKeyError.
    """
    from cancelchain.exceptions import NoPrivateKeyError

    w = Wallet(b64ks=wallet.public_key_b64)
    assert w.private_key is None
    assert w.public_key is not None
    assert w.address == wallet.address
    # Public verify should still work
    sig = wallet.sign(b'data')
    assert w.validate_signature(b'data', sig) is True
    # Private operations raise
    with pytest.raises(NoPrivateKeyError):
        w.sign(b'data')
```

If the `from cancelchain.exceptions import NoPrivateKeyError` line is needed at module level (i.e., `NoPrivateKeyError` isn't already imported at the top of `tests/test_wallet.py`), hoist it to the top alongside the existing `from cancelchain.exceptions import InvalidKeyError` line. Verify with `grep -n 'NoPrivateKeyError' tests/test_wallet.py` before deciding — if it shows up only inside the new function, hoist it.

### Step 9: Verify all gates

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

All four must exit 0. Test count: 205 → ~213 (8 new tests).

If `mypy` reports new errors in `wallet.py`, the most common cause is the `Any`-typed `self.key` attribute interacting with method calls. The intent is that `Wallet.key` is `Any` throughout so callers don't need to import RSAPrivateKey/RSAPublicKey types. If a specific line type-errors, add a narrow `# type: ignore` rather than tightening the type. Aim for ≤1 ignore in the new code; if you need more, something's wrong — investigate before suppressing.

If `pytest` fails on:
- `test_create_from_key` — the fixture regeneration was wrong. Re-run Step 7's assertion script and inspect the mismatch.
- `test_crypto` (decrypt with wrong wallet) — that test asserts `with pytest.raises(ValueError)`. AES-GCM's `AESGCM.decrypt` on a wrong key raises `InvalidTag`, which is a subclass of `cryptography.exceptions.InvalidTag` (NOT `ValueError`). The test may need its `pytest.raises` argument widened. Check with `uv run pytest tests/test_wallet.py::test_crypto -v` and inspect the error; if it raises `InvalidTag`, update the test to `pytest.raises((ValueError, InvalidTag))` and add `from cryptography.exceptions import InvalidTag` to the test file imports. Document this in the commit message.

### Step 10: Commit

```bash
git add pyproject.toml uv.lock src/cancelchain/wallet.py tests/conftest.py tests/test_wallet.py
git commit -m "$(cat <<'EOF'
feat(deps): swap pycryptodome → cryptography in wallet.py

Phase 5a. Single-file swap. Greenfield posture (per
project-no-legacy-chain memory): no backward-compat shims, no
migration tool.

src/cancelchain/wallet.py:
- 5 Crypto.* imports replaced with cryptography hazmat primitives:
  rsa, padding, hashes, serialization, AESGCM, InvalidSignature.
- RSA.generate(2048) → rsa.generate_private_key(public_exponent=65537,
  key_size=2048).
- RSA.import_key auto-detect → explicit PEM-vs-DER sniff (bytes
  startswith '-----BEGIN' in first 30B) followed by private-key
  loader fallthrough to public-key loader (callers in api.py,
  schema.py, models.py construct Wallet(b64ks=public_key_b64) for
  signature verification against peer keys).
- PKCS1_v1_5+SHA384 sign/verify → private_key.sign(data,
  padding.PKCS1v15(), hashes.SHA384()) / public_key.verify(...). The
  verify path raises InvalidSignature on failure; wrap in try/except
  (InvalidSignature, ValueError, TypeError) to preserve the
  return-bool contract.
- PKCS1_OAEP (SHA-1 default) → padding.OAEP(mgf=MGF1(SHA256()),
  algorithm=SHA256(), label=None). Stronger hash; no compat constraint.
- AES.MODE_EAX → AESGCM. EAX is not in pyca/cryptography. JWT
  challenge ciphertexts ARE persisted in ApiToken.cipher (DB column)
  but only for up to 60s, after which ApiToken.expired triggers
  refreshed_cipher() to regenerate. Greenfield (no production DB),
  so the persistence window is irrelevant for migration; even if
  deployed, the cleanup is automatic on the next handshake. The
  wire-format change is safe. New layout: enc_session_key (256B) ||
  nonce (12B) || ciphertext_with_appended_tag.
- Crypto.Random.get_random_bytes(N) → os.urandom(N).
- Private-key DER and PEM serialization both switch to PKCS#8.
- Encrypted PEM uses BestAvailableEncryption (PBKDF2-SHA256/AES-256-CBC).

pyproject.toml:
- "pycryptodome>=3.20" removed from [project.dependencies].
- "cryptography>=44" added in alphabetical position.
- [[tool.mypy.overrides]] block for module = ["Crypto", "Crypto.*"]
  removed (cryptography ships type stubs).

tests/conftest.py:
- WALLET_PRIVATE_KEY_B58 regenerated under the new PKCS#8 DER format
  (the underlying RSA key value is unchanged). The other four WALLET_*
  constants (WALLET_PUBLIC_KEY_B64, WALLET_ADDRESS, WALLET_SIGNATURE_DATA,
  WALLET_SIGNATURE) stay byte-identical — SubjectPublicKeyInfo DER and
  PKCS1v1.5+SHA384 are deterministic across both libraries.

tests/test_wallet.py:
- 8 new tests cover address/PEM/b58 round-trips, sign-verify happy and
  tamper-rejection paths, encrypt-decrypt round-trip, encrypted-PEM
  round-trip, and the public-key-only construct path (verifies that
  Wallet(b64ks=peer_pub_b64) accepts the input, exposes public_key,
  returns None for private_key, and raises NoPrivateKeyError on
  sign/decrypt while still verifying peer signatures).

Test count: 205 → ~213.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 11: Push and open PR

```bash
git push -u origin feat/cryptography-swap
gh pr create --base main --title "feat(deps): swap pycryptodome → cryptography in wallet.py" --body "$(cat <<'EOF'
## Summary
- Replaces pycryptodome with pyca/cryptography in \`src/cancelchain/wallet.py\` (single-file swap).
- Drops \`pycryptodome>=3.20\` from \`[project.dependencies]\`, adds \`cryptography>=44\`.
- Drops the \`[[tool.mypy.overrides]]\` block for the \`Crypto\` module (cryptography ships type stubs).
- AES-EAX → AES-GCM (pyca doesn't support EAX; JWT challenge ciphertexts aren't persisted so wire-format change is safe).
- OAEP hash defaults switch from SHA-1 to SHA-256.
- Private-key DER and PEM serialization both switch to PKCS#8 standard format.
- Encrypted PEM uses BestAvailableEncryption (PBKDF2-SHA256 + AES-256-CBC).
- 8 new tests in \`tests/test_wallet.py\` covering round-trips, sign/verify happy + reject paths, encrypt/decrypt, encrypted-PEM, and public-key-only Wallet construction.
- \`WALLET_PRIVATE_KEY_B58\` fixture regenerated under PKCS#8 DER (the underlying RSA key is unchanged; the other 4 WALLET_* fixtures stay byte-identical).

**Greenfield posture** (per \`project-no-legacy-chain\` memory): no backward-compat shims, no migration tool. No persisted wallet \`.pem\` files or in-flight JWT challenges to preserve.

Phase 5a. Spec/plan merged in the preceding docs PR.

## Test plan
- [x] \`uv run mypy\` exits 0.
- [x] \`uv run pytest\` passes (205 → ~213).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [x] \`uv run python -c "import Crypto"\` raises ModuleNotFoundError.
- [x] \`grep -rn 'pycryptodome' src/cancelchain/\` and \`grep -rn 'Crypto\.' src/cancelchain/\` both return nothing.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 12: Stop — controller handles wor + mwg + sync

---

## Task 3: Phase 5a acceptance verification

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

- [ ] **Step 3: pycryptodome absent**

```bash
grep -rn 'pycryptodome' src/cancelchain/
grep -rn 'Crypto\.' src/cancelchain/
grep -c pycryptodome pyproject.toml
grep -ci pycryptodome uv.lock
uv run python -c "import Crypto" 2>&1 | head -3
```

Expected: first four grep checks return nothing / 0; the import attempt raises `ModuleNotFoundError`. Two separate `grep`s because POSIX `grep` treats `\b` as a literal backspace, not a word boundary.

- [ ] **Step 4: cryptography present**

```bash
uv run python -c "from importlib.metadata import version; print('cryptography', version('cryptography'))"
uv run python -c "from cryptography.hazmat.primitives.asymmetric import rsa; print(rsa)"
```

Expected: prints `cryptography 44.x.x` and the rsa module repr.

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

Expected: both print `213 passed, 1 skipped` (or whatever the new count is — should be 8 more than 205).

- [ ] **Step 7: CLI smoke**

```bash
uv run cancelchain --help
```

Expected: prints the full command tree.

- [ ] **Step 8: Wallet round-trip smoke**

```bash
uv run python <<'PY'
from cancelchain.wallet import Wallet
w1 = Wallet()
print('generated address:', w1.address)
b58 = w1.private_key_b58
w2 = Wallet(b58ks=b58)
assert w1.address == w2.address
print('b58 round-trip OK')
sig = w1.sign(b'hello')
assert w1.validate_signature(b'hello', sig) is True
assert w1.validate_signature(b'world', sig) is False
print('sign/verify OK')
ct = w1.encrypt(b'secret')
assert w1.decrypt(ct) == b'secret'
print('encrypt/decrypt OK')
PY
```

Expected: prints `generated address: CC...CC`, then `b58 round-trip OK`, then `sign/verify OK`, then `encrypt/decrypt OK`.

- [ ] **Step 9: Docker build smoke**

```bash
docker build --target builder -t cc-phase5a-final .
```

Expected: succeeds.

- [ ] **Step 10: Acceptance complete**

If Steps 1–9 all pass, Phase 5a is done. No commit.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 2) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory).
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend) and ask the user to click "Re-request review" in the PR sidebar — Copilot's auto-review doesn't fire on subsequent fix pushes consistently; the UI click is the only reliable trigger.

---

## Risks and watchpoints

### Risk: `test_crypto` may break on the AES-GCM exception type

The existing `tests/test_wallet.py::test_crypto` does:
```python
with pytest.raises(ValueError):
    wallet2.decrypt(msg)
```

Under pycryptodome's AES-EAX, `decrypt_and_verify` raised `ValueError` on a bad tag (wrong key). Under cryptography's AES-GCM, `AESGCM.decrypt` raises `cryptography.exceptions.InvalidTag` — which is NOT a subclass of `ValueError`. This test will fail.

Fix in Step 9: widen the `pytest.raises` to a tuple:
```python
from cryptography.exceptions import InvalidTag

with pytest.raises((ValueError, InvalidTag)):
    wallet2.decrypt(msg)
```

Or, even cleaner: change to `with pytest.raises(InvalidTag):` since we now know the precise exception type. Document the change in the commit message under "tests/test_wallet.py:".

### Risk: AES-GCM wire-layout offset off-by-one

The `decrypt` slicing in Step 4's code assumes:
- `enc_session_key`: first `key_size_bytes` (256) bytes
- `nonce`: next 12 bytes (`raw[256:268]`)
- `ciphertext_with_tag`: everything from byte 268 onward

`AESGCM.encrypt(nonce, data, None)` returns `ciphertext || tag` (16-byte GCM tag appended). `AESGCM.decrypt` takes the same ciphertext-plus-tag blob and the original nonce. So the slicing is correct *only if* `encrypt` returns the same layout I'm slicing for in `decrypt`. Verify with the round-trip test in Step 9 (`test_wallet_encrypt_decrypt_round_trip`); if it fails, inspect the actual byte lengths via `len(raw)`, `key_size_bytes`, etc.

### Risk: `Generator` import becomes unused

The old `decrypt` had an inner `msg_parts(key_size, raw) -> Generator[bytes, None, None]` helper. The new `decrypt` uses straight slicing. Remove `from collections.abc import Generator` if no other code in `wallet.py` uses it. Ruff will flag this and `uv run ruff check` will fail — fix by removing the import.

### Risk: hardcoded `Wallet.size_in_bytes()` reference somewhere outside wallet.py

`grep -rn 'size_in_bytes\|size_in_bits' src/ tests/` — should return empty (these are pycryptodome methods that don't exist on cryptography keys). If any external code accessed `wallet.private_key.size_in_bytes()`, it would break silently with a `AttributeError`. Verify pre-implementation.

### Risk: `Wallet.__eq__` uses `self.key == other.key`

pyca/cryptography RSA key objects implement `__eq__` against same-type keys. For `RSAPrivateKey == RSAPrivateKey`, equality compares the underlying key material — preserves the existing contract. For `RSAPrivateKey == RSAPublicKey` (mixed-type), pyca returns NotImplemented and Python falls back to identity comparison (False). This matches the spirit of the prior pycryptodome behavior; verify with `test_eq` from the existing test file (passes if the contract holds).

### Risk: `mypy --strict` complaining about `Any`-typed key

The `self.key: Any` declaration plus the `isinstance` runtime check at the constructor satisfies strict mypy for most uses. The properties `private_key: Any | None` and `public_key: Any` keep the broad type at the boundary so external callers don't have to import pyca types. If mypy still complains on a specific method (e.g., `sign` calling `self.private_key.sign(...)`), the issue is that mypy can't narrow `Any | None` after the `if self.private_key is None: raise` check — assign to a local first:
```python
key = self.private_key
if key is None:
    raise NoPrivateKeyError()
sig = key.sign(data, padding.PKCS1v15(), hashes.SHA384())
```

If mypy still complains, narrow with `# type: ignore[no-any-return]` or similar at the specific call site. ≤2 ignores in the new code is acceptable; more than that and the type design needs rethinking.
