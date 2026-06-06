// Typed errors shared by the wallet storage orchestration (gc-store) and the
// real adapters (gc-passkey-webauthn). Kept in their own module so an adapter
// can throw the typed error without importing the whole orchestration graph.

// PRF/WebAuthn unavailable, or an assertion returned no PRF result — distinct
// from a user-cancel/abort DOMException, so callers can branch on it.
export class UnsupportedError extends Error {}

// unlock() called with nothing stored.
export class NoWalletError extends Error {}
