# Phase 5a — `pycryptodome` → `cryptography`

**Status:** Draft for review
**Date:** 2026-05-26
**Scope:** Replace the pycryptodome RSA / AES / hash primitives in `src/cancelchain/wallet.py` with their pyca/`cryptography` equivalents. After this phase: no `Crypto.*` imports anywhere in the codebase, and `pycryptodome` is no longer in `[project.dependencies]` or `uv.lock`.

## Goal

Modernize the cryptographic primitives onto pyca/`cryptography` — the Rust-backed library at the center of the Python crypto ecosystem. Drops `pycryptodome` (less actively maintained, no longer the de facto standard), and aligns the project with the broader Python ecosystem.

**Greenfield posture.** No legacy wallet `.pem` files exist that need to round-trip across this swap, no existing JWT-handshake ciphertexts need to be readable, no addresses need to match what pycryptodome would have produced for the same RSA key. Library swaps can pick the modern format/algorithm freely. Same-version determinism still applies — a freshly generated wallet must round-trip in this codebase, and the existing test suite must still pass.

## Non-goals

- **No async crypto.** RSA + AES are CPU-bound and the per-call latency doesn't warrant an async API.
- **No replacing `base58check`.** Unrelated dep, used for address-tag encoding.
- **No changing `KEY_SIZE = 2048`.** RSA-2048 stays.
- **No typing the `Wallet.key` / `.public_key` / `.private_key` attributes beyond `Any`.** Tightening to `rsa.RSAPrivateKey` / `rsa.RSAPublicKey` would force callers to import from `cryptography` to satisfy mypy strict at every consumer. Deferred to a separate refactor (Phase 6 or later).
- **No `__hash__` / `__eq__` cleanup.** The current `__hash__: None = None` ergonomics and the `__eq__` that compares the underlying key object both stay as-is. pyca/cryptography's RSA key objects implement `__eq__` against other key objects, so the equality semantic is preserved.
- **No backward compatibility shims.** Greenfield project (no production deploy, no legacy chain, no shipped wallets). No migration tool, no encrypted-PEM compat reader, no AES-EAX fallback for old in-flight challenges (they don't exist).
- **No `Wallet.to_dict` / `Wallet.from_dict` JSON-shape change.** The b58-encoded private key payload stays the same wire shape (binary key bytes → base58check), though the binary key bytes themselves shift from pycryptodome's DER serialization to cryptography's PKCS#8 DER serialization.

## Decisions taken during brainstorming

- **Scope: single-file swap.** All pycryptodome usage lives in `wallet.py` (208 lines). No other source consumer. One PR.
- **Symmetric cipher: AES-GCM** replaces pycryptodome's AES-EAX. pyca/cryptography does not support EAX. The JWT challenge ciphertext IS persisted briefly in `ApiToken.cipher` (DB column) — up to 60 seconds, since `ApiToken.expired` triggers `refreshed_cipher()` to regenerate a fresh challenge after that timeout. Greenfield project (no production deploy, no existing DB), so the persistence window is irrelevant for migration purposes; even in a hypothetical deployed scenario the worst case is 60s of unreadable challenges that auto-resolve on the next refresh. The wire-format change is safe.
- **OAEP hash: SHA-256.** pycryptodome's `PKCS1_OAEP.new` defaulted to SHA-1 for both MGF1 and the algorithm. With no compat constraint, use SHA-256 — stronger, modern.
- **Private-key PEM format: PKCS#8.** pycryptodome wrote PKCS#1 TraditionalOpenSSL for unencrypted private keys (the `pkcs=1` default in `export_private_key_pem`). Switch to PKCS#8 universally — the modern standard. The wire shape on disk changes from `-----BEGIN RSA PRIVATE KEY-----` to `-----BEGIN PRIVATE KEY-----` (or `-----BEGIN ENCRYPTED PRIVATE KEY-----` when a passphrase is supplied).
- **Encrypted PEM uses `BestAvailableEncryption`.** pyca/cryptography's wrapper picks PBKDF2-SHA256 + AES-256-CBC (the recommended PKCS#8 default). The current `wallet.py` explicitly configures `protection='scryptAndAES128-CBC'` (scrypt KDF + AES-128). AES-128 is still cryptographically strong; switching to AES-256 just gives a larger security margin and uses cryptography's recommended default scheme.
- **Random bytes: `os.urandom`.** pycryptodome's `Crypto.Random.get_random_bytes(16)` becomes the stdlib `os.urandom(16)`. Same security guarantees (both backed by `/dev/urandom` on Linux, `BCryptGenRandom` on Windows). Drops one library function.
- **Format sniffing on `import_key`.** pycryptodome's `RSA.import_key` auto-detected PEM vs DER. pyca/cryptography requires the caller to pick: `load_pem_private_key(data, password)` vs `load_der_private_key(data, password)`. Sniff: if `b'-----BEGIN'` appears within the first 30 bytes of input, route to PEM; else DER. On any exception (including format mismatch), return None — preserves the `import_key` contract.

## Changes

### Files

- Modify: `src/cancelchain/wallet.py`
- Modify: `pyproject.toml` (drop `pycryptodome>=3.20`, add `cryptography>=44`)
- Modify: `uv.lock` (regenerated)
- Modify: `tests/conftest.py` (regenerate the hardcoded `WALLET_PRIVATE_KEY_B58` constant — see "Test fixture regeneration" below)
- Modify: `tests/test_wallet.py` (already exists with 12 tests; append 8 new round-trip / public-key-only tests)

### New imports in `wallet.py`

```python
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidSignature
```

Drop:
```python
import Crypto.Random
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA384
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
```

### Function-by-function mapping

#### Key generation (constructor's `else` branch)

```python
# Before:
self.key = RSA.generate(KEY_SIZE)
# After:
self.key = rsa.generate_private_key(
    public_exponent=65537, key_size=KEY_SIZE
)
```

#### `import_key(ks, passphrase)` — format-sniffing helper

Must accept **both private and public** keys. `api.py`, `schema.py`, and `models.py` all call `Wallet(b64ks=public_key_b64)` to wrap a remote party's public key for signature verification. Strategy: try the private-key loaders first (since they're the common case in CLI / wallet-load flows); on failure, fall back to public-key loaders. Return None if neither path works.

