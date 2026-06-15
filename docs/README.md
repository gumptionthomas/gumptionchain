# GumptionChain docs

In-repo documentation for running, extending, and integrating a GumptionChain
node. (The hosted, rendered docs live at <https://gumption.com/chain/docs>.)

## Protocol & API

- [API authentication protocol (`gc-sig-v1`)](api-auth-protocol.md) — the
  per-request signing-key signature scheme every authenticated API request
  uses: canonical string, `GC-*` headers, algorithm, and a worked example.
- [Social binding envelope (`gc-msg-v1`)](social-binding-envelope.md) — the
  bidirectional, Keybase-style proof that binds a signing key to an external
  handle.

## Running a node (operators)

- [Roll your own miller Pi](howto-miller-pi.md) — step-by-step setup of an
  outbound-only milling node on a Raspberry Pi.
- [Pi appliance runbook](pi-appliance-runbook.md) — operator reference for
  building "plug it in and forget it" miller appliances and provisioning a
  fleet.

## Building on a node (app / extension developers)

- [Base ↔ extension UI seam](ui-extension-seam.md) — how an extension re-skins
  and extends a vanilla node's browser pages through the template-override seam
  and block contract.
- [Key onboarding for EGU apps](key-onboarding-for-egu-apps.md) — the contract
  every EGU app follows to bring a user's signing key into being safely
  (reference implementation: the hub's `/onboarding` flow).

## Internal

- `superpowers/` — dated design specs, implementation plans, audits, and the
  roadmap. Historical records of how features were built; not user-facing and
  intentionally preserved as written.
