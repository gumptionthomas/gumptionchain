# GumptionChain docs

In-repo documentation for running, extending, and integrating a GumptionChain
node.

## Protocol & API

- [API authentication protocol (`gc-sig-v1`)](api-auth-protocol.md) — the
  per-request signing-key signature scheme every authenticated API request
  uses: canonical string, `GC-*` headers, algorithm, and a worked example.

## Running a node (operators)

- [Roll your own miller Pi](howto-miller-pi.md) — step-by-step setup of an
  outbound-only milling node on a Raspberry Pi.

## Building on a node (app / extension developers)

- [Base ↔ extension UI seam](ui-extension-seam.md) — how an extension re-skins
  and extends a vanilla node's browser pages through the template-override seam
  and block contract.