```python
def import_key(ks: bytes | str, passphrase: str | None = None) -> Any | None:
    try:
        if isinstance(ks, str):
            ks = ks.encode()
        password = passphrase.encode() if passphrase is not None else None
        is_pem = b'-----BEGIN' in ks[:30]
        # Private-key path first
        try:
            if is_pem:
                return serialization.load_pem_private_key(ks, password)
            return serialization.load_der_private_key(ks, password)
        except Exception:
            pass
        # Public-key fallback (used by api.py / schema.py callers that
        # only have a peer's public key in hand)
        if is_pem:
            return serialization.load_pem_public_key(ks)
        return serialization.load_der_public_key(ks)
    except Exception:
        return None
```

The passphrase is only meaningful on the private path; public keys aren't encrypted at rest in this codebase.

#### `export_binary_key(key, passphrase)`

```python
def export_binary_key(key: Any, passphrase: str | None = None) -> bytes:
    if isinstance(key, RSAPublicKey):
        return key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    # else RSAPrivateKey
    encryption: serialization.KeySerializationEncryption
    if passphrase is None:
        encryption = serialization.NoEncryption()
    else:
        encryption = serialization.BestAvailableEncryption(passphrase.encode())
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
```

#### `Wallet.public_key` / `Wallet.private_key`

```python
@property
def private_key(self) -> Any | None:
    return self.key if isinstance(self.key, RSAPrivateKey) else None

@property
def public_key(self) -> Any:
    return (
        self.private_key.public_key() if self.private_key is not None
        else self.key
    )
```

