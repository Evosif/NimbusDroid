# Panduan Instalasi Cepat

Panduan ini memasang Droid Cloud dari nol pada server Ubuntu x86_64 yang
memiliki KVM. DNS, port 80/443, dan akses SSH harus disiapkan sebelum
instalasi.

## Yang Dibutuhkan

- Ubuntu 24.04 LTS atau lebih baru.
- Akses `sudo`.
- `/dev/kvm` tersedia.
- Minimal 4 vCPU, RAM 8 GB, dan ruang disk 80 GB untuk VM pertama.
- Hostname yang sudah menunjuk ke IP server, misalnya
  `android.example.com`.
- Port TCP 80 dan 443 terbuka.

Periksa KVM:

```bash
ls -l /dev/kvm
grep -E -m1 '(vmx|svm)' /proc/cpuinfo
```

## Clone Repo

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/OWNER/droid-cloud.git
cd droid-cloud
```

## Pasang

```bash
sudo -E DROID_CLOUD_PUBLIC_HOST=android.example.com \
  ./scripts/install.sh
```

Installer memasang paket sistem, Android SDK, image Android 15 Google Play,
service systemd, dan konfigurasi HTTPS Caddy.

## Ambil Kata Sandi

```bash
sudo /opt/droid-cloud/scripts/show-password.sh
```

Buka:

```text
https://android.example.com
```

## Cek Instalasi

```bash
sudo /opt/droid-cloud/scripts/verify.sh
sudo journalctl -u droid-cloud-controller -u droid-cloud-web -u caddy -n 100
```

## Buat VM Pertama

1. Masuk ke dashboard.
2. Pilih **Buat VM**.
3. Pilih perangkat dan versi Android.
4. Gunakan Google Play jika hanya membutuhkan pengalaman Play Store.
5. Tunggu status berubah menjadi **Running**.

## Akses ADB

ADB tidak terbuka ke internet. Gunakan perintah tunnel yang ditampilkan
dashboard:

```bash
ssh -i KUNCI_ANDA.pem -N \
  -L 127.0.0.1:5555:127.0.0.1:5555 \
  ubuntu@android.example.com
```

Di terminal kedua:

```bash
adb connect 127.0.0.1:5555
adb devices
```

Jangan mengunggah private key ke repo atau mengirim isinya di chat.

## Lokasi Data

```text
/opt/droid-cloud
/opt/android-sdk
/var/lib/droid-cloud/.android/avd
/var/lib/droid-cloud/state
/var/lib/droid-cloud/cache
/etc/droid-cloud/droid-cloud.env
```

Backup `state` dan `avd` secara bersamaan. Keduanya diperlukan untuk
memulihkan VM.

## Perintah Operasional

```bash
# Status service
systemctl status droid-cloud-controller droid-cloud-web caddy --no-pager

# Log
journalctl -u droid-cloud-controller -f
journalctl -u droid-cloud-web -f
journalctl -u caddy -f

# Reset kata sandi dashboard
sudo /opt/droid-cloud/scripts/reset-password.sh

# Cek kesehatan
sudo /opt/droid-cloud/scripts/verify.sh
```

## Upgrade

```bash
cd /path/to/droid-cloud
git pull --ff-only
sudo -E DROID_CLOUD_INSTALL_ANDROID_SDK=0 ./scripts/install.sh
sudo /opt/droid-cloud/scripts/verify.sh
```

## Masalah Umum

### Dashboard 502

```bash
curl -v http://127.0.0.1:8081/api/health
journalctl -u droid-cloud-controller -n 100
journalctl -u droid-cloud-web -n 100
```

### Sertifikat HTTPS Gagal

```bash
getent ahostsv4 android.example.com
journalctl -u caddy -n 150
```

Pastikan hostname menunjuk ke server dan port 80/443 dapat dijangkau.

### KVM Tidak Ada

Aktifkan virtualisasi pada instance cloud. VM tanpa `/dev/kvm` tidak didukung.

### VM Stuck Starting

```bash
journalctl -u droid-cloud-controller -f
tail -f /var/lib/droid-cloud/cache/logs/VM_ID-emulator.log
```

Boot pertama dapat memerlukan beberapa menit.

Untuk detail lanjutan, lihat
[DEPLOYMENT.md](DEPLOYMENT.md), [OPERATIONS.md](OPERATIONS.md), dan
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).
