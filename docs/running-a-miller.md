# Running a miller node

A **miller** is a gumptionchain node that mints (mills) blocks and earns the
coinbase reward. It is **outbound-only**: it *pushes* its milled blocks to a
configured peer and *polls* that peer for others' blocks — so it runs fine
**behind NAT**, with no inbound ports. Permissioning is the **network
operator's** policy: the peer must list your miller's address in its
`GC_MILLER_ADDRESSES` allowlist before it will accept your blocks.

> This guide covers the **network-agnostic** mechanics of running a node. For
> joining a *specific* network (e.g. the EGU fleet — which address to ask the
> operator to allow-list, which hub to peer with), see that network's runbook.

## Hardware

Any Linux host with Python ≥ 3.12. A **Raspberry Pi** works well — the network's
difficulty floor was benchmarked on a **Pi 3 B+** (see
[#169](https://github.com/gumptionthomas/gumptionchain/issues/169): 5 leading-zero
target, ~55.5 kH/s single-core). Pi 4s are faster; difficulty retargets upward as
faster millers join.

## 1. Install

gumptionchain is built with [uv](https://docs.astral.sh/uv/). Clone the repo and
sync the runtime dependencies:

```bash
git clone https://github.com/gumptionthomas/gumptionchain.git
cd gumptionchain
uv sync
```

`uv sync` creates a `.venv/` with the `gumptionchain` CLI on its path. Either
prefix commands with `uv run` (e.g. `uv run gumptionchain --help`) or call the
binary directly at `.venv/bin/gumptionchain` (what the systemd unit below uses).
A shorter `gc` alias is also installed.

## 2. Generate your node signing key

```bash
mkdir -p keys   # -d must point at an existing directory
uv run gumptionchain signing-key create -d ./keys
```

This writes `./keys/<address>.pem` and prints `Created ./keys/<address>.pem`.
**The filename is your `gc1…` address** — that's your miller's identity *and*
its coinbase-reward recipient. Send this address to the network operator to be
allow-listed (added to their `GC_MILLER_ADDRESSES`).

Back up the `.pem` (or its `gcsec1…` secret) somewhere safe — it is the only copy
of your key, and whoever holds it controls your rewards.

## 3. Configure

gumptionchain reads `FLASK_*` and `GC_*` environment variables (a `.env` file in
the working directory is auto-loaded). A minimal miller `.env`:

```bash
# This node's own host identifier (part of the request-signature canonical).
GC_NODE_HOST=https://my-miller.example

# Your node signing key — choose ONE:
#   inline secret (no files; ideal for cloud/secret-store deploys)
GC_SIGNING_KEY=gcsec1…
#   …or the directory holding your *.pem
# GC_SIGNING_KEY_DIR=./keys

# The peer you gossip milled blocks TO. The username is YOUR OWN address (you
# sign requests as it); the host is the peer node. JSON list, on one line.
GC_PEERS=["https://<your-address>@<peer-host>"]

# Database (sqlite is fine for a single miller).
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///gumptionchain.db
```

Then create the schema (runs the migrations):

```bash
uv run gumptionchain init
```

## 4. Mill

```bash
uv run gumptionchain mill <your-address> -p "https://<your-address>@<peer-host>" -m
```

- **`<your-address>`** (positional, required) — the address that receives the
  coinbase rewards. The matching private key is resolved automatically from
  `GC_SIGNING_KEY` / `GC_SIGNING_KEY_DIR`; pass `-w ./keys/<address>.pem`
  instead to point at a key file explicitly.
- **`-p, --peer`** — the peer to **poll** for new blocks/txns before milling.
  **This must be byte-identical to one of your `GC_PEERS` entries** — mill looks
  it up among the configured peer clients and errors with
  *"Peer … client not configured"* otherwise. Point it at the same node you push
  to.
- **`-m, --multi`** — use multiprocessing (all CPU cores).

Other options (`uv run gumptionchain mill --help`): `-r/--rounds` (rounds between
new-block checks), `-s/--size` (hashes per round per CPU), `-b/--blocks` (stop
after N blocks; default 0 = run forever).

## 5. Run on boot (systemd)

`/etc/systemd/system/gumptionchain-miller.service` (adjust the user, paths, and
the `<your-address>`/`<peer-host>` placeholders):

```ini
[Unit]
Description=gumptionchain miller
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/gumptionchain
EnvironmentFile=/home/pi/gumptionchain/.env
ExecStart=/home/pi/gumptionchain/.venv/bin/gumptionchain mill <your-address> -p https://<your-address>@<peer-host> -m
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` lets the `.env` and the sqlite DB resolve relative to the
repo. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gumptionchain-miller
journalctl -u gumptionchain-miller -f
```

## 6. Verify

- The miller logs print a `Milling as address <gc1…>` banner, then milling
  rounds and any blocks it finds.
- Your blocks appear confirmed on the peer's explorer, and your balance reflects
  the coinbase rewards:

  ```bash
  uv run gumptionchain signing-key balance <your-address> -h https://<peer-host>
  ```

  (A balance lookup is an authenticated API call; it signs as your key and needs
  at least `READER` access on the host it queries.)

## Notes

- **Outbound-only.** No port-forwarding or NAT traversal — the publicly
  reachable peer is the only inbound node. A miller pushes to / polls its peer.
- **Permissioning is the operator's policy.** Your address must be in the peer's
  `GC_MILLER_ADDRESSES`. On a private network you run, you set that; on a managed
  network, the operator does.
- **`-p` must match a `GC_PEERS` entry exactly** — the poll peer is resolved from
  the configured push clients, so the two strings have to be identical.
- **Difficulty floor:** the Pi 3 B+ benchmark in
  [#169](https://github.com/gumptionthomas/gumptionchain/issues/169).
- Full config reference: `CLAUDE.md` (the `GC_*` / `FLASK_*` settings) and
  `uv run gumptionchain <command> --help`.
