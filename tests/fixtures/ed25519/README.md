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

## Maintenance warning (consensus-critical)

Verdicts in `test_ed25519_vectors.py` are derived from GumptionChain's
**cofactored Option-B rule**, NOT copied from upstream labels:

- **speccheck:** `SPECCHECK_EXPECTED` is hand-derived per case (mixed-order cases
  accept under cofactored — they are not all rejects).
- **Wycheproof:** its `valid`/`invalid` labels encode a *cofactorless* convention
  that can legitimately disagree with our cofactored rule on small-order /
  non-canonical / malleability edge vectors. The test tolerates a disagreement
  ONLY on a vector flagged as such an edge class (tracked in
  `EXPECTED_DIVERGENCES`, currently empty — this pinned set has none).

If a fixture bump makes the gate fail on an edge vector, the verifier is almost
certainly correct and the *upstream label* differs by convention. **Vet the
vector and add its tcId to `EXPECTED_DIVERGENCES` — never change the consensus
rule to match an upstream (cofactorless) label.** That would silently swap our
rule for OpenSSL's, the exact split the vendored verifier prevents.
