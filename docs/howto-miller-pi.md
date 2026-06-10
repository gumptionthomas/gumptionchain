# GumptionChain: Roll Your Own Miller Pi

This guide walks through setting up an outbound-only GumptionChain milling
node on a Raspberry Pi. The node peers with gumption-hub, mines blocks, and
pushes them outbound. No port forwarding, no inbound connections, no server
port to expose.

Minimum hardware: Raspberry Pi 3B+ (1 GB RAM). Pi 4 or better is recommended
for comfortable headroom. Use ethernet — Wi-Fi introduces latency jitter that
hurts the polling loop. 16 GB SD card minimum; 32 GB preferred.

---

## 1. What you're building

A headless Pi that runs two systemd services:

- `gumptionchain-miller` — the milling loop. Polls gumption-hub for the
  current chain tip, races to extend it with proof-of-work, and pushes
  accepted blocks outbound. The loop is written to survive transient hub
  outages; `Restart=always` handles crashes. No Flask server runs on a
  miller node.
- `gumptionchain-update.timer` — a daily nightly-update timer that follows
  a release channel (`tags` by default), applies schema migrations, re-syncs
  unit files, health-gates the restart, and rolls back automatically if the
  new version fails the health check.

Your wallet address earns coinbase GRIT on every block you mine. The `.pem`
file is the only copy — back it up before the node ever mines a block.

---

## 2. Flash the OS

Flash **Raspberry Pi OS Lite (64-bit)** to the SD card using
[Raspberry Pi Imager](https://www.raspberrypi.com/software/).

**Warning:** The rpi-imager snap (the Linux version from the Snap Store)
silently drops GUI customization without error. Do not rely on the "Advanced
options" dialog if you installed via snap. Instead, hand-write a
`custom.toml` file directly onto the boot partition after flashing.

Copy `deploy/pi/custom.toml.example` from the repo as a starting point.
Mount the SD card's boot partition (the FAT32 partition, typically
`/boot/firmware/` on Bookworm) and write the file there:

```
config_version = 1

[system]
hostname = "gcm-NN"          # pick a unique name, e.g. gcm-02

[user]
name = "gc"
# generate with: openssl passwd -6
password = "$6$yoursalt$yourhash"

[ssh]
enabled = true
authorized_keys = ["ssh-ed25519 AAAA... you@yourhost"]

# [wlan]  — leave commented for ethernet-only nodes
# ssid = "your-network"
# password = "your-psk"
# country = "US"
```

Unmount and boot the Pi. Confirm you can SSH in as `gc` before continuing.

---

## 3. Install the kit

