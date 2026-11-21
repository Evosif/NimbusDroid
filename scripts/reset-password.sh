#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Jalankan sebagai root: sudo %s\n' "$0" >&2
  exit 1
fi

CONFIG_FILE="${DROID_CLOUD_CONFIG_FILE:-/etc/droid-cloud/droid-cloud.env}"
if [[ ! -r "$CONFIG_FILE" ]]; then
  printf 'Konfigurasi tidak ditemukan: %s\n' "$CONFIG_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

STATE_DIR="${DROID_CLOUD_STATE_DIR:-/var/lib/droid-cloud/state}"
state_owner="$(stat -c '%U:%G' "$STATE_DIR")"
state_user="${state_owner%%:*}"
state_group="${state_owner##*:}"
new_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
password_tmp="$(mktemp /tmp/droid-cloud-password.XXXXXX)"
trap 'rm -f -- "$password_tmp"' EXIT
printf '%s\n' "$new_password" > "$password_tmp"
chmod 0600 "$password_tmp"
chown "$state_user:$state_group" "$password_tmp"
runuser -u "$state_user" -- install -m 0600 "$password_tmp" "$STATE_DIR/password"

if [[ -e "$STATE_DIR/auth.json" ]]; then
  mv "$STATE_DIR/auth.json" "$STATE_DIR/auth.json.bak"
fi

systemctl restart droid-cloud-controller.service
printf 'Kata sandi dashboard baru: %s\n' "$new_password"