(pyca keys don't have `has_private()`. Check isinstance instead.)

#### `Wallet.export_private_key_pem(passphrase)`

```python
def export_private_key_pem(self, passphrase: str | None = None) -> bytes:
    if self.private_key is None:
        raise NoPrivateKeyError()
    encryption: serialization.KeySerializationEncryption
    if passphrase is None:
        encryption = serialization.NoEncryption()
    else:
        encryption = serialization.BestAvailableEncryption(passphrase.encode())
    return self.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
```

#### `Wallet.sign(data)`

```python
def sign(self, data: bytes) -> str:
    if self.private_key is None:
        raise NoPrivateKeyError()
    sig = self.private_key.sign(data, padding.PKCS1v15(), hashes.SHA384())
    return b64encode(sig)
```

#### `Wallet.validate_signature(data, signature)`

```python
def validate_signature(self, data: bytes, signature: str | None) -> bool:
    if not (data and signature):
        return False
    try:
        self.public_key.verify(
            b64decode(signature),
            data,
            padding.PKCS1v15(),
            hashes.SHA384(),
        )
    except (InvalidSignature, binascii.Error, ValueError, TypeError):
        # InvalidSignature: pyca raises this on a bad signature.
        # binascii.Error: malformed base64 (bad padding, non-b64 chars).
        #   It's a subclass of ValueError in Python 3 so the ValueError
        #   catch would cover it too — listing it explicitly clarifies
        #   intent for readers.
        # ValueError: bad-length signature bytes after b64decode.
        # TypeError: wrong types from caller.
        return False
    return True
```

(Add `import binascii` at the top of `wallet.py` alongside the other stdlib imports.)

`cryptography`'s `verify` raises on failure rather than returning False — translate at the call site. The catch narrows to the documented failure modes; an unexpected exception (e.g., system-level) propagates so we don't accidentally swallow real bugs.

#### `Wallet.encrypt(data)`

```python
def encrypt(self, data: bytes) -> str:
    session_key = os.urandom(16)
    enc_session_key = self.public_key.encrypt(
        session_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    nonce = os.urandom(12)
    ciphertext_with_tag = AESGCM(session_key).encrypt(nonce, data, None)
    return b64encode(enc_session_key + nonce + ciphertext_with_tag)
```

Wire layout: `enc_session_key (256B) || nonce (12B) || ciphertext_with_appended_tag`.

#### `Wallet.decrypt(msg)`

```python
def decrypt(self, msg: str) -> bytes:
    if self.private_key is None:
        raise NoPrivateKeyError()
    raw = b64decode(msg)
    key_size_bytes = self.private_key.key_size // 8
    enc_session_key = raw[:key_size_bytes]
    nonce = raw[key_size_bytes:key_size_bytes + 12]
    ciphertext = raw[key_size_bytes + 12:]
    session_key = self.private_key.decrypt(
        enc_session_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return AESGCM(session_key).decrypt(nonce, ciphertext, None)
```

Note: pycryptodome had `private_key.size_in_bytes()` returning 256 for RSA-2048. pyca uses `key_size // 8`.

#### Constructor validation

The check must accept **both** private and public keys at the requested size. Multiple call sites (api.py, schema.py, models.py) construct `Wallet(b64ks=public_key_b64)` to wrap a remote party's public key for verification.

```python
# Before:
if not (self.key and self.key.size_in_bits() == KEY_SIZE):
    raise InvalidKeyError()
# After:
if not (
    isinstance(self.key, (RSAPrivateKey, RSAPublicKey))
    and self.key.key_size == KEY_SIZE
):
    raise InvalidKeyError()
```

Both `RSAPrivateKey` and `RSAPublicKey` expose `.key_size` as an `int` (matches pycryptodome's `size_in_bits()` semantics).

### `pyproject.toml` changes

```diff
-  "pycryptodome>=3.20",
+  "cryptography>=44",
```

The `[[tool.mypy.overrides]]` block for `Crypto`/`Crypto.*` is present in `pyproject.toml` at lines 162–164 (confirmed via grep). It becomes unused — `cryptography` ships type stubs — and should be deleted:

```diff
-[[tool.mypy.overrides]]
-module = ["Crypto", "Crypto.*"]
-ignore_missing_imports = true
```

### Test fixture regeneration

`tests/conftest.py` has 5 hardcoded `WALLET_*` constants used by `tests/test_wallet.py`'s `test_create_from_key`, `test_sign`, etc. The relationship:

| Constant | What | Changes under cryptography? |
|---|---|---|
| `WALLET_PRIVATE_KEY_B58` | b58check encoding of `export_binary_key(private)` | **Yes** — PKCS#1 DER → PKCS#8 DER. The underlying RSA key value stays the same; only the DER envelope changes. **Regenerate.** |
| `WALLET_PUBLIC_KEY_B64` | b64 of `export_binary_key(public)` (SubjectPublicKeyInfo DER) | No — both libraries produce byte-identical SubjectPublicKeyInfo DER. |
| `WALLET_ADDRESS` | `'CC' + b58(mill_hash(public_key_der)) + 'CC'` | No (depends only on public DER, unchanged). |
| `WALLET_SIGNATURE_DATA` | `'helloworld'` literal | No. |
| `WALLET_SIGNATURE` | b64 of `PKCS1v1.5(SHA384(data))` signature | No (PKCS1v1.5 + SHA384 is deterministic given the same private key). |

Implementer regenerates `WALLET_PRIVATE_KEY_B58` by:
1. Loading the **old** WALLET_PRIVATE_KEY_B58 string through the new cryptography-based `Wallet(b58ks=...)` actually succeeds (cryptography reads PKCS#1 DER fine — it just writes PKCS#8 by default). The failure mode is round-trip mismatch: `Wallet(b58ks=OLD).private_key_b58 != OLD` because the re-export writes PKCS#8. The fixture assertion `wallet.private_key_b58 == wallet_private_key_b58` is what fails, not the constructor.
2. Cleanest path: take the old b58 → b58-decode → load as PKCS#1 DER using `serialization.load_der_private_key` → re-export via `private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())` → base58check-encode → that's the new WALLET_PRIVATE_KEY_B58.
3. Verify: `Wallet(b58ks=NEW_WALLET_PRIVATE_KEY_B58).public_key_b64 == WALLET_PUBLIC_KEY_B64` and `.address == WALLET_ADDRESS` and `.sign('helloworld'.encode()) == WALLET_SIGNATURE` all hold. If any of these don't hold, the regeneration is wrong.

A small inline script in the implementer's terminal does this regeneration once; the resulting string is hardcoded into `tests/conftest.py`. The other 4 WALLET_* constants stay untouched.

### Tests

Add to `tests/test_wallet.py` (already exists with 12 tests):

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

    Requires `from cancelchain.exceptions import NoPrivateKeyError`
    in the test file's imports (hoist alongside the existing
    InvalidKeyError import if not already present).
    """
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

(The existing `tests/test_wallet.py::test_create_invalid_key` already covers the `Wallet(b64ks='foo')` / `Wallet(b58ks='foo')` / `Wallet(ks='foo')` error paths — no extra invalid-key test needed in PR-5a.)

Test count: 205 → 213 (8 new tests). The existing `test_crypto` may also need its `pytest.raises(ValueError)` widened to accept `cryptography.exceptions.InvalidTag` — see Risks below.

## Acceptance

- `grep -rn 'pycryptodome' src/cancelchain/` returns nothing AND `grep -rn 'Crypto\.' src/cancelchain/` returns nothing (or only docstring references). Two greps because `\b` isn't a portable word-boundary in POSIX grep.
- `grep -i pycryptodome uv.lock` returns nothing.
- `uv run python -c "import Crypto"` raises `ModuleNotFoundError`.
- `uv run mypy` exits 0.
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run pytest` exits 0; test count grows by 8 (205 → 213).
- `uv run cancelchain --help` works.
- `docker build --target builder -t cc-phase5a .` succeeds.

## Risks

- **AES-GCM-vs-EAX semantic difference.** The output ciphertext layout differs (12-byte nonce vs 16-byte nonce; integrated tag vs separate). Internal callers in `api.py` (encrypts challenge) and `api_client.py` (decrypts challenge) both call through `Wallet.encrypt` / `Wallet.decrypt`, which both move atomically in this PR. Server and client never speak across versions.
- **`InvalidSignature` exception type.** The `validate_signature` catch needs to catch the right exception. The new code uses `except (InvalidSignature, binascii.Error, ValueError, TypeError)` — narrow and explicit. `binascii.Error` is technically a subclass of `ValueError` in Python 3 (so the `ValueError` catch would suffice), but listing it explicitly makes the b64-decode failure path obvious to readers. The function's documented contract is "return True iff valid".
- **Address mismatch from a DER format wobble.** Vanishingly unlikely — `SubjectPublicKeyInfo` DER is a deterministic ASN.1 sequence; both pycryptodome and pyca produce byte-identical output for the same RSA key. `tests/conftest.py` hardcodes `WALLET_ADDRESS` (the b58 of `mill_hash(public_key_der)` for a specific stored RSA key); since the public-DER format is invariant across libraries, `WALLET_ADDRESS` should NOT need regeneration. Verify by running the Step 7 fixture-invariant script (see the plan) before declaring done — it asserts `Wallet(b58ks=NEW_WALLET_PRIVATE_KEY_B58).address == WALLET_ADDRESS`. If that assertion fails, the public-DER format DID drift and the address constant needs updating too; investigate before regenerating blindly.

## Open decisions

None at design time. The brainstorming round resolved:
- Single vs split PRs → single (contained to one file).
- EAX → GCM is acceptable.
- Encrypted PEM backward compat → no constraint.

## Translation reference (quick lookup for the implementer)

| Concept | pycryptodome | cryptography (pyca) |
|---|---|---|
| Generate RSA key | `RSA.generate(2048)` | `rsa.generate_private_key(public_exponent=65537, key_size=2048)` |
| Has private? | `key.has_private()` | `isinstance(key, RSAPrivateKey)` |
| Public key from private | `private.public_key()` | `private.public_key()` (same name!) |
| Key size (bits) | `key.size_in_bits()` | `key.key_size` |
| Key size (bytes) | `key.size_in_bytes()` | `key.key_size // 8` |
| Random bytes | `Crypto.Random.get_random_bytes(N)` | `os.urandom(N)` |
| Sign RSA-PKCS1v1.5-SHA384 | `PKCS1_v1_5.new(k).sign(SHA384.new(d))` | `k.sign(d, padding.PKCS1v15(), hashes.SHA384())` |
| Verify (bool) | `PKCS1_v1_5.new(k).verify(SHA384.new(d), sig)` | `try: k.verify(sig, d, padding.PKCS1v15(), hashes.SHA384()); except InvalidSignature: ...` |
| Encrypt RSA-OAEP | `PKCS1_OAEP.new(k).encrypt(data)` | `k.encrypt(data, padding.OAEP(mgf=MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))` |
| Decrypt RSA-OAEP | `PKCS1_OAEP.new(k).decrypt(ct)` | `k.decrypt(ct, padding.OAEP(...))` (same OAEP() instance shape) |
| AES-GCM encrypt | `AES.new(key, MODE_EAX); ciphertext, tag = aes.encrypt_and_digest(data); nonce = aes.nonce` | `nonce = os.urandom(12); ct_with_tag = AESGCM(key).encrypt(nonce, data, None)` |
| AES-GCM decrypt | `AES.new(key, MODE_EAX, nonce).decrypt_and_verify(ct, tag)` | `AESGCM(key).decrypt(nonce, ct_with_tag, None)` |
| Load PEM private key | `RSA.import_key(pem_bytes, passphrase=...)` | `serialization.load_pem_private_key(pem_bytes, password)` |
| Load DER private key | `RSA.import_key(der_bytes, passphrase=...)` | `serialization.load_der_private_key(der_bytes, password)` |
| Export DER public | `key.export_key(format='DER')` | `key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)` |
| Export PEM private (PKCS#8, unencrypted) | `key.export_key(pkcs=1)` (Trad. OpenSSL — but we switch to PKCS#8) | `key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())` |
| Export PEM private (PKCS#8, encrypted) | `key.export_key(pkcs=8, passphrase=..., protection='scryptAndAES128-CBC')` | `key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase.encode()))` |
