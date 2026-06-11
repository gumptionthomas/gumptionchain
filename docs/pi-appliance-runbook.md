# GumptionChain Pi Appliance Runbook

Operator reference for building "plug it in and forget it" miller appliances
for EGU members. Covers wallet ceremony, bench provisioning, soak testing,
shipping, release discipline, and recovery.

The public roll-your-own path is in `docs/howto-miller-pi.md`. Cross-
references below point to HOWTO sections for shared mechanics rather than
duplicating them.

---

## 1. Fleet Roster

Update this table every time an appliance is built, shipped, or recovered.

| Hostname | Hardware  | Location          | Wallet address        | Channel | Shipped    |
|----------|-----------|-------------------|-----------------------|---------|------------|
| gcm-01   | Pi 3B+    | *(record here)*   | *(record here)*       | main    | *(canary)* |

**gcm-01** is the canary. It tracks `GC_UPDATE_CHANNEL=main` and auto-
updates nightly to the tip of `main`. A commit soaks on gcm-01 before it
receives a `v*` tag and rolls to the member fleet.

Column notes:
- **Channel** — `main` for gcm-01 (canary); `tags` for all member appliances.
- **Wallet address** — derived from the key content; matches the original
  generated filename before the rename to `gcm-NN.pem`.
- **Shipped** — the date the appliance left the bench. Leave `(canary)` for
  gcm-01; it never ships.
- Wallet address and location are sensitive; the table above is a
  template. Keep the **filled-in copy** in your private operator notes or
  a secrets store (e.g. `~/gc-fleet/roster.md`), not in the public repo.

---

## 2. Wallet Ceremony

**Generate the wallet on the bench workstation, never on the Pi.**

### 2a. Generate

```bash
mkdir -p ~/gc-fleet/gcm-NN
cd ~/gumptionchain
uv run gumptionchain wallet create -d ~/gc-fleet/gcm-NN
```

The `-d` flag requires the directory to exist (it is a `click.Path(exists=True)`
argument). The command prints the full path of the created file, e.g.:
`Created /home/you/gc-fleet/gcm-NN/GCabc123...GC.pem`. The filename is the
wallet address.

Rename the file to the device hostname so all per-device secrets share a
consistent naming convention:

```bash
mv ~/gc-fleet/gcm-NN/GCabc123...GC.pem ~/gc-fleet/gcm-NN/gcm-NN.pem
```

The wallet loader keys wallets by the address derived from the key's content,
not by filename — the rename is safe and keeps per-device files predictably
named.

Record the address in the fleet roster table above.

### 2b. Encrypted backup

Encrypt the `.pem` before storing it anywhere. Keep the encrypted backup
with your other operator secrets (e.g. a password manager vault, an
encrypted USB, or a self-hosted secrets store). The pattern below uses the
device hostname as the archive name:

**GPG symmetric (AES-256):**

```bash
gpg --symmetric --cipher-algo AES256 ~/gc-fleet/gcm-NN/gcm-NN.pem
# produces: ~/gc-fleet/gcm-NN/gcm-NN.pem.gpg
# store gcm-NN.pem.gpg in your secrets store; delete the plaintext .pem
# from any unsecured location after confirming the backup decrypts
```

**age (preferred if you have age installed):**

```bash
age -p -o ~/gc-fleet/gcm-NN/gcm-NN.pem.age ~/gc-fleet/gcm-NN/gcm-NN.pem
# -p prompts for a passphrase; the .age file is your backup
```

Verify the backup decrypts before proceeding:

```bash
# gpg
gpg -d ~/gc-fleet/gcm-NN/gcm-NN.pem.gpg | diff - ~/gc-fleet/gcm-NN/gcm-NN.pem

# age
age -d ~/gc-fleet/gcm-NN/gcm-NN.pem.age | diff - ~/gc-fleet/gcm-NN/gcm-NN.pem
```

### 2c. Hub allowlist

