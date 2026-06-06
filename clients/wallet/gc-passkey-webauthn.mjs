// Real WebAuthn-PRF passkey adapter (browser-only). Implements the `passkey`
// interface. The passkey is a normal credential (ES256/RS256) used ONLY for its
// PRF output — it is NOT the wallet's RSA key.
import { base64encode, base64decode } from './gc-crypto.mjs';
import { UnsupportedError } from './gc-errors.mjs';

const PRF_SALT = new TextEncoder().encode('gc-wallet-prf-v1');

function b64urlEncode(bytes) {
  return base64encode(bytes)
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64urlDecode(s) {
  const pad = s.length % 4 ? '='.repeat(4 - (s.length % 4)) : '';
  return base64decode(s.replace(/-/g, '+').replace(/_/g, '/') + pad);
}
function prfFirst(credential) {
  const r = credential.getClientExtensionResults?.()?.prf?.results?.first;
  return r ? new Uint8Array(r) : null;
}

export function makeWebauthnPasskey({ rpId, rpName, userVerification = 'preferred' }) {
  async function unlock(credentialId) {
    const assertion = await navigator.credentials.get({
      publicKey: {
        rpId,
        challenge: crypto.getRandomValues(new Uint8Array(32)),
        allowCredentials: [
          { type: 'public-key', id: b64urlDecode(credentialId) },
        ],
        userVerification,
        extensions: { prf: { eval: { first: PRF_SALT } } },
      },
    });
    if (!assertion) {
      throw new Error('passkey assertion failed or was cancelled');
    }
    const out = prfFirst(assertion);
    if (!out) {
      throw new UnsupportedError('passkey PRF not available on assertion');
    }
    return out;
  }

  return {
    async isSupported() {
      return (
        typeof window !== 'undefined' &&
        typeof window.PublicKeyCredential === 'function'
      );
    },

    async enroll({ userId, userName } = {}) {
      const idBytes =
        typeof userId === 'string'
          ? new TextEncoder().encode(userId)
          : userId || crypto.getRandomValues(new Uint8Array(16));
      const cred = await navigator.credentials.create({
        publicKey: {
          rp: { id: rpId, name: rpName },
          user: { id: idBytes, name: userName, displayName: userName },
          challenge: crypto.getRandomValues(new Uint8Array(32)),
          pubKeyCredParams: [
            { type: 'public-key', alg: -7 },
            { type: 'public-key', alg: -257 },
          ],
          authenticatorSelection: { residentKey: 'required', userVerification },
          extensions: { prf: { eval: { first: PRF_SALT } } },
        },
      });
      if (!cred) {
        throw new Error('passkey creation failed or was cancelled');
      }
      const credentialId = b64urlEncode(new Uint8Array(cred.rawId));
      // Prefer the create-time PRF result; else one assertion gets it (which
      // throws UnsupportedError if the authenticator doesn't support PRF).
      const prfOutput = prfFirst(cred) ?? (await unlock(credentialId));
      return { credentialId, prfOutput };
    },

    unlock,
  };
}
