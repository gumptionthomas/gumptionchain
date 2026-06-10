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