Add the address to the hub's `GC_MILLER_ADDRESSES` configuration and restart
the hub. The MILLER role covers TRANSACTOR — one allowlist entry is sufficient
for both block submission and faucet transactions from the member's game
backend.

```bash
# On the hub: add the address to GC_MILLER_ADDRESSES in the hub's env,
# then restart the hub process (exact mechanism depends on the hub's
# deployment; see the gumption-hub runbook).
```

The hub will reject blocks from the device until this step is complete.
Do it before provisioning so the bench soak actually exercises acceptance.

### 2d. Deliver a key copy to the member

Once allowlisted, deliver a copy of the `.pem` to the member over a
**pre-agreed secure channel** (Signal, Bitwarden Send, age-encrypted email,
or hand-off in person). The member's game backend signs faucet transactions
with the same wallet — they need the private key to authorize those
transactions. Confirm receipt before shipping the appliance.

---

## 3. Bench Provisioning

### 3a. Per-device secrets layout

Keep per-device secrets on the bench workstation at:

```
~/gc-fleet/gcm-NN/
    gcm-NN.pem           # wallet private key (plaintext only during bench work)
    gcm-NN.pem.gpg       # encrypted backup (always retained)
    env                  # the device's .env file
    deploy.env           # the device's deploy.env file
```

### 3b. Write the config files

**`~/gc-fleet/gcm-NN/env`** (becomes `.env` on the device):

```
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////home/gc/gumptionchain/gumptionchain.db
FLASK_SECRET_KEY=<random-string-at-least-32-chars>

GC_NODE_HOST=http://localhost:5000
GC_WALLET_DIR=/home/gc/wallets

# The username portion of each GC_PEERS entry is the LOCAL wallet address
# this node signs outgoing API requests as when talking to that peer.
# That wallet's .pem must be in GC_WALLET_DIR.
GC_PEERS=["https://<device-address>@hub.gumption.com"]
```

Use `sqlite:////` (four slashes). A relative path resolves inside
`src/instance/`, not the repo root.

**`~/gc-fleet/gcm-NN/deploy.env`** (read by systemd at unit start):

```
GC_MILL_ADDRESS=<device-address>
GC_MILL_PEER=https://<device-address>@hub.gumption.com
GC_UPDATE_CHANNEL=tags
```

`GC_MILL_PEER` **must exactly match the corresponding entry in `GC_PEERS`**,
including the `<device-address>@` username prefix. The miller looks up the
peer in `app.clients` by the literal string; a mismatch causes a crash-loop.
Both `GC_PEERS` in `.env` and `GC_MILL_PEER` in `deploy.env` must carry the
same `<device-address>@hub.gumption.com` value.

For the canary (gcm-01) use `GC_UPDATE_CHANNEL=main` instead of `tags`.

### 3c. Flash the SD card

Flash **Raspberry Pi OS Lite (64-bit)** to the SD card. After flashing,
mount the boot partition (the FAT32 partition, typically `/boot/firmware/`)
and hand-write `custom.toml` there.

Use `deploy/pi/custom.toml.example` as the template — fill in the device
hostname from the roster and your operator SSH public key:

```toml
config_version = 1

[system]
hostname = "gcm-NN"

[user]
name = "gc"
# generate with: openssl passwd -6
password = "$6$replace$me"

[ssh]
enabled = true
# bench access only; unreachable behind member's NAT once shipped
authorized_keys = ["ssh-ed25519 AAAA... operator@bench"]

[wlan]
# leave commented for ethernet-only appliances
# ssid = "member-network"
# password = "member-psk"
# country = "US"
```

**Warning:** The rpi-imager snap (Linux Snap Store) silently drops GUI
customization. Do not rely on "Advanced options" if you installed via snap.
Always hand-write `custom.toml` directly onto the boot partition.

Unmount the SD card, insert into the Pi, boot on the bench LAN. Confirm SSH
as `gc` works before running the provisioner.

### 3d. Run the provisioner

