#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

python3 -m compileall -q controller tests
python3 -m unittest discover -s tests -v
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
  if [ -f web/farm.js ]; then
    node --check web/farm.js
  fi
  if [ -f web/location-map.js ]; then
    node --check web/location-map.js
  fi
else
  printf '[skip] Node.js tidak tersedia untuk syntax check frontend.\n'
fi
bash -n scripts/install.sh
bash -n scripts/verify.sh
bash -n scripts/show-password.sh
bash -n scripts/reset-password.sh

if grep -RInE '/home/ubuntu|54\.238\.137\.23|nip\.io' controller web \
  --exclude='*.woff2'; then
  printf 'Ditemukan path atau host spesifik instalasi lama.\n' >&2
  exit 1
fi

if git ls-files --error-unmatch password auth.json tokens '*.pem' >/dev/null 2>&1; then
  printf 'File rahasia masuk ke indeks Git.\n' >&2
  exit 1
fi

printf 'Pemeriksaan statis berhasil.\n'