From a shell on the Pi (or over SSH as `gc`):

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/gumptionthomas/gumptionchain.git ~/gumptionchain
sudo bash ~/gumptionchain/deploy/pi/install.sh
```

`install.sh` is idempotent. It:

1. Runs `apt-get update` and installs `git`, `curl`, and `ca-certificates`.
2. Installs `uv` under `/home/gc/.local/bin/uv` if absent.
3. Clones the repo into `~/gumptionchain` if not already there, then checks
   out the latest release tag.
4. Runs `uv sync --frozen` to install Python dependencies.
5. Copies the systemd unit files to `/etc/systemd/system/`, reloads the
   daemon, and enables the update timer.

`uv` is installed into `~/.local/bin/uv`. That directory is added to your
`PATH` on login, so **log out and back in** (or prefix commands with
`~/.local/bin/uv`) before continuing.

If `.env` and `deploy.env` are not yet present, the installer prints the
remaining steps and exits cleanly — it will not attempt `gumptionchain init`
until the config files exist. Complete sections 4–6 below, then run:

```bash
sudo systemctl enable --now gumptionchain-miller
```

---

## 4. Create a wallet

From the repo directory as the `gc` user:

```bash
mkdir -p /home/gc/wallets
cd ~/gumptionchain
uv run gumptionchain wallet create -d /home/gc/wallets
```

The `-d` / `--walletdir` option is `click.Path(exists=True)` — the directory
**must exist before you run the command** (hence `mkdir -p` above). If
omitted, it falls back to the `GC_WALLET_DIR` value in your `.env` (which
must exist first). Using an explicit path here is safe.

Running the command before `.env` exists will log a `SQLALCHEMY_DATABASE_URI`
ERROR line, but the wallet is still created and the command still prints
`Created /home/gc/wallets/<address>.pem` — this is normal.

The command prints the path to the created file, e.g.
`/home/gc/wallets/GCabc123...GC.pem`. The file name is the wallet address.
That address string is what you give to the hub operator and put in your
config.

**Back up the `.pem` now.** Copy it to at least one offline location (USB
drive, encrypted cloud storage, printed key material — your choice). The
`.pem` is the only record of your private key. If it is lost, any GRIT
mined to that address is unrecoverable.

---

## 5. Get allowlisted by the hub operator

Your wallet address must appear in the hub's `GC_MILLER_ADDRESSES`
configuration before the hub will accept blocks from you.

Send your address (the filename of the `.pem`, without the `.pem` extension)
to the gumption-hub operator. Once they add it and restart the hub, your node
can submit blocks.

The hub's MILLER role also covers TRANSACTOR — once allowlisted, your address
can submit transactions to the hub directly.

---

## 6. Configure

Create two files in `~/gumptionchain/`.

**`.env`** — loaded automatically by python-dotenv at startup:

```
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////home/gc/gumptionchain/gumptionchain.db
FLASK_SECRET_KEY=<random-string-at-least-32-chars>

GC_NODE_HOST=http://localhost:5000
GC_WALLET_DIR=/home/gc/wallets

# GC_PEERS entries have the form https://<your-address>@<hub-host>.
# The username portion is your LOCAL wallet address — the address this
# node signs outgoing API requests as when talking to that peer.
# That wallet's .pem must be in GC_WALLET_DIR, and the hub must list
# the address in its GC_MILLER_ADDRESSES.
GC_PEERS=["https://<your-address>@hub.gumption.com"]
```

Use an **absolute** path for the database URI (four slashes: `sqlite:////`).
A relative path resolves from within the installed package directory
(`src/instance/`), not the repo root.

**`deploy.env`** — read by the systemd unit at runtime:

```
GC_MILL_ADDRESS=<your-address>
GC_MILL_PEER=https://<your-address>@hub.gumption.com
GC_UPDATE_CHANNEL=tags
```

`GC_MILL_ADDRESS` is the wallet address that receives coinbase rewards.
`GC_MILL_PEER` is the URL the miller polls before each round. **This value
must exactly match the corresponding entry in `GC_PEERS` (including the
`<your-address>@` username prefix)** — the miller looks up the peer in
`app.clients` by the literal string, and a mismatch causes a crash-loop.
`GC_UPDATE_CHANNEL=tags` means the updater follows the highest semver
`v*` release tag (the standard appliance channel).

Once both files are in place, initialize the database:

```bash
cd ~/gumptionchain
uv run gumptionchain init
```

---

## 7. Start and verify

Enable and start the miller:

```bash
sudo systemctl enable --now gumptionchain-miller
```

Watch the live log:

```bash
journalctl -u gumptionchain-miller -f
```

Healthy milling output looks like:

1. **First-sync phase** — a `Synchronizing with peer <peer>` rule prints,
   followed by a two-panel progress display: "Finding Blocks" (backward
   walk counting ancestors from the hub's tip) then "Loading Blocks" (forward
   apply with a progress bar). This downloads and applies every ancestor block
   from the hub's current chain tip all the way to genesis — a full local
   chain copy, automatically, with no separate sync command. Expected duration
   grows with chain length.

2. **Milling phase** — a `Milling as address <address>` rule prints, then
   for each block attempt: a borderless start table (Block index, Chain hash,
   Target, Started timestamp) followed by a spinner while hashing, then a
   stop table (Stopped, Elapsed, Hashes). The stop table's `POW` row reads
   the proof-of-work value when **this node wins the block**, or `SCOOPED`
   (styled dimly) when another miller extends the chain first. A very close
   race prints `SCOOPED (but so close)`.