```bash
cd ~/gumptionchain
deploy/pi/provision-appliance.sh \
    gcm-NN.local \
    ~/gc-fleet/gcm-NN/gcm-NN.pem \
    ~/gc-fleet/gcm-NN/env \
    ~/gc-fleet/gcm-NN/deploy.env
```

The provisioner runs in three stages:

1. **Base packages + repo** — installs `unattended-upgrades`, clones the
   repo on the device (if absent).
2. **Secrets + config** — `scp`s the wallet `.pem`, `.env`, and `deploy.env`
   to the device and sets `chmod 600`. This happens **before** `install.sh`
   runs, so `install.sh` always finds the config files in place and can
   proceed to `gumptionchain init` and start the services in a single pass.
3. **Install + start** — runs `sudo bash deploy/pi/install.sh` on the device.
   Because `.env` and `deploy.env` already exist, `install.sh` runs
   `gumptionchain init` to create the database, then enables and starts both
   `gumptionchain-update.timer` and `gumptionchain-miller`.

The script ends with `systemctl is-active gumptionchain-miller` and the last
20 lines of the miller journal. A successful run prints:
`==> gcm-NN.local: provisioned. Begin the 24h bench soak (see runbook).`

---

## 4. 24-Hour Bench Soak Checklist

**Do not ship until every item below is checked.**

- [ ] `systemctl is-active gumptionchain-miller` returns `active` on the device.
- [ ] The miller journal shows the first-sync phase completing:
      `journalctl -u gumptionchain-miller -n 50 --no-pager`
      Look for "Finding Blocks" / "Loading Blocks" completing, then
      "Milling as address ..." appearing. The full chain downloads
      from the hub on first start — allow time proportional to chain
      length (potentially many minutes on a long chain).
- [ ] At least one block accepted by the hub is visible on the hub explorer.
      A `SCOOPED` result is normal (another miller won the race); the node
      is healthy regardless. A successful `POW` result is the gold standard.
- [ ] One forced update-timer run completes cleanly:
      ```bash
      ssh gc@gcm-NN.local sudo systemctl start gumptionchain-update.service
      ssh gc@gcm-NN.local journalctl -u gumptionchain-update.service --no-pager
      ```
      Expected: either a silent exit 0 when already current (check
      `systemctl status gumptionchain-update.service` shows
      `status=0/SUCCESS`), or `updated to vX.Y.Z` (tags channel) / `updated
      to <commit-sha>` (branch channel). No rollback, no skip-file entry.
- [ ] Reboot test — services come back unattended:
      ```bash
      ssh gc@gcm-NN.local sudo reboot
      # wait ~60 s
      ssh gc@gcm-NN.local systemctl is-active gumptionchain-miller
      ```
      Expected: `active`.
- [ ] No errors in the miller journal from the last 4 hours:
      ```bash
      ssh gc@gcm-NN.local journalctl -u gumptionchain-miller --since "4h ago" --no-pager
      ```

Only after all items are checked: proceed to shipping.

---

## 5. Ship Checklist

### What to include

- [ ] Raspberry Pi (SD card inserted, kit provisioned and soaked).
- [ ] Official Pi power supply (5V 3A USB-C for Pi 4; 5.1V 2.5A micro-USB
      for Pi 3B+). Do not substitute; under-voltage causes SD corruption.
- [ ] Short ethernet cable (recommended — but see note below).
- [ ] Brief "what your Pi does" card (see talking points below).

### Ethernet expectation

The appliance is ethernet-first. If the member's placement requires Wi-Fi,
bake the SSID and PSK into `custom.toml` at flash time and test the
connection on the bench before shipping. **Wi-Fi is the exception, not the
default.**

### What to tell the member

- Plug into power and a router port. Nothing else to do.
- The box mines GRIT blocks and credits your wallet automatically.
- It updates itself nightly. You will never need to log in.
- If it stops working: mail it back. Do not attempt to debug it yourself.
- Keep the private key copy you received — you will need it if the device
  is ever re-flashed (your GRIT balance is safe on the chain; the key
  is required to spend it).

