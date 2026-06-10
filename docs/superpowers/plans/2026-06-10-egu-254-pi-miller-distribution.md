# EGU #254 — Pi Miller Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `deploy/pi/` runtime kit (miller service, auto-update
timer + script, installer, bench provisioner) plus the public roll-your-own
HOWTO and the operator appliance runbook.

**Architecture:** Per the approved spec
(`docs/superpowers/specs/2026-06-10-egu-254-pi-miller-distribution-design.md`):
one kit, two wrappers. The updater follows a channel (`tags` = highest
semver `v*` tag for appliances, a branch name for the gcm-01 canary),
applies `git checkout → uv sync --frozen → db upgrade → unit re-sync →
restart`, health-gates, and rolls back + skip-files a bad tag. No remote
access to appliances; everything is outbound.

**Tech Stack:** bash (`set -euo pipefail`, shellcheck-clean), systemd
units, pytest (script behavior against a local fixture git repo with
stubbed `systemctl`/`uv` seams), pre-commit shellcheck.

**Branch:** `feat/egu-254-pi-deploy-kit` off `main` (after this docs PR
merges). Single implementation PR.

**Verified-in-code facts the implementer needs:**

- `gumptionchain mill --peer <peer> <address>` runs forever by default
  (`--blocks` default 0, `src/gumptionchain/command.py` `mill_command`),
  polls the peer between PoW rounds (`src/gumptionchain/miller.py`
  `mill_block` → `poll_latest_blocks`), pushes mined blocks outbound, and
  catches per-block exceptions (the loop survives transient hub outages).
  No Flask server runs on a miller.
- The CLI autoloads `.env` from CWD (python-dotenv); `uv run gumptionchain`
  works from the repo checkout. `uv` for user `gc` lives at
  `/home/gc/.local/bin/uv` (gcm-01 precedent).
- `gumptionchain init` / `db upgrade` need `FLASK_SQLALCHEMY_DATABASE_URI`
  set — installer must not run them before `.env` exists. Use an
  **absolute** sqlite path (relative resolves into `src/instance/`).
- `GC_PEERS` entries are `http(s)://<address>@host` where `<address>` is
  the **local** wallet address the node signs as; that wallet's `.pem`
  must be in `GC_WALLET_DIR` and the hub must allowlist the address in
  its `GC_MILLER_ADDRESSES`.
- Tests run with the suite-wide `error::sqlalchemy.exc.SAWarning` gate;
  the new tests here don't touch the DB at all.
- Ruff only lints `src`/`tests` Python; shell scripts get shellcheck via
  pre-commit (Task 5). Repo has no `deploy/` directory yet.
- `git tag --list 'v*' --sort=-version:refname | head -n 1` resolves the
  highest semver tag (git version-sort handles `v0.10.0 > v0.9.0`).
- systemd `EnvironmentFile` + `ExecStart=... ${VAR}` expands `${VAR}` as
  a single word — correct for peer URLs.

## File structure

```
deploy/pi/
  gumptionchain-miller.service   # Task 1
  gumptionchain-update.service   # Task 1
  gumptionchain-update.timer     # Task 1
  update.sh                      # Task 2
  install.sh                     # Task 3
  provision-appliance.sh         # Task 4
  custom.toml.example            # Task 4
tests/test_deploy_pi.py          # Tasks 1–2
docs/howto-miller-pi.md          # Task 6
docs/pi-appliance-runbook.md     # Task 7
.pre-commit-config.yaml          # Task 5 (modify)
```

---

### Task 1: systemd units + sanity tests

**Files:**
- Create: `deploy/pi/gumptionchain-miller.service`,
  `deploy/pi/gumptionchain-update.service`,
  `deploy/pi/gumptionchain-update.timer`
- Create: `tests/test_deploy_pi.py`

- [ ] **Step 1: Write the failing sanity tests**

