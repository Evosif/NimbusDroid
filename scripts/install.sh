#!/usr/bin/env bash
set -Eeuo pipefail

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEFAULT_INSTALL_DIR="/opt/droid-cloud"
readonly DEFAULT_CONFIG_FILE="/etc/droid-cloud/droid-cloud.env"
readonly AWS_CMDLINE_TOOLS_URL="${DROID_CLOUD_CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-16111833_latest.zip}"

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Jalankan installer sebagai root, misalnya: sudo -E %s\n' "$0" >&2
  exit 1
fi

CONFIG_FILE="${DROID_CLOUD_CONFIG_FILE:-$DEFAULT_CONFIG_FILE}"
if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi

INSTALL_DIR="${DROID_CLOUD_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_USER="${DROID_CLOUD_SERVICE_USER:-droid-cloud}"
SERVICE_HOME="${DROID_CLOUD_HOME:-/var/lib/droid-cloud}"
SDK_ROOT="${DROID_CLOUD_SDK_ROOT:-/opt/android-sdk}"
PUBLIC_HOST="${DROID_CLOUD_PUBLIC_HOST:-}"
SSH_USER="${DROID_CLOUD_SSH_USER:-${SUDO_USER:-ubuntu}}"
WEB_BIND="${DROID_CLOUD_WEB_BIND:-127.0.0.1:8080}"
API_PORT="${DROID_CLOUD_API_PORT:-8081}"
INSTALL_SDK="${DROID_CLOUD_INSTALL_ANDROID_SDK:-1}"
INSTALL_IMAGE="${DROID_CLOUD_INSTALL_SYSTEM_IMAGE:-1}"
SYSTEM_IMAGE="${DROID_CLOUD_SYSTEM_IMAGE:-system-images;android-35;google_apis_playstore;x86_64}"

if [[ -z "$PUBLIC_HOST" ]]; then
  printf 'DROID_CLOUD_PUBLIC_HOST wajib diisi, contoh: sudo -E DROID_CLOUD_PUBLIC_HOST=android.example.com %s\n' "$0" >&2
  exit 1
