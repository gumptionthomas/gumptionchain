// Self-sovereign derived-identity flow: enroll a passkey and DERIVE the GC
// seed from its PRF (no stored key), and resolve (federated login) by
// re-deriving and matching a CALLER-SUPPLIED expected address. A passkey's
// userHandle is fixed at create() time — before its PRF exists — so a derive
// identity's address can't be its userHandle; the expected address comes from
// the consumer's recognition directory (hub #313/#67), defaulting to the
// discovered userHandle only when a consumer wired it that way. The passphrase
// 2FA mode is confirmed by the address alone (a wrong passphrase -> a different
// address). DOM-free; the `passkey` interface is injected (same shape as
// gc-passkey-webauthn). The hub + the /signing-key page compose this. No deps.
import { deriveSigningKey } from './gc-derive.mjs';
import { seedToMnemonic } from './gc-bip39.mjs';
import { decodeSecret } from './gc-bech32.mjs';

export function makeDerivedIdentity({ passkey }) {
  // Create a passkey, derive the seed from its PRF, and return the key + its
  // recovery phrase + credentialId. The CALLER records (credentialId ->
  // address[, hasPassphrase]) in its recognition directory so a later resolve()
  // knows the expected address. The derived address is NOT the userHandle.
  async function enroll({ userName, passphrase } = {}) {
    const { credentialId, prfOutput } = await passkey.enroll({ userName });
    const signing_key = await deriveSigningKey(prfOutput, { passphrase });
    const address = await signing_key.address();
    const secret = await signing_key.exportSecret();
    const mnemonic = await seedToMnemonic(decodeSecret(secret));
    return { signing_key, address, mnemonic, credentialId };
  }

  // Discover a passkey, derive, and match against the caller-supplied expected
  // address (falling back to the discovered userHandle if the caller omitted it
  // but the credential carries one).
  async function resolve({ expectedAddress, passphrase } = {}) {
    const found = await passkey.discover();
    if (!found) return { status: 'no-passkey' };
    const { prfOutput, userHandle } = found;
    const expected = expectedAddress ?? userHandle ?? null;
    const sk = await deriveSigningKey(prfOutput, { passphrase });
    const address = await sk.address();
    if (expected == null) {
      return { status: 'derived', signing_key: sk, address };
    }
    if (address === expected) {
      return { status: 'ok', signing_key: sk, address };
    }
    if (!passphrase) return { status: 'needs-passphrase', expected };
    return { status: 'mismatch', expected };
  }

  return { enroll, resolve };
}