```python
"""Sanity checks for the deploy/pi kit (string-level; no systemd in CI)."""

from pathlib import Path

DEPLOY = Path(__file__).parent.parent / 'deploy' / 'pi'


def test_unit_files_exist_and_are_consistent():
    miller = (DEPLOY / 'gumptionchain-miller.service').read_text()
    update = (DEPLOY / 'gumptionchain-update.service').read_text()
    timer = (DEPLOY / 'gumptionchain-update.timer').read_text()
    # the miller runs as the gc user from the repo checkout
    assert 'User=gc' in miller
    assert 'WorkingDirectory=/home/gc/gumptionchain' in miller
    assert 'mill --peer ${GC_MILL_PEER} ${GC_MILL_ADDRESS}' in miller
    assert 'Restart=always' in miller
    # the updater is root (it restarts units and writes /etc) and points
    # at a script that exists in the kit
    assert 'update.sh' in update
    assert 'User=' not in update  # root
    script = update.split('ExecStart=')[1].splitlines()[0].strip()
    assert script == '/home/gc/gumptionchain/deploy/pi/update.sh'
    assert (DEPLOY / 'update.sh').name in script
    # timer fires daily with jitter and catches up after downtime
    assert 'OnCalendar=daily' in timer
    assert 'RandomizedDelaySec=' in timer
    assert 'Persistent=true' in timer


def test_kit_scripts_are_executable():
    for name in ('update.sh', 'install.sh', 'provision-appliance.sh'):
        path = DEPLOY / name
        assert path.exists(), name
        assert path.stat().st_mode & 0o111, f'{name} not executable'
```