**First-sync expectations:** on an empty database the full first-sync
download runs automatically before the first milling round begins — there
is no separate `gumptionchain sync` step needed. The time this takes scales
with chain length.

Check service status at any time:

```bash
systemctl status gumptionchain-miller
systemctl status gumptionchain-update.timer
```

---

## 8. Updates

### Automatic (default)

The `gumptionchain-update.timer` fires once daily with a randomized delay
of up to 4 hours (`RandomizedDelaySec=4h`). The timer is `Persistent=true`,
so a Pi that was off at the scheduled time will run the update on next boot.

When the timer fires, `update.sh`:

1. Fetches all tags from origin.
2. Resolves the highest semver `v*` tag.
3. Exits cleanly if the node is already on that tag, or if the tag appears
   in the skip file (see below).
4. Checks out the new tag, runs `uv sync --frozen`, runs
   `gumptionchain db upgrade` to apply any schema migrations.
5. Re-copies any changed unit files to `/etc/systemd/system/` and reloads
   the daemon.
6. Restarts `gumptionchain-miller` and waits 60 seconds.
7. **Health gate:** if the service is not `active` after the settle period,
   the updater rolls back to the previous tag (runs `uv sync --frozen` +
   `db upgrade` again on the old code, restarts the service), appends the
   bad tag to the skip file at `~gc/.gumptionchain-skip-tags`, and exits
   non-zero. The failure is visible in the journal.

Migrations are never downgraded on rollback — only code is reverted. This
is safe as long as release discipline is followed: never tag a version whose
migration breaks the previous tag's code.

### Manual

To update to a specific tag without waiting for the timer:

```bash
cd ~/gumptionchain
git fetch --tags origin
git checkout <new-tag>
uv sync --frozen
uv run gumptionchain db upgrade
sudo systemctl restart gumptionchain-miller
```

To trigger the automatic updater immediately (runs the full
health-gate/rollback path):

```bash
sudo systemctl start gumptionchain-update.service
journalctl -u gumptionchain-update.service -f
```

---

## 9. Troubleshooting

**Follow live logs for the miller:**

```bash
journalctl -u gumptionchain-miller -f
```

**Check recent update runs:**

```bash
journalctl -u gumptionchain-update.service --no-pager
```

**Check service status:**

```bash
systemctl status gumptionchain-miller
systemctl status gumptionchain-update.timer
```

**Re-sync from scratch** (if the local chain is corrupt or the node is on
a fork with no path forward): stop the miller, delete the database, restart.
The miller will re-poll the hub and rebuild from its current tip.

```bash
sudo systemctl stop gumptionchain-miller
rm ~/gumptionchain/gumptionchain.db
cd ~/gumptionchain && uv run gumptionchain init
sudo systemctl start gumptionchain-miller
journalctl -u gumptionchain-miller -f
```

The database path must match `FLASK_SQLALCHEMY_DATABASE_URI` in `.env`.
If you used a different absolute path, delete that file instead.

**The update skip file** — if a bad tag was detected and rolled back, the
tag is appended to `~gc/.gumptionchain-skip-tags` (one tag per line). The
updater will never re-attempt a listed tag. To clear it (for example, after
a patched re-tag is published):

```bash
# inspect
cat ~/.gumptionchain-skip-tags

# clear entirely (next timer run will attempt the highest available tag)
# (sudo required: update.sh runs as root and owns this file)
sudo rm ~/.gumptionchain-skip-tags
```

**Hub connectivity** — if the miller is running but not submitting blocks,
confirm the hub is reachable and your address is allowlisted:

```bash
curl -s https://hub.gumption.com/  # basic reachability
```

If the hub returns 403 on block submissions, your address is not in
`GC_MILLER_ADDRESSES` — contact the hub operator.
