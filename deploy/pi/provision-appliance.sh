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
