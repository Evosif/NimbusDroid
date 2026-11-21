#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${DROID_CLOUD_CONFIG_FILE:-/etc/droid-cloud/droid-cloud.env}"
if [[ -r "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi

STATE_DIR="${DROID_CLOUD_STATE_DIR:-/var/lib/droid-cloud/state}"
PASSWORD_FILE="$STATE_DIR/password"
if [[ ! -s "$PASSWORD_FILE" ]]; then
  printf 'Kata sandi belum dibuat. Jalankan ulang service controller atau %s.\n' \
    "$(dirname -- "$0")/reset-password.sh" >&2
  exit 1
fi
cat "$PASSWORD_FILE"