---

## 6. Release Discipline

**Tagging is the fleet deploy trigger.** Every member appliance running
`GC_UPDATE_CHANNEL=tags` will pick up the new tag on its next nightly
update window (within 28 hours, given the 4-hour jitter).

### Canary-first rule

1. Land commits on `main`.
2. gcm-01 (`GC_UPDATE_CHANNEL=main`) auto-updates nightly. Let it soak for
   at least one full update cycle (24+ hours) before tagging.
3. Confirm gcm-01 is still milling and its update journal is clean.
4. Only then cut the release tag.

### Tagging

Use **annotated tags** (`-a`). The annotation is the release audience:

```bash
git tag -a vX.Y.Z -m "vX.Y.Z — brief description"
git push origin vX.Y.Z
```

Member appliances will pick it up on their next nightly timer run.

### Forward-only migration rule

Never tag a release whose schema migration breaks the *previous* tag's code.
Rollback in `update.sh` reverts code only — it does NOT downgrade the
database. If a rollback fires, the device runs the previous code against the
post-migration schema. The release is safe only when the previous code
remains compatible with the new schema. At EGU's current scale this
constraint is easy to honor: additive-only schema changes (new nullable
columns, new tables) satisfy it automatically.

### Bad tag handling

A bad tag costs one failed nightly update per device. The health gate
detects the failure, rolls back the code, appends the tag to the skip file
(`/home/gc/.gumptionchain-skip-tags`, root-owned), and exits non-zero (visible
in the journal). The device stays on its previous tag and continues milling.
The fleet waits for the next tag — no manual intervention is needed per
device. Publish a corrected tag once the issue is fixed; devices will pick
it up on the next timer run.

---

## 7. Recovery Flow

Use when a device is unresponsive, corrupted, or physically returned.

### 7a. Re-flash and re-provision

The wallet address and its on-chain balance are permanent. Re-flashing
gives the device a fresh SD with the same identity.

- [ ] Retrieve the encrypted wallet backup:
      `gpg -d ~/gc-fleet/gcm-NN/gcm-NN.pem.gpg > ~/gc-fleet/gcm-NN/gcm-NN.pem`
      (or `age -d ~/gc-fleet/gcm-NN/gcm-NN.pem.age > ~/gc-fleet/gcm-NN/gcm-NN.pem`)
- [ ] Flash a fresh Raspberry Pi OS Lite SD. Hand-write `custom.toml` with
      the **same hostname** from the roster (same address, same identity).
- [ ] Boot on the bench LAN. Confirm SSH.
- [ ] Re-run the provisioner with the **same secrets files** — no wallet
      ceremony needed:
      ```bash
      deploy/pi/provision-appliance.sh \
          gcm-NN.local \
          ~/gc-fleet/gcm-NN/gcm-NN.pem \
          ~/gc-fleet/gcm-NN/env \
          ~/gc-fleet/gcm-NN/deploy.env
      ```
- [ ] The chain database is empty after a re-flash. `gumptionchain init`
      (run by `install.sh`) creates a fresh DB; the miller syncs the full
      chain from the hub on first start. Budget soak time for the sync
      (see Section 4 checklist item 2).
- [ ] Hub allowlist entry does **not** need to change — the address is
      unchanged.
- [ ] Run the 24-hour bench soak (Section 4) before re-shipping.
- [ ] Update the roster: note the recovery date in the Shipped column.
- [ ] Shred the plaintext `.pem` from the workstation once provisioning
      confirms the device is milling:
      `shred -u ~/gc-fleet/gcm-NN/gcm-NN.pem`

### 7b. If the device is permanently lost (hardware failure)

If the Pi hardware itself is dead and cannot be re-flashed:

- [ ] Provision a new Pi through the full ceremony (Sections 2–4) with a
      new wallet and a new `gcm-NN` hostname.
