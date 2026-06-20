# Ed25519 Adversarial Test Vector Fixtures

Static vendored fixtures for `tests/test_ed25519_vectors.py`. Never fetched at
test time.

## speccheck_cases.json

- **Source repo:** `novifinancial/ed25519-speccheck`
- **Path:** `cases.json`
- **Commit SHA pinned:** `65519336fda78a3d016e947df6d82848aca0c9da`
- **Fetched via:**
  `gh api repos/novifinancial/ed25519-speccheck/contents/cases.json --jq .content | base64 -d`

12 edge-case vectors exercising small-order A/R, mixed-order A/R (cofactored
vs cofactorless distinguishers), S≥L, and non-canonical point encodings.
JSON schema: array of objects with `message`, `pub_key`, `signature` (all hex
strings, no `0x` prefix).

## wycheproof_ed25519.json

- **Source repo:** `C2SP/wycheproof`
- **Path:** `testvectors_v1/ed25519_test.json`
- **Repo HEAD commit SHA pinned:** `6d7cccd0fcb1917368579adeeac10fe802f1b521`
- **File blob SHA:** `17cfb05dae4a351777d9e08e46095d81659b10b4`
- **Fetched via:**
  `gh api repos/C2SP/wycheproof/contents/testvectors_v1/ed25519_test.json --jq .content | base64 -d`

150 test vectors across 77 test groups. JSON schema:
- Top level: `{ algorithm, schema, numberOfTests, header, notes, testGroups }`
- `testGroups[i]`: `{ type, source, publicKey: { type, curve, keySize, pk }, publicKeyDer, publicKeyPem, publicKeyJwk, tests }`
- `tests[j]`: `{ tcId, comment, flags, msg, sig, result }` where `result` is
  `'valid'` or `'invalid'` (no `'acceptable'` entries in this file).
  All hex values use no `0x` prefix; `msg` and `sig` are hex, `pk` is hex.
