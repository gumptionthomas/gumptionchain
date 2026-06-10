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