- [ ] Deliver the new key to the member.
- [ ] Mark the old roster entry retired (old address, old hostname).
- [ ] The old address's on-chain balance is inaccessible without the key.
      If the member's key copy survived, they can still spend from it on
      any node; the lost Pi's mining simply stops.

---

## 8. First Execution: Re-Provision gcm-01

gcm-01 currently runs an ad-hoc rsync setup. This section migrates it to
the new kit. Complete these steps before building the first member appliance.

- [ ] **Generate gcm-01's deploy.env on the bench workstation.**
      gcm-01's wallet already exists. Locate the `.pem` and note the address.
      Write `~/gc-fleet/gcm-01/env` and `~/gc-fleet/gcm-01/deploy.env` using
      the address and the real hub URL. Set `GC_UPDATE_CHANNEL=main`:

      ```
      # deploy.env for gcm-01 (canary)
      GC_MILL_ADDRESS=<gcm-01-address>
      GC_MILL_PEER=https://<gcm-01-address>@hub.gumption.com
      GC_UPDATE_CHANNEL=main
      ```

      `GC_MILL_PEER` must exactly match the `GC_PEERS` entry in `.env`
      including the `<gcm-01-address>@` prefix.

- [ ] **Verify gcm-01's address is in the hub's `GC_MILLER_ADDRESSES`.**
      It should already be there from the existing setup. Confirm before
      proceeding.

- [ ] **Stop the old ad-hoc rsync/service setup on gcm-01.** This varies by
      whatever is currently running; the goal is a clean slate for the new
      unit files. Stop and disable any existing gumptionchain service units,
      then remove them from `/etc/systemd/system/` so the new installer
      takes full ownership.

- [ ] **Copy the existing wallet** to `~/gc-fleet/gcm-01/` on the bench (or
      confirm it is already there with an encrypted backup). No new wallet
      ceremony — same key, same address.

- [ ] **Flash a fresh SD or re-provision in-place.**
      - *Fresh flash (recommended for a clean baseline):* Flash Pi OS Lite,
        hand-write `custom.toml` for `gcm-01`, boot, confirm SSH, then:
        ```bash
        deploy/pi/provision-appliance.sh \
            gcm-01.local \
            ~/gc-fleet/gcm-01/gcm-01.pem \
            ~/gc-fleet/gcm-01/env \
            ~/gc-fleet/gcm-01/deploy.env
        ```
      - *In-place (if re-flashing is inconvenient):* `scp` the secrets to the
        device and run `sudo bash ~/gumptionchain/deploy/pi/install.sh` manually
        from an SSH session. Confirm `GC_UPDATE_CHANNEL=main` is in `deploy.env`
        before running.

- [ ] **Confirm provisioning succeeded:**
      ```bash
      ssh gc@gcm-01.local systemctl is-active gumptionchain-miller
      ssh gc@gcm-01.local journalctl -u gumptionchain-miller -n 30 --no-pager
      ```
      The journal should show the first-sync phase (or fast-start if the DB
      was preserved), then "Milling as address ...".

- [ ] **Run the bench soak (Section 4) for gcm-01.** At minimum: miller
      active, one block attempt visible, forced update-timer run clean,
      reboot test.

- [ ] **Cut the first `v*` release tag** once gcm-01 has soaked at least one
      full nightly update cycle cleanly (24+ hours, update journal shows
      a silent exit 0 when already current or `updated to <commit-sha>`
      (branch channel), no rollback):
      ```bash
      git tag -a v0.1.0 -m "v0.1.0 — initial managed appliance release"
      git push origin v0.1.0
      ```
      Member appliances (once built) will auto-update to this tag on their
      first nightly timer run.

- [ ] **Update the fleet roster** with gcm-01's address and the migration
      date. Mark channel `main`.

Only after the first tag cycle soaks on gcm-01 cleanly: proceed to building
the first member appliance.
