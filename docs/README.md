# GumptionChain docs

In-repo documentation for running, extending, and integrating a GumptionChain
node.

## Reference

- [HTTP API reference](api-reference.md) — every `/api` endpoint, the role it
  requires, and its request/response shapes.
- [CLI reference](cli-reference.md) — the `gumptionchain` command tree
  (`gc` alias): node lifecycle, milling, migrations, keys, subjects, and
  transactions.
- [Configuration reference](configuration.md) — `FLASK_*` and `GC_*` settings,
  role allowlists, and joining a network.

## Protocol & auth

- [API authentication protocol (`gc-sig-v1`)](api-auth-protocol.md) — the
  per-request signing-key signature scheme every authenticated API request
  uses: canonical string, `GC-*` headers, algorithm, and a worked example.

## Running a node (operators)

- [Roll your own miller Pi](howto-miller-pi.md) — step-by-step setup of an
  outbound-only milling node on a Raspberry Pi.

## Building on a node (app / extension developers)

- [Browser-facing node proxy](node-proxy.md) — the embeddable blueprint that
  lets a web app build, sign, and submit transactions from the browser without
  exposing the node host or relay key.
- [Base ↔ extension UI seam](ui-extension-seam.md) — how an extension re-skins
  and extends a vanilla node's browser pages through the template-override seam
  and block contract.
