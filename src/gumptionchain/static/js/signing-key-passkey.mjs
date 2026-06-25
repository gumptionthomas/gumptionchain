// Shared passkey-adapter factory for the browser signing_key glue. Both /signing-key
// (signing-key-glue.mjs) and /transact (transact-glue.mjs) gate passkey unlock on a
// secure context where WebAuthn-PRF is actually supported, so the gating lives
// in one place rather than being duplicated per page.
import { makeWebauthnPasskey } from '../sdk/gc-passkey-webauthn.mjs';

// Resolve the WebAuthn RP ID for a passkey ceremony: an explicit
// (server-configured) rpId wins; otherwise fall back to this origin's hostname
// (the historical behaviour). Pure + DOM-free so it can be unit-tested with a
// fake window. A shared, canonical rpId across distinct EGU origins is what
// lets one passkey identity federate via Related Origin Requests (gump-hub#67);
// leaving it unset keeps each origin self-scoped (non-breaking default).
export function resolveRpId({ window: win = window, rpId } = {}) {
  return rpId || win?.location?.hostname;
}

// Build a passkey adapter for THIS origin, but only on a secure context where
// PRF is actually supported. Returns null (not a half-working adapter) when
// passkeys aren't usable here, so the UI degrades to passphrase-only. rpId
// overrides the origin hostname (see resolveRpId) so the page can enroll/unlock
// under a canonical EGU rpId; it MUST be identical across enroll and unlock.
export async function makePasskey({ window: win = window, rpName, rpId } = {}) {
  if (!win?.isSecureContext) {
    return null;
  }
  const passkey = makeWebauthnPasskey({
    rpId: resolveRpId({ window: win, rpId }),
    rpName,
  });
  if (!(await passkey.isSupported())) {
    return null;
  }
  return passkey;
}