fi
if [[ ! "$PUBLIC_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
  printf 'DROID_CLOUD_PUBLIC_HOST tidak valid: %s\n' "$PUBLIC_HOST" >&2
  exit 1
fi
if [[ ! "$API_PORT" =~ ^[0-9]+$ ]]; then
  printf 'DROID_CLOUD_API_PORT tidak valid: %s\n' "$API_PORT" >&2
  exit 1
fi
API_PORT=$((10#$API_PORT))
if (( API_PORT < 1 || API_PORT > 65535 )); then
  printf 'DROID_CLOUD_API_PORT tidak valid: %s\n' "$API_PORT" >&2
  exit 1
fi
if [[ ! "$WEB_BIND" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  printf 'DROID_CLOUD_WEB_BIND harus berupa loopback IPv4, misalnya 127.0.0.1:8080.\n' >&2
  exit 1
fi
WEB_PORT=$((10#${BASH_REMATCH[1]}))
if (( WEB_PORT < 1 || WEB_PORT > 65535 )) || [[ "$WEB_PORT" == "$API_PORT" ]]; then
  printf 'Port web harus valid dan berbeda dari DROID_CLOUD_API_PORT.\n' >&2
  exit 1
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  printf 'Installer saat ini membutuhkan server x86_64 dengan KVM.\n' >&2
  exit 1
fi
if [[ ! -e /dev/kvm ]]; then
  printf '/dev/kvm tidak tersedia. Aktifkan virtualisasi atau nested virtualization terlebih dahulu.\n' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  caddy \
  curl \
  jq \
  openjdk-21-jre-headless \
  openssh-server \
  python3 \
  rsync \
  scrcpy \
  unzip \
  websockify \
  x11vnc \
  xvfb

if ! getent group kvm >/dev/null; then
  groupadd --system kvm
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$SERVICE_HOME" --shell /bin/bash "$SERVICE_USER"
fi
usermod -aG kvm "$SERVICE_USER"

install -d -m 0755 -o root -g root "$INSTALL_DIR"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'tests/.artifacts/' \
  "$SOURCE_DIR/" "$INSTALL_DIR/"
chmod -R u=rwX,go=rX "$INSTALL_DIR"

install -d -m 0755 -o root -g root "$(dirname "$CONFIG_FILE")"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$SERVICE_HOME" \
  "$SERVICE_HOME/.android" \
  "${DROID_CLOUD_AVD_HOME:-$SERVICE_HOME/.android/avd}" \
  "${DROID_CLOUD_STATE_DIR:-$SERVICE_HOME/state}" \
  "${DROID_CLOUD_CACHE_DIR:-$SERVICE_HOME/cache}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  cat > "$CONFIG_FILE" <<EOF
DROID_CLOUD_PUBLIC_HOST=$PUBLIC_HOST
DROID_CLOUD_SSH_USER=$SSH_USER
DROID_CLOUD_HOME=$SERVICE_HOME
DROID_CLOUD_SDK_ROOT=$SDK_ROOT
DROID_CLOUD_AVD_HOME=${DROID_CLOUD_AVD_HOME:-$SERVICE_HOME/.android/avd}
DROID_CLOUD_STATE_DIR=${DROID_CLOUD_STATE_DIR:-$SERVICE_HOME/state}
DROID_CLOUD_CACHE_DIR=${DROID_CLOUD_CACHE_DIR:-$SERVICE_HOME/cache}
DROID_CLOUD_WEB_ROOT=$INSTALL_DIR/web
DROID_CLOUD_WEB_BIND=$WEB_BIND
DROID_CLOUD_API_HOST=127.0.0.1
DROID_CLOUD_API_PORT=$API_PORT
DROID_CLOUD_ALLOWED_ORIGINS=https://$PUBLIC_HOST
EOF
fi
chmod 0640 "$CONFIG_FILE"
chown root:"$SERVICE_USER" "$CONFIG_FILE"

# shellcheck disable=SC1090
set -a
source "$CONFIG_FILE"
set +a

install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$SDK_ROOT" \
  "$SDK_ROOT/cmdline-tools"

if [[ "$INSTALL_SDK" == "1" && ! -x "$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" ]]; then
  sdk_tmp="$(mktemp -d)"
  trap 'rm -rf -- "$sdk_tmp"' EXIT
  curl --fail --location --retry 3 --output "$sdk_tmp/cmdline-tools.zip" "$AWS_CMDLINE_TOOLS_URL"
  unzip -q "$sdk_tmp/cmdline-tools.zip" -d "$sdk_tmp/unpacked"
  runuser -u "$SERVICE_USER" -- install -d -m 0755 "$SDK_ROOT/cmdline-tools/latest"
  rsync -a "$sdk_tmp/unpacked/cmdline-tools/" "$SDK_ROOT/cmdline-tools/latest/"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$SDK_ROOT/cmdline-tools/latest"
fi

if [[ "$INSTALL_SDK" == "1" ]]; then
  sdkmanager="$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"
  if [[ ! -x "$sdkmanager" ]]; then
    printf 'sdkmanager tidak ditemukan di %s\n' "$sdkmanager" >&2
    exit 1
  fi
  printf 'y\n%.0s' {1..100} | runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    ANDROID_HOME="$SDK_ROOT" \
    ANDROID_SDK_ROOT="$SDK_ROOT" \
    "$sdkmanager" --licenses >/dev/null
  packages=("platform-tools" "emulator")
  if [[ "$INSTALL_IMAGE" == "1" ]]; then
    packages+=("$SYSTEM_IMAGE")
  fi
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    ANDROID_HOME="$SDK_ROOT" \
    ANDROID_SDK_ROOT="$SDK_ROOT" \
    "$sdkmanager" "${packages[@]}"
fi

python3 "$INSTALL_DIR/scripts/render-template.py" \
  "$INSTALL_DIR/deploy/systemd/droid-cloud-controller.service.in" \
  "/etc/systemd/system/droid-cloud-controller.service" \
  "SERVICE_USER=$SERVICE_USER" \
  "CONFIG_FILE=$CONFIG_FILE" \
  "INSTALL_DIR=$INSTALL_DIR" \
  "PYTHON=$(command -v python3)"

python3 "$INSTALL_DIR/scripts/render-template.py" \
  "$INSTALL_DIR/deploy/systemd/droid-cloud-web.service.in" \
  "/etc/systemd/system/droid-cloud-web.service" \
  "SERVICE_USER=$SERVICE_USER" \
  "CONFIG_FILE=$CONFIG_FILE" \
  "WEBSOCKIFY=$(command -v websockify)"

python3 "$INSTALL_DIR/scripts/render-template.py" \
  "$INSTALL_DIR/deploy/Caddyfile.template" \
  "/etc/caddy/Caddyfile" \
  "PUBLIC_HOST=$PUBLIC_HOST" \
  "API_PORT=$API_PORT" \
  "WEB_PORT=$WEB_PORT"

install -d -m 0755 /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/droid-cloud.conf <<EOF
[Unit]
Requires=droid-cloud-web.service
After=droid-cloud-web.service
EOF

caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now droid-cloud-controller.service droid-cloud-web.service
systemctl restart droid-cloud-controller.service
systemctl enable --now caddy.service
systemctl restart caddy.service

printf '\nDroid Cloud terpasang.\n'
printf 'Dashboard : https://%s\n' "$PUBLIC_HOST"
printf 'Password  : sudo %s/scripts/show-password.sh\n' "$INSTALL_DIR"
printf 'Health    : sudo %s/scripts/verify.sh\n' "$INSTALL_DIR"
printf 'Logs      : journalctl -u droid-cloud-controller -u droid-cloud-web -u caddy\n'
