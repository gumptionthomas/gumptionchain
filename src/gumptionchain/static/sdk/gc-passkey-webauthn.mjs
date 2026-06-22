// Real WebAuthn-PRF passkey adapter (browser-only). Implements the `passkey`
// interface. The passkey is a normal credential (ES256/RS256) used ONLY for its
// PRF output — it is NOT the signing key's Ed25519 key.
import { base64encode, base64decode } from './gc-crypto.mjs';
import { UnsupportedError } from './gc-errors.mjs';

const PRF_SALT = new TextEncoder().encode('gc-signing-key-prf-v1');

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

  async function discover({ mediation = 'optional', signal } = {}) {
    if (typeof navigator === 'undefined'
        || !navigator.credentials
        || typeof navigator.credentials.get !== 'function') {
      return null;
    }
    let assertion;
    try {
      assertion = await navigator.credentials.get({
        mediation,
        ...(signal != null && { signal }),
        publicKey: {
          rpId,
          challenge: crypto.getRandomValues(new Uint8Array(32)),
          allowCredentials: [],
          userVerification,
          extensions: { prf: { eval: { first: PRF_SALT } } },
        },
      });
    } catch (e) {
      // Dismissal / abort are normal "no passkey selected" outcomes, not errors.
      if (e && (e.name === 'NotAllowedError'
          || e.name === 'AbortError'
          || e.name === 'SecurityError')) {
        return null;
      }
      throw e;
    }
    if (!assertion) {
      return null;
    }
    const out = prfFirst(assertion);
    if (!out) {
      throw new UnsupportedError('passkey PRF not available on assertion');
    }
    // userHandle carries the enrolled user.id — in EGU apps that's the GC
    // address — so a cross-origin discovery yields *which* identity, with no
    // key material. Generic at the SDK layer; the consumer interprets it.
    const uh = assertion.response && assertion.response.userHandle;
    return {
      credentialId: b64urlEncode(new Uint8Array(assertion.rawId)),
      prfOutput: out,
      userHandle: uh ? new TextDecoder().decode(uh) : null,
    };
  }

  async function isConditionalAvailable() {
    try {
      if (typeof window === 'undefined' ||
          typeof window.PublicKeyCredential !== 'function') {
        return false;
      }
      const fn = window.PublicKeyCredential.isConditionalMediationAvailable;
      return typeof fn === 'function'
        ? Boolean(await fn.call(window.PublicKeyCredential))
        : false;
    } catch {
      return false;
    }
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

    discover,
    isConditionalAvailable,
  };
}

// Member-kit recognition: "is a returning EGU user here, and who are they?"
// A thin convenience over discover() — build a discover-only adapter for rpId,
// run discovery, and map to { recognized, address }. NEVER throws for the
// absent / cancelled / unsupported paths (discover() returns null for dismissal
// and an unsupported navigator; PRF-absent throws UnsupportedError, caught here);
// any unexpected error also resolves to { recognized: false } so a "who's here?"
// hint always lets the caller fall through to "create". address is the decoded
// userHandle (the enrolled userId; the EGU convention is that this is the GC
// address) — generic at the SDK layer. rpName is unused by discover(), so rpId
// is passed as a harmless placeholder.
//
// IMPORTANT: the returned address equals the GC address ONLY for ENROLLED
// (wrap-keyring) identities, where user.id === address. DERIVED (passkey-PRF)
// identities have a NON-address userHandle — their address isn't known at
// create() time, before the PRF exists — so recognize() would report a
// confidently wrong address for them. Recognize derived identities via
// makeDerivedIdentity.resolve() (which re-derives the real address from the
// PRF), not this helper. The member-kit guide routes the two kinds accordingly.
export async function recognize({ rpId, mediation = 'optional', signal } = {}) {
  const pk = makeWebauthnPasskey({ rpId, rpName: rpId });
  let found;
  try {
    found = await pk.discover({ mediation, signal });
  } catch {
    // Deliberate fail-open: a signal abort or any unexpected error maps to
    // "not recognized" (this is a UX hint, never an auth gate) — do NOT
    // "fix" this into a throw.
    return { recognized: false, address: null };
  }
  if (!found || !found.userHandle) {
    return { recognized: false, address: null };
  }
  return { recognized: true, address: found.userHandle };
}
