#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${DROID_CLOUD_CONFIG_FILE:-/etc/droid-cloud/droid-cloud.env}"
if [[ ! -r "$CONFIG_FILE" ]]; then
  printf 'Konfigurasi tidak ditemukan: %s\n' "$CONFIG_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

failures=0
check() {
  local label="$1"
  shift
  if "$@"; then
    printf '[ok]   %s\n' "$label"
  else
    printf '[fail] %s\n' "$label"
    failures=$((failures + 1))
  fi
}

check "service controller" systemctl is-active --quiet droid-cloud-controller.service
check "service web" systemctl is-active --quiet droid-cloud-web.service
check "service caddy" systemctl is-active --quiet caddy.service
check "KVM device" test -c /dev/kvm
check "ADB executable" test -x "$DROID_CLOUD_SDK_ROOT/platform-tools/adb"
check "emulator executable" test -x "$DROID_CLOUD_SDK_ROOT/emulator/emulator"
check "controller health" curl --fail --silent \
  --max-time 10 \
  "http://127.0.0.1:${DROID_CLOUD_API_PORT}/api/health"
check "public dashboard" curl --fail --silent --location \
  --max-time 20 \
  --output /dev/null "https://${DROID_CLOUD_PUBLIC_HOST}/"

if (( failures > 0 )); then
  printf '\n%d pemeriksaan gagal. Lihat journalctl untuk detail.\n' "$failures" >&2
  exit 1
fi
printf '\nSemua pemeriksaan berhasil.\n'