(`test_kit_scripts_are_executable` will stay red until Tasks 2–4 land
their scripts; that's expected — run only the first test for this task's
GREEN, and keep the second as the cross-task tracker.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_deploy_pi.py -q`
Expected: FAIL (`FileNotFoundError` — `deploy/pi` doesn't exist)

- [ ] **Step 3: Write the unit files**

`deploy/pi/gumptionchain-miller.service`:

```ini
[Unit]
Description=GumptionChain miller
Wants=network-online.target
After=network-online.target

[Service]
Type=exec
User=gc
WorkingDirectory=/home/gc/gumptionchain
EnvironmentFile=/home/gc/gumptionchain/deploy.env
ExecStart=/home/gc/.local/bin/uv run gumptionchain mill --peer ${GC_MILL_PEER} ${GC_MILL_ADDRESS}
Restart=always
RestartSec=10
# 1 GB Pi 3: a leak degrades to a service restart, not a frozen box.
MemoryMax=700M

[Install]
WantedBy=multi-user.target
```

`deploy/pi/gumptionchain-update.service`:

```ini
[Unit]
Description=GumptionChain channel update
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/home/gc/gumptionchain/deploy.env
ExecStart=/home/gc/gumptionchain/deploy/pi/update.sh
```

`deploy/pi/gumptionchain-update.timer`:

```ini
[Unit]
Description=Daily GumptionChain update check

[Timer]
OnCalendar=daily
RandomizedDelaySec=4h
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Run the first test to verify it passes**

Run: `uv run pytest tests/test_deploy_pi.py::test_unit_files_exist_and_are_consistent -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/pi tests/test_deploy_pi.py
git commit -m "feat(deploy): Pi miller + update systemd units (#254)"
```

---

### Task 2: `update.sh` — channel follower with health gate + rollback

**Files:**
- Create: `deploy/pi/update.sh` (mode 755)
- Modify: `tests/test_deploy_pi.py`

- [ ] **Step 1: Write the failing behavior tests**

Append to `tests/test_deploy_pi.py`:

```python
import os
import shutil
import subprocess

import pytest

UPDATE_SH = DEPLOY / 'update.sh'


def _git(cwd, *args):
    subprocess.run(
        ['git', *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture
def kit(tmp_path):
    """A fixture 'origin' with tags v0.1.0/v0.2.0, a clone on v0.1.0,
    and stubbed seams (uv, systemctl) that log their invocations."""
    origin = tmp_path / 'origin.git'
    work = tmp_path / 'seed'
    work.mkdir()
    _git(work, 'init', '-q', '-b', 'main')
    _git(work, 'config', 'user.email', 't@example.com')
    _git(work, 'config', 'user.name', 't')
    (work / 'deploy' / 'pi').mkdir(parents=True)
    for unit in (
        'gumptionchain-miller.service',
        'gumptionchain-update.service',
        'gumptionchain-update.timer',
    ):
        shutil.copy(DEPLOY / unit, work / 'deploy' / 'pi' / unit)
    (work / 'f.txt').write_text('one\n')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-qm', 'one')
    _git(work, 'tag', 'v0.1.0')
    (work / 'f.txt').write_text('two\n')
    _git(work, 'commit', '-aqm', 'two')
    _git(work, 'tag', 'v0.2.0')
    _git(work, 'clone', '-q', '--bare', '.', str(origin))
    repo = tmp_path / 'repo'
    _git(tmp_path, 'clone', '-q', str(origin), str(repo))
    _git(repo, 'checkout', '-q', 'v0.1.0')

    log = tmp_path / 'calls.log'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text(f'#!/bin/sh\necho "uv $@" >> {log}\nexit 0\n')
    uv.chmod(0o755)
    sysctl = bin_dir / 'systemctl'
    sysctl.write_text(f'#!/bin/sh\necho "systemctl $@" >> {log}\nexit 0\n')
    sysctl.chmod(0o755)

    env = os.environ | {
        'REPO_DIR': str(repo),
        'AS_GC': '',
        'SKIP_FILE': str(tmp_path / 'skip'),
        'UNIT_DIR': str(tmp_path / 'units'),
        'HEALTH_SETTLE': '0',
        'PATH': f'{bin_dir}:{os.environ["PATH"]}',
    }
    (tmp_path / 'units').mkdir()
    return {'repo': repo, 'env': env, 'log': log, 'tmp': tmp_path}


def _head_tag(repo):
    out = subprocess.run(
        ['git', 'describe', '--tags', '--exact-match'],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _run_update(kit, **extra):
    return subprocess.run(
        [str(UPDATE_SH)],
        env=kit['env'] | {k: str(v) for k, v in extra.items()},
        capture_output=True, text=True,
    )


def test_update_follows_highest_tag(kit):
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0, result.stderr
    assert _head_tag(kit['repo']) == 'v0.2.0'
    calls = kit['log'].read_text()
    assert 'uv sync --frozen' in calls
    assert 'gumptionchain db upgrade' in calls
    assert 'systemctl restart gumptionchain-miller' in calls
    assert 'systemctl is-active' in calls


def test_update_noop_when_current(kit):
    _run_update(kit, GC_UPDATE_CHANNEL='tags')
    kit['log'].write_text('')
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0
    assert 'restart' not in kit['log'].read_text()


def test_update_rolls_back_and_skips_bad_tag(kit):
    # health gate fails (is-active exits 1) -> rollback to v0.1.0 + skip
    sysctl = kit['tmp'] / 'bin' / 'systemctl'
    sysctl.write_text(
        f'#!/bin/sh\necho "systemctl $@" >> {kit["log"]}\n'
        'case "$1" in is-active) exit 1;; esac\nexit 0\n'
    )
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode != 0
    assert _head_tag(kit['repo']) == 'v0.1.0'
    skip = (kit['tmp'] / 'skip').read_text()
    assert 'v0.2.0' in skip
    # next run: bad tag is skipped, exit 0, still on v0.1.0
    sysctl.write_text(f'#!/bin/sh\necho "systemctl $@" >> {kit["log"]}\nexit 0\n')
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0
    assert _head_tag(kit['repo']) == 'v0.1.0'


def test_update_branch_channel_follows_main(kit):
    result = _run_update(kit, GC_UPDATE_CHANNEL='main')
    assert result.returncode == 0, result.stderr
    head = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=kit['repo'],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    main = subprocess.run(
        ['git', 'rev-parse', 'origin/main'], cwd=kit['repo'],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == main


def test_update_syncs_changed_unit_files(kit):
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0, result.stderr
    units = kit['tmp'] / 'units'
    assert (units / 'gumptionchain-miller.service').exists()
    assert 'systemctl daemon-reload' in kit['log'].read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_deploy_pi.py -q`
Expected: new tests FAIL (`update.sh` missing)

- [ ] **Step 3: Write `deploy/pi/update.sh`**

```bash
#!/usr/bin/env bash
# GumptionChain Pi updater (#254): follow the release channel, migrate,
# re-sync units, restart, health-gate, roll back on failure. Runs as
# root from gumptionchain-update.service; repo operations run as the
# unprivileged gc user. Seams (REPO_DIR/AS_GC/SKIP_FILE/UNIT_DIR/
# HEALTH_SETTLE and PATH-resolved uv/systemctl) exist for the tests.
set -euo pipefail

GC_USER="${GC_USER:-gc}"
REPO_DIR="${REPO_DIR:-/home/${GC_USER}/gumptionchain}"
CHANNEL="${GC_UPDATE_CHANNEL:-tags}"
SKIP_FILE="${SKIP_FILE:-/home/${GC_USER}/.gumptionchain-skip-tags}"
MILLER_UNIT="${MILLER_UNIT:-gumptionchain-miller}"
AS_GC="${AS_GC-runuser -u ${GC_USER} --}"
HEALTH_SETTLE="${HEALTH_SETTLE:-60}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

cd "$REPO_DIR"

run_gc() {
  if [ -n "$AS_GC" ]; then
    # shellcheck disable=SC2086  # AS_GC is intentionally word-split
    $AS_GC "$@"
  else
    "$@"
  fi
}

current_ref() {
  if [ "$CHANNEL" = 'tags' ]; then
    run_gc git describe --tags --exact-match 2>/dev/null || echo none
  else
    run_gc git rev-parse HEAD
  fi
}

target_ref() {
  if [ "$CHANNEL" = 'tags' ]; then
    run_gc git tag --list 'v*' --sort=-version:refname | head -n 1
  else
    run_gc git rev-parse "origin/${CHANNEL}"
  fi
}

apply() {
  run_gc git checkout --quiet "$1"
  run_gc uv sync --frozen
  run_gc uv run gumptionchain db upgrade
}

sync_units() {
  local changed=0 name
  for unit in deploy/pi/*.service deploy/pi/*.timer; do
    name="$(basename "$unit")"
    if ! cmp -s "$unit" "${UNIT_DIR}/${name}"; then
      cp "$unit" "${UNIT_DIR}/${name}"
      changed=1
    fi
  done
  if [ "$changed" = 1 ]; then
    systemctl daemon-reload
  fi
}

healthy() {
  sleep "$HEALTH_SETTLE"
  systemctl is-active --quiet "$MILLER_UNIT"
}

run_gc git fetch --tags origin

target="$(target_ref)"
if [ -z "$target" ]; then
  echo "no release target on channel ${CHANNEL}"
  exit 0
fi
current="$(current_ref)"
if [ "$target" = "$current" ]; then
  exit 0
fi
if [ "$CHANNEL" = 'tags' ] && [ -f "$SKIP_FILE" ] \
  && grep -qxF "$target" "$SKIP_FILE"; then
  echo "skipping known-bad tag ${target}"
  exit 0
fi

echo "updating ${current} -> ${target}"
if apply "$target" && sync_units \
  && systemctl restart "$MILLER_UNIT" && healthy; then
  echo "updated to ${target}"
  exit 0
fi

echo "update to ${target} failed; rolling back" >&2
if [ "$CHANNEL" = 'tags' ]; then
  echo "$target" >>"$SKIP_FILE"
fi
if [ "$current" != 'none' ]; then
  apply "$current" || true
  sync_units || true
  systemctl restart "$MILLER_UNIT" || true
fi
exit 1
```

`chmod 755 deploy/pi/update.sh`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_deploy_pi.py -q`
Expected: all PASS except `test_kit_scripts_are_executable` (waits on
Tasks 3–4). Also run `bash -n deploy/pi/update.sh` — clean. (shellcheck
arrives as a pre-commit hook in Task 5 and re-gates every kit script.)

- [ ] **Step 5: Commit**

```bash
git add deploy/pi/update.sh tests/test_deploy_pi.py
git commit -m "feat(deploy): channel-following updater with health gate + rollback (#254)"
```

---

### Task 3: `install.sh` — shared idempotent installer

**Files:**
- Create: `deploy/pi/install.sh` (mode 755)

No pytest (needs root + network); gate with `bash -n` + shellcheck, and
the gcm-01 runbook execution validates it for real.

- [ ] **Step 1: Write `deploy/pi/install.sh`**

```bash
#!/usr/bin/env bash
# GumptionChain miller installer (#254). Idempotent; run as root:
#   sudo bash install.sh
# Used verbatim by the roll-your-own HOWTO and by provision-appliance.sh.
set -euo pipefail

GC_USER="${GC_USER:-gc}"
GC_HOME="$(getent passwd "$GC_USER" | cut -d: -f6)"
REPO_DIR="${REPO_DIR:-${GC_HOME}/gumptionchain}"
REPO_URL="${REPO_URL:-https://github.com/gumptionthomas/gumptionchain.git}"
CHANNEL="${GC_UPDATE_CHANNEL:-tags}"
UNIT_DIR='/etc/systemd/system'

if [ "$(id -u)" -ne 0 ]; then
  echo 'run as root: sudo bash install.sh' >&2
  exit 1
fi
if [ -z "$GC_HOME" ]; then
  echo "user ${GC_USER} does not exist; create it first (see HOWTO)" >&2
  exit 1
fi

as_gc() { runuser -u "$GC_USER" -- "$@"; }

apt-get install -y --no-install-recommends git curl ca-certificates

if [ ! -x "${GC_HOME}/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | as_gc sh
fi
export PATH="${GC_HOME}/.local/bin:${PATH}"

if [ ! -d "$REPO_DIR/.git" ]; then
  as_gc git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
as_gc git fetch --tags origin
if [ "$CHANNEL" = 'tags' ]; then
  tag="$(as_gc git tag --list 'v*' --sort=-version:refname | head -n 1)"
  if [ -n "$tag" ]; then
    as_gc git checkout --quiet "$tag"
  else
    echo 'no release tags yet; staying on default branch' >&2
  fi
else
  as_gc git checkout --quiet "$CHANNEL"
  as_gc git pull --ff-only origin "$CHANNEL"
fi
as_gc "${GC_HOME}/.local/bin/uv" sync --frozen

for unit in deploy/pi/*.service deploy/pi/*.timer; do
  cp "$unit" "${UNIT_DIR}/$(basename "$unit")"
done
systemctl daemon-reload
systemctl enable gumptionchain-update.timer

if [ -f "${REPO_DIR}/.env" ] && [ -f "${REPO_DIR}/deploy.env" ]; then
  as_gc "${GC_HOME}/.local/bin/uv" run gumptionchain init
  systemctl enable --now gumptionchain-update.timer gumptionchain-miller
  echo 'miller enabled and started.'
else
  cat <<'EOF'
Installed. Before the miller can start you must:
  1. put your wallet .pem in the wallet dir,
  2. write .env and deploy.env in the repo root (see docs/howto-miller-pi.md),
  3. ask the hub operator to allowlist your address,
then run:
  sudo systemctl enable --now gumptionchain-miller
EOF
fi
```

`chmod 755 deploy/pi/install.sh`

- [ ] **Step 2: Lint**

Run: `bash -n deploy/pi/install.sh`
Expected: clean (shellcheck gates this in Task 5)

- [ ] **Step 3: Commit**

```bash
git add deploy/pi/install.sh
git commit -m "feat(deploy): idempotent miller installer (#254)"
```

---

### Task 4: `provision-appliance.sh` + `custom.toml.example`

**Files:**
- Create: `deploy/pi/provision-appliance.sh` (mode 755)
- Create: `deploy/pi/custom.toml.example`

- [ ] **Step 1: Write `deploy/pi/provision-appliance.sh`**

```bash
#!/usr/bin/env bash
# Bench provisioner (#254): run from the operator workstation against a
# freshly imaged Pi on the bench LAN. Usage:
#   provision-appliance.sh <host> <wallet.pem> <env-file> <deploy-env-file>
# e.g. provision-appliance.sh gcm-07.local secrets/gcm-07.pem \
#        secrets/gcm-07.env secrets/gcm-07.deploy.env
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <host> <wallet.pem> <env-file> <deploy-env-file>" >&2
  exit 64
fi
HOST="$1" WALLET_PEM="$2" ENV_FILE="$3" DEPLOY_ENV="$4"
GC_USER="${GC_USER:-gc}"
REPO_DIR="/home/${GC_USER}/gumptionchain"
WALLET_DIR="/home/${GC_USER}/wallets"
SSH=(ssh "${GC_USER}@${HOST}")

for f in "$WALLET_PEM" "$ENV_FILE" "$DEPLOY_ENV"; do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 66; }
done

echo "==> ${HOST}: base packages + repo + kit install"
"${SSH[@]}" "sudo apt-get update -qq \
  && sudo apt-get install -y --no-install-recommends unattended-upgrades \
  && sudo systemctl enable --now unattended-upgrades"
"${SSH[@]}" "command -v git >/dev/null || sudo apt-get install -y git"
"${SSH[@]}" "[ -d ${REPO_DIR}/.git ] \
  || git clone https://github.com/gumptionthomas/gumptionchain.git ${REPO_DIR}"

echo "==> ${HOST}: secrets + config"
"${SSH[@]}" "mkdir -p ${WALLET_DIR} && chmod 700 ${WALLET_DIR}"
scp "$WALLET_PEM" "${GC_USER}@${HOST}:${WALLET_DIR}/"
scp "$ENV_FILE" "${GC_USER}@${HOST}:${REPO_DIR}/.env"
scp "$DEPLOY_ENV" "${GC_USER}@${HOST}:${REPO_DIR}/deploy.env"
"${SSH[@]}" "chmod 600 ${WALLET_DIR}/* ${REPO_DIR}/.env ${REPO_DIR}/deploy.env"

echo "==> ${HOST}: install + start"
"${SSH[@]}" "sudo bash ${REPO_DIR}/deploy/pi/install.sh"

echo "==> ${HOST}: status"
"${SSH[@]}" "systemctl is-active gumptionchain-miller \
  && journalctl -u gumptionchain-miller -n 20 --no-pager"
echo "==> ${HOST}: provisioned. Begin the 24h bench soak (see runbook)."
```

`chmod 755 deploy/pi/provision-appliance.sh`

- [ ] **Step 2: Write `deploy/pi/custom.toml.example`**

```toml
# Raspberry Pi Imager customization (#254) — HAND-WRITE this file onto
# the boot partition of a freshly flashed Raspberry Pi OS Lite card as
# custom.toml. (The rpi-imager snap silently drops GUI customization;
# do not rely on it.) Verify the schema against the Raspberry Pi OS
# Bookworm documentation when the OS image major-version changes.
config_version = 1

[system]
hostname = "gcm-NN"

[user]
name = "gc"
# generate with: openssl passwd -6
password = "$6$replace$me"

[ssh]
enabled = true
# bench access only; appliances are unreachable behind the member's NAT
authorized_keys = ["ssh-ed25519 AAAA... operator@bench"]

[wlan]
# leave commented for ethernet-only appliances
# ssid = "member-network"
# password = "member-psk"
# country = "US"
```

- [ ] **Step 3: Lint + full deploy tests**

Run: `bash -n deploy/pi/provision-appliance.sh`
Expected: clean (shellcheck gates this in Task 5)
Run: `uv run pytest tests/test_deploy_pi.py -q`
Expected: ALL pass now (including `test_kit_scripts_are_executable`)

- [ ] **Step 4: Commit**

```bash
git add deploy/pi/provision-appliance.sh deploy/pi/custom.toml.example
git commit -m "feat(deploy): bench provisioner + imager customization template (#254)"
```

---

### Task 5: shellcheck in pre-commit

**Files:**
- Modify: `.pre-commit-config.yaml`

- [ ] **Step 1: Add the hook** (append to the existing `repos:` list,
matching the file's existing pinning style — pin a specific rev):

```yaml
  - repo: https://github.com/shellcheck-py/shellcheck-py
    rev: v0.10.0.1
    hooks:
      - id: shellcheck
        files: ^deploy/pi/.*\.sh$
```

- [ ] **Step 2: Verify**

Run: `uv run pre-commit run shellcheck --all-files`
Expected: Passed

- [ ] **Step 3: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: shellcheck pre-commit hook for deploy scripts (#254)"
```

---

### Task 6: `docs/howto-miller-pi.md` (public roll-your-own)

**Files:**
- Create: `docs/howto-miller-pi.md`

- [ ] **Step 1: Write the HOWTO.** Tone/structure like
`docs/api-auth-protocol.md` (definitive, no fluff). Required sections,
each with the exact commands shown:

1. **What you're building** — an outbound-only milling node that peers
   with gumption-hub; no port forwarding, no inbound; ~Pi 3B+ or better,
   ethernet recommended, 16 GB+ SD.
2. **Flash the OS** — Raspberry Pi OS Lite (64-bit); hand-write
   `custom.toml` from `deploy/pi/custom.toml.example` onto the boot
   partition (include the rpi-imager-snap warning); user `gc`.
3. **Install the kit** —
   ```bash
   sudo apt-get install -y git
   git clone https://github.com/gumptionthomas/gumptionchain.git ~/gumptionchain
   sudo bash ~/gumptionchain/deploy/pi/install.sh
   ```
4. **Create a wallet** — `uv run gumptionchain wallet create` from the
   repo dir (implementer: verify the exact `wallet` subcommand name with
   `uv run gumptionchain wallet --help` and document the real one),
   where the `.pem` lands, and **back up the .pem** (it is the only copy
   of your milling rewards).
5. **Get allowlisted** — send your address to the hub operator; your
   address must appear in the hub's `GC_MILLER_ADDRESSES` before blocks
   are accepted.
6. **Configure** — full `.env` example (absolute
   `FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////home/gc/gumptionchain/gumptionchain.db`,
   `FLASK_SECRET_KEY`, `GC_NODE_HOST`, `GC_WALLET_DIR=/home/gc/wallets`,
   `GC_PEERS=["https://<your-address>@hub.gumption.com"]` with the
   username-is-your-own-address explanation) and full `deploy.env`
   example (`GC_MILL_ADDRESS`, `GC_MILL_PEER`, `GC_UPDATE_CHANNEL=tags`).
7. **Start + verify** —
   ```bash
   sudo systemctl enable --now gumptionchain-miller
   journalctl -u gumptionchain-miller -f
   ```
   what healthy milling output looks like; first sync from genesis
   expectations.
8. **Updates** — the auto-update timer (what it does nightly, the
   health-gate + rollback behavior, the skip file), and the manual path:
   ```bash
   cd ~/gumptionchain
   git fetch --tags origin
   git checkout <new-tag>
   uv sync --frozen
   uv run gumptionchain db upgrade
   sudo systemctl restart gumptionchain-miller
   ```
9. **Troubleshooting** — journalctl recipes, `systemctl status`,
   re-sync-from-scratch (delete DB + restart; chain rebuilds from the
   hub), where the skip file lives and how to clear it.

- [ ] **Step 2: Verify commands against the code** — every CLI
invocation in the doc must be checked against `--help` output before
committing (especially the wallet-creation subcommand).

- [ ] **Step 3: Commit**

```bash
git add docs/howto-miller-pi.md
git commit -m "docs: roll-your-own miller Pi HOWTO (#254)"
```

---

### Task 7: `docs/pi-appliance-runbook.md` (operator)

**Files:**
- Create: `docs/pi-appliance-runbook.md`

- [ ] **Step 1: Write the runbook.** Required sections:

1. **Fleet roster** — table: hostname (`gcm-NN`), hardware, location,
   wallet address, channel (`tags`/`main`), shipped date. Seed with
   gcm-01 (canary, `main`).
2. **Wallet ceremony** — generate on the bench workstation (never on the
   Pi); encrypted backup (`age` or `gpg --symmetric` of the `.pem`,
   stored with the operator's other secrets — name the actual location
   pattern, not the secret); add the address to the hub's
   `GC_MILLER_ADDRESSES`; **deliver a key copy to the member** over a
   pre-agreed secure channel — their game backend transacts (faucet
   txns) with the same wallet, and MILLER role covers TRANSACTOR.
3. **Bench provisioning** — flash + `custom.toml` (hostname from the
   roster); boot on bench LAN; run
   `deploy/pi/provision-appliance.sh gcm-NN.local <pem> <env> <deploy.env>`;
   the per-device secrets files live outside the repo (give the
   directory convention, e.g. `~/gc-fleet/gcm-NN/`).
4. **24 h bench soak checklist** — miller active and milling against the
   real hub; at least one block accepted (visible on the hub explorer);
   one forced update-timer run (`sudo systemctl start
   gumptionchain-update.service`) completing cleanly; reboot test
   (services come back unattended); only then ship.
5. **Ship checklist** — what the member receives (Pi, PSU, ethernet
   expectation, "plug into power + router; nothing else"), what to tell
   them (it updates itself; if it dies, mail it back / re-flash).
6. **Release discipline** — tagging is the fleet deploy: soak on gcm-01
   (`GC_UPDATE_CHANNEL=main`) first; never tag a release whose migration
   breaks the previous tag's code (rollback reverts code only);
   annotated tags `vX.Y.Z`.
7. **Recovery flow** — member mails the Pi back or operator mails a new
   SD: re-flash, re-provision with the SAME wallet from the encrypted
   backup; chain DB re-syncs from the hub; roster updated.
8. **First execution = gcm-01** — explicit step list to re-provision
   gcm-01 with this kit (it currently runs an ad-hoc rsync setup),
   set `GC_UPDATE_CHANNEL=main`, and soak one real tag cycle before
   building the first member appliance.

- [ ] **Step 2: Commit**

```bash
git add docs/pi-appliance-runbook.md
git commit -m "docs: managed Pi appliance runbook (#254)"
```

---

### Task 8: Gates + PR

- [ ] `uv run ruff format --check src tests && uv run ruff check src tests`
- [ ] `uv run mypy`
- [ ] `uv run pytest -q` (full suite + the new deploy tests)
- [ ] `uv run pre-commit run --all-files`
- [ ] PR `feat(deploy): Pi miller kit — units, updater, installer, bench
  provisioner + HOWTO/runbook (#254)`; subagent review; hold for author
  review. PR body must note the operational follow-through (gcm-01
  re-provision + first tag) is intentionally not part of the PR.
