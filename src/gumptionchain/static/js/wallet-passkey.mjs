// Shared passkey-adapter factory for the browser wallet glue. Both /wallet
// (wallet-glue.mjs) and /transact (transact-glue.mjs) gate passkey unlock on a
// secure context where WebAuthn-PRF is actually supported, so the gating lives
// in one place rather than being duplicated per page.
import { makeWebauthnPasskey } from '../wallet/gc-passkey-webauthn.mjs';

// Build a passkey adapter for THIS origin, but only on a secure context where
// PRF is actually supported. Returns null (not a half-working adapter) when
// passkeys aren't usable here, so the UI degrades to passphrase-only.
export async function makePasskey({ window: win = window, rpName } = {}) {
  if (!win?.isSecureContext) {
    return null;
  }
  const passkey = makeWebauthnPasskey({
    rpId: win.location.hostname,
    rpName,
  });
  if (!(await passkey.isSupported())) {
    return null;
  }
  return passkey;
}
