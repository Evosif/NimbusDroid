"""Community real-device fingerprint catalog, identity generator, and ISO catalog."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import secrets
import shlex
import string
import urllib.request
from pathlib import Path
from typing import Any

from settings import ISO_DIR, STATE_DIR

CUSTOM_FINGERPRINTS_FILE = STATE_DIR / "fingerprints_custom.json"

CARRIERS: list[dict[str, str]] = [
    {"id": "telkomsel", "name": "Telkomsel", "numeric": "51010", "iso": "id", "country": "Indonesia"},
    {"id": "indosat", "name": "Indosat Ooredoo", "numeric": "51001", "iso": "id", "country": "Indonesia"},
    {"id": "xl", "name": "XL Axiata", "numeric": "51011", "iso": "id", "country": "Indonesia"},
    {"id": "singtel", "name": "Singtel", "numeric": "52501", "iso": "sg", "country": "Singapore"},
    {"id": "tmobile", "name": "T-Mobile", "numeric": "310260", "iso": "us", "country": "United States"},
    {"id": "att", "name": "AT&T", "numeric": "310410", "iso": "us", "country": "United States"},
    {"id": "docomo", "name": "NTT DOCOMO", "numeric": "44010", "iso": "jp", "country": "Japan"},
    {"id": "vodafone", "name": "Vodafone UK", "numeric": "23415", "iso": "gb", "country": "United Kingdom"},
]
CARRIER_BY_ID = {c["id"]: c for c in CARRIERS}

ISO_CATALOG: list[dict[str, Any]] = [
    {
        "id": "blissos-16-gapps",
        "name": "BlissOS 16.9.7 Gapps (Android 13)",
        "filename": "Bliss-v16.9.7-x86_64-OFFICIAL-gapps-20241011.iso",
        "androidVersion": "Android 13",
        "apiLevel": 33,
        "variant": "Google Play + ARM64 Native Bridge",
        "sizeLabel": "~2.3 GB",
        "url": "https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS16/Gapps/Generic/Bliss-v16.9.7-x86_64-OFFICIAL-gapps-20241011.iso/download",
        "source": "BlissOS Official",
        "recommendedProfile": "pixel-7-pro-a13",
    },
    {
        "id": "blissos-15-gapps",
        "name": "BlissOS 15.9.2 Gapps (Android 12L)",
        "filename": "Bliss-v15.9.2-x86_64-OFFICIAL-gapps-20241012.iso",
        "androidVersion": "Android 12L",
        "apiLevel": 32,
        "variant": "Google Play + ARM64 Native Bridge",
        "sizeLabel": "~2.1 GB",
        "url": "https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS15/Gapps/Generic/Bliss-v15.9.2-x86_64-OFFICIAL-gapps-20241012.iso/download",
        "source": "BlissOS Official",
        "recommendedProfile": "pixel-7-pro-a13",
    },
    {
        "id": "blissos-16-foss",
        "name": "BlissOS 16.9.7 FOSS (Android 13)",
        "filename": "Bliss-v16.9.7-x86_64-OFFICIAL-foss-20241011.iso",
        "androidVersion": "Android 13",
        "apiLevel": 33,
        "variant": "Open Source + microG + ARM64",
        "sizeLabel": "~1.7 GB",
        "url": "https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS16/FOSS/Generic/Bliss-v16.9.7-x86_64-OFFICIAL-foss-20241011.iso/download",
        "source": "BlissOS Official",
        "recommendedProfile": "pixel-7-pro-a13",
    },
]

BUILTIN_PROFILES: list[dict[str, Any]] = [
    {
        "id": "pixel-9-pro-a16",
        "label": "Google Pixel 9 Pro (Android 16)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 9 Pro",
        "marketname": "Pixel 9 Pro",
        "device": "caiman",
        "product": "caiman",
        "hardware": "caiman",
        "board": "caiman",
        "platform": "zumapro",
        "bootloader": "caiman-16.0-14624750",
        "baseband": "g5400c-250115-250205-B-14624750",
        "socManufacturer": "Google",
        "socModel": "Tensor G4",
        "release": "16",
        "apiLevel": 36,
        "firstApiLevel": 34,
        "buildId": "BP4A.260205.002.A1",
        "incremental": "14624750",
        "securityPatch": "2026-02-05",
        "fingerprint": "google/caiman/caiman:16/BP4A.260205.002.A1/14624750:user/release-keys",
        "tac": "35847631",
        "width": 1280,
        "height": 2856,
        "density": 480,
    },
    {
        "id": "pixel-9-pro-a15",
        "label": "Google Pixel 9 Pro (Android 15)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 9 Pro",
        "marketname": "Pixel 9 Pro",
        "device": "caiman",
        "product": "caiman",
        "hardware": "caiman",
        "board": "caiman",
        "platform": "zumapro",
        "bootloader": "caiman-15.2-13277947",
        "baseband": "g5400c-250304-250410-B-13277947",
        "socManufacturer": "Google",
        "socModel": "Tensor G4",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 34,
        "buildId": "BP1A.250505.005.D1",
        "incremental": "13277947",
        "securityPatch": "2025-05-05",
        "fingerprint": "google/caiman/caiman:15/BP1A.250505.005.D1/13277947:user/release-keys",
        "tac": "35847631",
        "width": 1280,
        "height": 2856,
        "density": 480,
    },
    {
        "id": "pixel-9-pro-xl-a16",
        "label": "Google Pixel 9 Pro XL (Android 16)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 9 Pro XL",
        "marketname": "Pixel 9 Pro XL",
        "device": "komodo",
        "product": "komodo",
        "hardware": "komodo",
        "board": "komodo",
        "platform": "zumapro",
        "bootloader": "komodo-16.0-14624750",
        "baseband": "g5400c-250115-250205-B-14624750",
        "socManufacturer": "Google",
        "socModel": "Tensor G4",
        "release": "16",
        "apiLevel": 36,
        "firstApiLevel": 34,
        "buildId": "BP4A.260205.002.A1",
        "incremental": "14624750",
        "securityPatch": "2026-02-05",
        "fingerprint": "google/komodo/komodo:16/BP4A.260205.002.A1/14624750:user/release-keys",
        "tac": "35912468",
        "width": 1344,
        "height": 2992,
        "density": 486,
    },
    {
        "id": "pixel-8-pro-a16",
        "label": "Google Pixel 8 Pro (Android 16)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 8 Pro",
        "marketname": "Pixel 8 Pro",
        "device": "husky",
        "product": "husky",
        "hardware": "husky",
        "board": "husky",
        "platform": "zuma",
        "bootloader": "ripcurrent-16.0-14624666",
        "baseband": "g5300q-250110-250201-B-14624666",
        "socManufacturer": "Google",
        "socModel": "Tensor G3",
        "release": "16",
        "apiLevel": 36,
        "firstApiLevel": 34,
        "buildId": "BP4A.260205.001",
        "incremental": "14624666",
        "securityPatch": "2026-02-05",
        "fingerprint": "google/husky/husky:16/BP4A.260205.001/14624666:user/release-keys",
        "tac": "35542188",
        "width": 1344,
        "height": 2992,
        "density": 489,
    },
    {
        "id": "pixel-8-pro-a15",
        "label": "Google Pixel 8 Pro (Android 15)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 8 Pro",
        "marketname": "Pixel 8 Pro",
        "device": "husky",
        "product": "husky",
        "hardware": "husky",
        "board": "husky",
        "platform": "zuma",
        "bootloader": "ripcurrent-15.2-13277630",
        "baseband": "g5300q-250214-250410-B-13277630",
        "socManufacturer": "Google",
        "socModel": "Tensor G3",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 34,
        "buildId": "BP1A.250505.005.B1",
        "incremental": "13277630",
        "securityPatch": "2025-05-05",
        "fingerprint": "google/husky/husky:15/BP1A.250505.005.B1/13277630:user/release-keys",
        "tac": "35542188",
        "width": 1344,
        "height": 2992,
        "density": 489,
    },
    {
        "id": "pixel-8-a15",
        "label": "Google Pixel 8 (Android 15)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 8",
        "marketname": "Pixel 8",
        "device": "shiba",
        "product": "shiba",
        "hardware": "shiba",
        "board": "shiba",
        "platform": "zuma",
        "bootloader": "ripcurrent-15.2-13277630",
        "baseband": "g5300q-250214-250410-B-13277630",
        "socManufacturer": "Google",
        "socModel": "Tensor G3",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 34,
        "buildId": "BP1A.250505.005.B1",
        "incremental": "13277630",
        "securityPatch": "2025-05-05",
        "fingerprint": "google/shiba/shiba:15/BP1A.250505.005.B1/13277630:user/release-keys",
        "tac": "35498217",
        "width": 1080,
        "height": 2400,
        "density": 420,
    },
    {
        "id": "pixel-8a-a16",
        "label": "Google Pixel 8a (Android 16)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 8a",
        "marketname": "Pixel 8a",
        "device": "akita",
        "product": "akita",
        "hardware": "akita",
        "board": "akita",
        "platform": "zuma",
        "bootloader": "ripcurrent-16.0-14624666",
        "baseband": "g5300q-250110-250201-B-14624666",
        "socManufacturer": "Google",
        "socModel": "Tensor G3",
        "release": "16",
        "apiLevel": 36,
        "firstApiLevel": 34,
        "buildId": "BP4A.260205.001",
        "incremental": "14624666",
        "securityPatch": "2026-02-05",
        "fingerprint": "google/akita/akita:16/BP4A.260205.001/14624666:user/release-keys",
        "tac": "35736420",
        "width": 1080,
        "height": 2400,
        "density": 430,
    },
    {
        "id": "pixel-7-pro-a13",
        "label": "Google Pixel 7 Pro (Android 13)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 7 Pro",
        "marketname": "Pixel 7 Pro",
        "device": "cheetah",
        "product": "cheetah",
        "hardware": "cheetah",
        "board": "cheetah",
        "platform": "gs201",
        "bootloader": "cloudripper-1.0-10753682",
        "baseband": "g5300q-230626-230818-B-10753682",
        "socManufacturer": "Google",
        "socModel": "Tensor G2",
        "release": "13",
        "apiLevel": 33,
        "firstApiLevel": 33,
        "buildId": "TQ3A.230901.001.C2",
        "incremental": "10753682",
        "securityPatch": "2023-09-01",
        "fingerprint": "google/cheetah/cheetah:13/TQ3A.230901.001.C2/10753682:user/release-keys",
        "tac": "35185089",
        "width": 1440,
        "height": 3120,
        "density": 512,
    },
    {
        "id": "pixel-6-a15",
        "label": "Google Pixel 6 (Android 15)",
        "source": "Google Official OTA",
        "brand": "google",
        "manufacturer": "Google",
        "model": "Pixel 6",
        "marketname": "Pixel 6",
        "device": "oriole",
        "product": "oriole",
        "hardware": "oriole",
        "board": "oriole",
        "platform": "gs101",
        "bootloader": "slider-15.2-13277524",
        "baseband": "g5123b-135617-250314-B-13277524",
        "socManufacturer": "Google",
        "socModel": "Tensor",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 31,
        "buildId": "BP1A.250505.005",
        "incremental": "13277524",
        "securityPatch": "2025-05-05",
        "fingerprint": "google/oriole/oriole:15/BP1A.250505.005/13277524:user/release-keys",
        "tac": "35461487",
        "width": 1080,
        "height": 2400,
        "density": 420,
    },
    {
        "id": "samsung-s24-ultra",
        "label": "Samsung Galaxy S24 Ultra (SM-S928B)",
        "source": "Community Certified",
        "brand": "samsung",
        "manufacturer": "samsung",
        "model": "SM-S928B",
        "marketname": "Galaxy S24 Ultra",
        "device": "e3q",
        "product": "e3qxxx",
        "hardware": "qcom",
        "board": "pineapple",
        "platform": "pineapple",
        "bootloader": "S928BXXS3AXFE",
        "baseband": "S928BXXS3AXFE",
        "socManufacturer": "Qualcomm",
        "socModel": "SM8650",
        "release": "14",
        "apiLevel": 34,
        "firstApiLevel": 34,
        "buildId": "UP1A.231005.007",
        "incremental": "S928BXXS3AXFE",
        "securityPatch": "2024-07-01",
        "fingerprint": "samsung/e3qxxx/e3q:14/UP1A.231005.007/S928BXXS3AXFE:user/release-keys",
        "tac": "35046842",
        "width": 1440,
        "height": 3120,
        "density": 560,
    },
    {
        "id": "samsung-s24",
        "label": "Samsung Galaxy S24 (SM-S921B)",
        "source": "Community Certified",
        "brand": "samsung",
        "manufacturer": "samsung",
        "model": "SM-S921B",
        "marketname": "Galaxy S24",
        "device": "e1s",
        "product": "e1sxxx",
        "hardware": "s5e9945",
        "board": "s5e9945",
        "platform": "s5e9945",
        "bootloader": "S921BXXS3AXFE",
        "baseband": "S921BXXS3AXFE",
        "socManufacturer": "Samsung",
        "socModel": "s5e9945",
        "release": "14",
        "apiLevel": 34,
        "firstApiLevel": 34,
        "buildId": "UP1A.231005.007",
        "incremental": "S921BXXS3AXFE",
        "securityPatch": "2024-07-01",
        "fingerprint": "samsung/e1sxxx/e1s:14/UP1A.231005.007/S921BXXS3AXFE:user/release-keys",
        "tac": "35046711",
        "width": 1080,
        "height": 2340,
        "density": 450,
    },
    {
        "id": "samsung-a55",
        "label": "Samsung Galaxy A55 5G (SM-A556E)",
        "source": "Community Certified",
        "brand": "samsung",
        "manufacturer": "samsung",
        "model": "SM-A556E",
        "marketname": "Galaxy A55 5G",
        "device": "a55x",
        "product": "a55xnsxx",
        "hardware": "s5e8845",
        "board": "s5e8845",
        "platform": "s5e8845",
        "bootloader": "A556EXXS4AXH1",
        "baseband": "A556EXXS4AXH1",
        "socManufacturer": "Samsung",
        "socModel": "s5e8845",
        "release": "14",
        "apiLevel": 34,
        "firstApiLevel": 34,
        "buildId": "UP1A.231005.007",
        "incremental": "A556EXXS4AXH1",
        "securityPatch": "2024-08-01",
        "fingerprint": "samsung/a55xnsxx/a55x:14/UP1A.231005.007/A556EXXS4AXH1:user/release-keys",
        "tac": "35412890",
        "width": 1080,
        "height": 2340,
        "density": 420,
    },
    {
        "id": "poco-f5-a15",
        "label": "Xiaomi POCO F5 (Android 15)",
        "source": "LineageOS Community",
        "brand": "POCO",
        "manufacturer": "Xiaomi",
        "model": "23049PCD8G",
        "marketname": "POCO F5",
        "device": "marble",
        "product": "marble_global",
        "hardware": "qcom",
        "board": "taro",
        "platform": "taro",
        "bootloader": "marble-2.0.205.0",
        "baseband": "MPSS.DE.2.0.c1-00491",
        "socManufacturer": "Qualcomm",
        "socModel": "SM7475",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 33,
        "buildId": "AQ3A.241006.001",
        "incremental": "OS2.0.205.0.VMRMIXM",
        "securityPatch": "2025-05-01",
        "fingerprint": "POCO/marble_global/marble:15/AQ3A.241006.001/OS2.0.205.0.VMRMIXM:user/release-keys",
        "tac": "86429306",
        "width": 1080,
        "height": 2400,
        "density": 440,
    },
    {
        "id": "xiaomi-13-pro-a15",
        "label": "Xiaomi 13 Pro (Android 15)",
        "source": "LineageOS Community",
        "brand": "Xiaomi",
        "manufacturer": "Xiaomi",
        "model": "2210132C",
        "marketname": "Xiaomi 13 Pro",
        "device": "nuwa",
        "product": "nuwa",
        "hardware": "qcom",
        "board": "kalama",
        "platform": "kalama",
        "bootloader": "nuwa-2.0.215.0",
        "baseband": "MPSS.DE.3.0.c1-00381",
        "socManufacturer": "Qualcomm",
        "socModel": "SM8550",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 33,
        "buildId": "AQ3A.240912.001",
        "incremental": "OS2.0.215.0.VMBCNXM",
        "securityPatch": "2025-05-01",
        "fingerprint": "Xiaomi/nuwa/nuwa:15/AQ3A.240912.001/OS2.0.215.0.VMBCNXM:user/release-keys",
        "tac": "86571206",
        "width": 1440,
        "height": 3200,
        "density": 560,
    },
    {
        "id": "oneplus-12-a15",
        "label": "OnePlus 12 (Android 15)",
        "source": "LineageOS Community",
        "brand": "OnePlus",
        "manufacturer": "OnePlus",
        "model": "CPH2581",
        "marketname": "OnePlus 12",
        "device": "OP595DL1",
        "product": "CPH2581EEA",
        "hardware": "qcom",
        "board": "pineapple",
        "platform": "pineapple",
        "bootloader": "OP595DL1-15.0.0",
        "baseband": "Q_V1_P14",
        "socManufacturer": "Qualcomm",
        "socModel": "SM8650",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 34,
        "buildId": "AP3A.240617.008",
        "incremental": "U.R4T3.cfc6a2_1337040_1337041",
        "securityPatch": "2025-04-05",
        "fingerprint": "OnePlus/CPH2581EEA/OP595DL1:15/AP3A.240617.008/U.R4T3.cfc6a2_1337040_1337041:user/release-keys",
        "tac": "86891406",
        "width": 1440,
        "height": 3168,
        "density": 510,
    },
    {
        "id": "sony-xperia-1v-a15",
        "label": "Sony Xperia 1 V (Android 15)",
        "source": "LineageOS Community",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XQ-DQ72",
        "marketname": "Xperia 1 V",
        "device": "XQ-DQ72",
        "product": "XQ-DQ72",
        "hardware": "qcom",
        "board": "kalama",
        "platform": "kalama",
        "bootloader": "xboot",
        "baseband": "MPSS.DE.3.0",
        "socManufacturer": "Qualcomm",
        "socModel": "SM8550",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 33,
        "buildId": "67.2.A.3.16",
        "incremental": "067002A003001600521143226",
        "securityPatch": "2025-05-01",
        "fingerprint": "Sony/XQ-DQ72/XQ-DQ72:15/67.2.A.3.16/067002A003001600521143226:user/release-keys",
        "tac": "35361874",
        "width": 1096,
        "height": 2560,
        "density": 450,
    },
    {
        "id": "moto-edge-40-pro-a15",
        "label": "Motorola Edge 40 Pro (Android 15)",
        "source": "LineageOS Community",
        "brand": "motorola",
        "manufacturer": "motorola",
        "model": "motorola edge 40 pro",
        "marketname": "motorola edge 40 pro",
        "device": "rtwo",
        "product": "rtwo_g",
        "hardware": "qcom",
        "board": "kalama",
        "platform": "kalama",
        "bootloader": "MBM-3.0-rtwo_g",
        "baseband": "M8550_HI434_03.562",
        "socManufacturer": "Qualcomm",
        "socModel": "SM8550",
        "release": "15",
        "apiLevel": 35,
        "firstApiLevel": 33,
        "buildId": "V1TRS35H.60-33-5",
        "incremental": "7195d7-c0632",
        "securityPatch": "2025-05-01",
        "fingerprint": "motorola/rtwo_g/rtwo:15/V1TRS35H.60-33-5/7195d7-c0632:user/release-keys",
        "tac": "35978122",
        "width": 1080,
        "height": 2400,
        "density": 400,
    },
]

PIXEL_CODENAME_INFO: dict[str, dict[str, Any]] = {
    "caiman": {"model": "Pixel 9 Pro", "soc": "Tensor G4", "platform": "zumapro", "tac": "35847631", "w": 1280, "h": 2856, "dpi": 480},
    "komodo": {"model": "Pixel 9 Pro XL", "soc": "Tensor G4", "platform": "zumapro", "tac": "35912468", "w": 1344, "h": 2992, "dpi": 486},
    "tokay": {"model": "Pixel 9", "soc": "Tensor G4", "platform": "zumapro", "tac": "35847510", "w": 1080, "h": 2424, "dpi": 420},
    "husky": {"model": "Pixel 8 Pro", "soc": "Tensor G3", "platform": "zuma", "tac": "35542188", "w": 1344, "h": 2992, "dpi": 489},
    "shiba": {"model": "Pixel 8", "soc": "Tensor G3", "platform": "zuma", "tac": "35498217", "w": 1080, "h": 2400, "dpi": 420},
    "akita": {"model": "Pixel 8a", "soc": "Tensor G3", "platform": "zuma", "tac": "35736420", "w": 1080, "h": 2400, "dpi": 430},
    "cheetah": {"model": "Pixel 7 Pro", "soc": "Tensor G2", "platform": "gs201", "tac": "35185089", "w": 1440, "h": 3120, "dpi": 512},
    "oriole": {"model": "Pixel 6", "soc": "Tensor", "platform": "gs101", "tac": "35461487", "w": 1080, "h": 2400, "dpi": 420},
}


def generate_luhn_imei(tac: str = "35847631") -> str:
    clean_tac = re.sub(r"\D", "", str(tac or ""))[:8].ljust(8, "0")
    body = clean_tac + "".join(str(random.randint(0, 9)) for _ in range(6))
    total = 0
    for idx, ch in enumerate(body):
        d = int(ch)
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return f"{body}{check}"


def generate_serial(brand: str = "google") -> str:
    b = (brand or "").lower()
    if "samsung" in b:
        chars = string.ascii_uppercase + string.digits
        return "R5CX" + "".join(random.choice(chars) for _ in range(7))
    if "google" in b:
        chars = string.ascii_uppercase + string.digits
        return "4" + "".join(random.choice(chars) for _ in range(13))
    return secrets.token_hex(6).upper()


def generate_android_id() -> str:
    return secrets.token_hex(8)


def parse_fingerprint_string(fp: str) -> dict[str, str]:
    """Parse brand/product/device:release/buildId/incremental:type/tags."""
    clean = str(fp or "").strip()
    m = re.fullmatch(
        r"([^/]+)/([^/]+)/([^:]+):([^/]+)/([^/]+)/([^:]+):([^/]+)/([^\s]+)",
        clean,
    )
    if not m:
        raise ValueError(
            "Invalid Android fingerprint format. Expected brand/product/device:release/id/incremental:user/release-keys."
        )
    return {
        "brand": m.group(1),
        "product": m.group(2),
        "device": m.group(3),
        "release": m.group(4),
        "buildId": m.group(5),
        "incremental": m.group(6),
        "type": m.group(7),
        "tags": m.group(8),
        "fingerprint": clean,
    }


def _load_custom_profiles() -> list[dict[str, Any]]:
    try:
        data = json.loads(CUSTOM_FINGERPRINTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def _save_custom_profiles(items: list[dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = CUSTOM_FINGERPRINTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CUSTOM_FINGERPRINTS_FILE)


def list_profiles() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in BUILTIN_PROFILES:
        by_id[item["id"]] = {**item, "custom": False}
    for item in _load_custom_profiles():
        if isinstance(item, dict) and item.get("id"):
            by_id[item["id"]] = {**item, "custom": True}
    return list(by_id.values())


def get_profile(profile_id: str) -> dict[str, Any] | None:
    for item in list_profiles():
        if item["id"] == profile_id:
            return item
    return None


def pick_random_profile(api_level: int | None = None) -> dict[str, Any]:
    all_profiles = list_profiles()
    if api_level:
        matching = [p for p in all_profiles if abs(int(p.get("apiLevel") or api_level) - int(api_level)) <= 1]
        if matching:
            return random.choice(matching)
    return random.choice(all_profiles)


def import_custom_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Import a PlayIntegrityFix pif.json, MagiskHidePropsConf entry, or structured JSON profile."""
    raw_text = payload.get("raw")
    data: dict[str, Any] = dict(payload)
    if isinstance(raw_text, str) and raw_text.strip():
        stripped = raw_text.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                data.update(parsed)
        elif "=" in stripped and ":user/" in stripped:
            # MagiskHidePropsConf format: Name:Manufacturer:Model=fingerprint__patch
            left, right = stripped.split("=", 1)
            fp_part, _, patch_part = right.partition("__")
            parts = [p.strip() for p in left.split(":")]
            data["fingerprint"] = fp_part.strip()
            if patch_part.strip():
                data["securityPatch"] = patch_part.strip()
            if len(parts) >= 3:
                data.setdefault("label", parts[0])
                data.setdefault("manufacturer", parts[1])
                data.setdefault("model", parts[2])
        else:
            data["fingerprint"] = stripped

    fp = data.get("fingerprint") or data.get("FINGERPRINT") or ""
    parsed_fp = parse_fingerprint_string(fp)

    manufacturer = str(data.get("manufacturer") or data.get("MANUFACTURER") or parsed_fp["brand"]).strip()
    brand = str(data.get("brand") or data.get("BRAND") or parsed_fp["brand"]).strip()
    model = str(data.get("model") or data.get("MODEL") or parsed_fp["device"]).strip()
    device = str(data.get("device") or data.get("DEVICE") or parsed_fp["device"]).strip()
    product = str(data.get("product") or data.get("PRODUCT") or parsed_fp["product"]).strip()
    release = str(data.get("release") or data.get("RELEASE") or parsed_fp["release"]).strip()
    build_id = str(data.get("buildId") or data.get("ID") or parsed_fp["buildId"]).strip()
    incremental = str(data.get("incremental") or data.get("INCREMENTAL") or parsed_fp["incremental"]).strip()
    security_patch = str(data.get("securityPatch") or data.get("SECURITY_PATCH") or "2025-05-05").strip()
    first_api = int(data.get("firstApiLevel") or data.get("DEVICE_INITIAL_SDK_INT") or 33)
    rel_num = int(re.sub(r"\D", "", release) or 15)
    api_map = {11: 30, 12: 31, 13: 33, 14: 34, 15: 35, 16: 36, 17: 37}
    api_level = int(data.get("apiLevel") or api_map.get(rel_num, 35))
    label = str(data.get("label") or f"{manufacturer} {model} (Android {release})").strip()[:72]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{brand}-{device}-{release}".lower()).strip("-")[:40]
    profile_id = str(data.get("id") or f"custom-{slug}-{secrets.token_hex(2)}").strip()

    entry = {
        "id": profile_id,
        "label": label,
        "source": str(data.get("source") or "Community Import"),
        "brand": brand,
        "manufacturer": manufacturer,
        "model": model,
        "marketname": str(data.get("marketname") or model),
        "device": device,
        "product": product,
        "hardware": str(data.get("hardware") or device),
        "board": str(data.get("board") or device),
        "platform": str(data.get("platform") or device),
        "bootloader": str(data.get("bootloader") or f"{device}-{incremental[:10]}"),
        "baseband": str(data.get("baseband") or f"g5400-{incremental[:8]}"),
        "socManufacturer": str(data.get("socManufacturer") or manufacturer),
        "socModel": str(data.get("socModel") or device),
        "release": release,
        "apiLevel": api_level,
        "firstApiLevel": first_api,
        "buildId": build_id,
        "incremental": incremental,
        "securityPatch": security_patch,
        "fingerprint": parsed_fp["fingerprint"],
        "tac": str(data.get("tac") or "35847631"),
        "width": int(data.get("width") or 1080),
        "height": int(data.get("height") or 2400),
        "density": int(data.get("density") or 420),
        "custom": True,
    }
    customs = [item for item in _load_custom_profiles() if item.get("id") != profile_id]
    customs.insert(0, entry)
    _save_custom_profiles(customs[:48])
    return entry


def delete_custom_profile(profile_id: str) -> bool:
    customs = _load_custom_profiles()
    updated = [item for item in customs if item.get("id") != profile_id]
    if len(updated) == len(customs):
        return False
    _save_custom_profiles(updated)
    return True


def sync_official_pixel_fingerprints() -> list[dict[str, Any]]:
    """Fetch fresh real Pixel build fingerprints directly from Google OTA metadata headers."""
    req = urllib.request.Request(
        "https://developers.google.com/android/ota",
        headers={"User-Agent": "Mozilla/5.0", "Cookie": "devsite_wall_acks=nexus-ota-tos"},
    )
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    synced: list[dict[str, Any]] = []
    for codename in ("caiman", "komodo", "tokay", "husky", "shiba", "akita"):
        urls = re.findall(rf"https://dl\.google\.com/dl/android/aosp/{codename}-ota-[^\"']+\.zip", html)
        if not urls:
            continue
        # Pick latest Android 16 (bp4a/bp3a/bp2a) or latest overall
        preferred = [u for u in urls if "-ota-bp4a." in u or "-ota-bp3a." in u]
        target_url = preferred[-1] if preferred else urls[-1]
        try:
            r = urllib.request.Request(target_url, headers={"Range": "bytes=0-16383"})
            chunk = urllib.request.urlopen(r, timeout=12).read().decode("latin1", "ignore")
            m_fp = re.search(r"post-build=([^\r\n]+)", chunk)
            m_sp = re.search(r"post-security-patch-level=([^\r\n]+)", chunk)
            if not m_fp:
                continue
            fp = m_fp.group(1).strip()
            patch = m_sp.group(1).strip() if m_sp else "2026-02-05"
            info = PIXEL_CODENAME_INFO[codename]
            parsed = parse_fingerprint_string(fp)
            entry = import_custom_profile(
                {
                    "id": f"synced-{codename}-a{parsed['release']}",
                    "label": f"Google {info['model']} (Android {parsed['release']} · Synced)",
                    "source": "Google OTA Live Sync",
                    "manufacturer": "Google",
                    "brand": "google",
                    "model": info["model"],
                    "marketname": info["model"],
                    "device": codename,
                    "product": codename,
                    "hardware": codename,
                    "board": codename,
                    "platform": info["platform"],
                    "socManufacturer": "Google",
                    "socModel": info["soc"],
                    "fingerprint": fp,
                    "securityPatch": patch,
                    "tac": info["tac"],
                    "width": info["w"],
                    "height": info["h"],
                    "density": info["dpi"],
                }
            )
            synced.append(entry)
        except Exception:
            continue
    return synced


def build_identity_record(
    profile: dict[str, Any],
    carrier_id: str = "telkomsel",
    existing: dict[str, Any] | None = None,
    randomize_ids: bool = False,
) -> dict[str, Any]:
    carrier = CARRIER_BY_ID.get(carrier_id) or CARRIERS[0]
    keep = existing if (existing and not randomize_ids) else {}
    serial = str(keep.get("serial") or generate_serial(profile.get("brand", "google")))
    imei = str(keep.get("imei") or generate_luhn_imei(profile.get("tac", "35847631")))
    android_id = str(keep.get("androidId") or generate_android_id())
    device_name = str(profile.get("marketname") or profile.get("model") or "Android Phone")
    return {
        "profileId": profile["id"],
        "label": profile.get("label") or device_name,
        "source": profile.get("source") or "Community",
        "brand": profile["brand"],
        "manufacturer": profile["manufacturer"],
        "model": profile["model"],
        "marketname": device_name,
        "device": profile["device"],
        "product": profile["product"],
        "hardware": profile.get("hardware") or profile["device"],
        "board": profile.get("board") or profile["device"],
        "platform": profile.get("platform") or profile["device"],
        "bootloader": (
            profile.get("bootloader")
            if profile.get("bootloader") and str(profile.get("bootloader")).lower() != "unknown"
            else f"{profile['device']}-1.0-{profile['incremental'][:12]}"
        ),
        "baseband": profile.get("baseband") or "g5400-250205",
        "socManufacturer": profile.get("socManufacturer") or profile["manufacturer"],
        "socModel": profile.get("socModel") or profile["device"],
        "release": str(profile.get("release") or "15"),
        "apiLevel": int(profile.get("apiLevel") or 35),
        "firstApiLevel": int(profile.get("firstApiLevel") or 33),
        "buildId": profile["buildId"],
        "incremental": profile["incremental"],
        "securityPatch": profile.get("securityPatch") or "2025-05-05",
        "fingerprint": profile["fingerprint"],
        "description": f"{profile['product']}-user {profile.get('release', '15')} {profile['buildId']} {profile['incremental']} release-keys",
        "flavor": f"{profile['product']}-user",
        "serial": serial,
        "imei": imei,
        "androidId": android_id,
        "carrierId": carrier["id"],
        "carrierName": carrier["name"],
        "carrierNumeric": carrier["numeric"],
        "carrierIso": carrier["iso"],
    }


BUSYBOX_SHIM = (
    "#!/system/bin/sh\n"
    "[ \"$1\" = \"sh\" ] && shift\n"
    "[ \"$1\" = \"-o\" ] && shift 2\n"
    "exec /system/bin/sh \"$@\"\n"
)


def build_identity_prop_pairs(identity: dict[str, Any], is_emulator: bool = True) -> list[tuple[str, str]]:
    """Return ordered (key, value) system property overrides for a real device identity."""
    brand = identity["brand"]
    mfr = identity["manufacturer"]
    model = identity["model"]
    market = identity["marketname"]
    device = identity["device"]
    product = identity["product"]
    fp = identity["fingerprint"]
    bid = identity["buildId"]
    inc = identity["incremental"]
    desc = identity["description"]
    flavor = identity["flavor"]
    patch = identity["securityPatch"]
    serial = identity["serial"]
    board = identity["board"]
    platform = identity["platform"]
    soc_mfr = identity["socManufacturer"]
    soc_model = identity["socModel"]
    bootloader = identity["bootloader"]
    baseband = identity["baseband"]
    hardware = identity["hardware"]

    props: list[tuple[str, str]] = [
        ("ro.product.brand", brand),
        ("ro.product.manufacturer", mfr),
        ("ro.product.model", model),
        ("ro.product.device", device),
        ("ro.product.name", product),
        ("ro.product.marketname", market),
        ("ro.product.vendor.marketname", market),
        ("ro.product.odm.marketname", market),
        ("ro.build.product", device),
        ("ro.build.id", bid),
        ("ro.build.display.id", bid),
        ("ro.build.version.incremental", inc),
        ("ro.build.version.security_patch", patch),
        ("ro.vendor.build.security_patch", patch),
        ("ro.build.description", desc),
        ("ro.build.flavor", flavor),
        ("ro.build.tags", "release-keys"),
        ("ro.build.type", "user"),
        ("ro.build.characteristics", "default"),
        ("ro.build.fingerprint", fp),
        ("ro.bootimage.build.fingerprint", fp),
        ("ro.product.board", board),
        ("ro.board.platform", platform),
        ("ro.soc.manufacturer", soc_mfr),
        ("ro.soc.model", soc_model),
        ("ro.bootloader", bootloader if bootloader.lower() != "unknown" else f"{device}-1.0-{inc[:12]}"),
        ("ro.baseband", baseband),
        ("ro.serialno", serial),
        ("ro.boot.serialno", serial),
        ("ro.boot.verifiedbootstate", "green"),
        ("ro.boot.flash.locked", "1"),
        ("ro.boot.vbmeta.device_state", "locked"),
        ("gsm.version.baseband", baseband),
        ("gsm.operator.alpha", identity["carrierName"]),
        ("gsm.operator.numeric", identity["carrierNumeric"]),
        ("gsm.operator.iso-country", identity["carrierIso"]),
        ("gsm.sim.operator.alpha", identity["carrierName"]),
        ("gsm.sim.operator.numeric", identity["carrierNumeric"]),
        ("gsm.sim.operator.iso-country", identity["carrierIso"]),
        ("gsm.sim.state", "READY"),
    ]

    for part in ("system", "system_ext", "vendor", "odm", "product", "system_dlkm", "vendor_dlkm"):
        props.extend(
            [
                (f"ro.product.{part}.brand", brand),
                (f"ro.product.{part}.manufacturer", mfr),
                (f"ro.product.{part}.model", model),
                (f"ro.product.{part}.device", device),
                (f"ro.product.{part}.name", product),
                (f"ro.{part}.build.fingerprint", fp),
                (f"ro.{part}.build.id", bid),
                (f"ro.{part}.build.version.incremental", inc),
                (f"ro.{part}.build.tags", "release-keys"),
                (f"ro.{part}.build.type", "user"),
            ]
        )

    if is_emulator:
        props.extend(
            [
                ("ro.debuggable", "0"),
                ("ro.secure", "1"),
                ("ro.boot.qemu", "0"),
                ("ro.hardware", hardware),
                ("ro.boot.hardware", hardware),
            ]
        )
    else:
        props.extend(
            [
                ("ro.hardware", hardware),
                ("ro.boot.hardware", hardware),
                ("ro.boot.selinux", "enforcing"),
                ("ro.boot.veritymode", "enforcing"),
                ("ro.boot.warranty_bit", "0"),
                ("vendor.boot.verifiedbootstate", "green"),
                ("vendor.boot.vbmeta.device_state", "locked"),
                ("ro.adb.secure", "1"),
                ("sys.oem_unlock_allowed", "0"),
                ("ro.radio.noril", "no"),
                ("vendor.rild.libpath", "/vendor/lib64/libreference-ril.so"),
                ("ro.telephony.default_network", "9"),
                ("persist.radio.multisim.config", "ss"),
            ]
        )
    return props


OEM_MAC_OUIS: dict[str, str] = {
    "google": "3c:28:6d",
    "samsung": "b4:ef:39",
    "xiaomi": "64:cc:2e",
    "poco": "64:cc:2e",
    "oneplus": "94:65:2d",
    "sony": "ac:9b:0a",
    "motorola": "f8:cf:c5",
}


def generate_oem_mac(brand: str, seed: str) -> str:
    """Return a deterministic real smartphone OEM MAC address (never QEMU 52:54:00)."""
    oui = OEM_MAC_OUIS.get((brand or "").strip().lower(), "3c:28:6d")
    digest = hashlib.sha256(f"{oui}:{seed}".encode("utf-8")).digest()
    return f"{oui}:{digest[0]:02x}:{digest[1]:02x}:{digest[2]:02x}"


BLISS_DELETE_PROPS: tuple[str, ...] = (
    "ro.bliss.build",
    "ro.bliss.build.status",
    "ro.bliss.build.timestamp",
    "ro.bliss.build.variant",
    "ro.bliss.changelog.version",
    "ro.bliss.codename",
    "ro.bliss.cpuname",
    "ro.bliss.device",
    "ro.bliss.fingerprint",
    "ro.bliss.serialnumber",
    "ro.bliss.static.version",
    "ro.bliss.version",
    "persist.bliss.disable_taskbar",
    "ro.product.bliss.brand",
    "ro.product.bliss.device",
    "ro.product.bliss.manufacturer",
    "ro.product.bliss.model",
    "ro.product.bliss.name",
    "ro.build.selinux",
)


def build_resetprop_lines(identity: dict[str, Any], resetprop_bin: str = "resetprop", is_emulator: bool = True) -> list[str]:
    """Generate safe, complete resetprop commands covering all Android build partitions."""
    q = shlex.quote
    rp = resetprop_bin
    props = build_identity_prop_pairs(identity, is_emulator=is_emulator)
    lines = [f"{rp} -n {q(k)} {q(v)} 2>/dev/null || true" for k, v in props]
    if is_emulator:
        lines.extend(
            [
                f"{rp} -n --delete ro.kernel.qemu 2>/dev/null || true",
                f"{rp} -n --delete ro.boot.qemu.avd_name 2>/dev/null || true",
                f"{rp} -n --delete ro.boot.qemu.skin 2>/dev/null || true",
            ]
        )
    else:
        for prop_key in BLISS_DELETE_PROPS:
            lines.append(f"{rp} -n --delete {q(prop_key)} 2>/dev/null || true")
        lines.append(f"{rp} -n service.adb.root 0 2>/dev/null || true")
    return lines


def build_custom_pif_prop(identity: dict[str, Any]) -> str:
    """Build PlayIntegrityFork custom.pif.prop matching a real hardware profile."""
    first_api = int(identity.get("firstApiLevel") or 32)
    return "\n".join(
        [
            f"MANUFACTURER={identity['manufacturer']}",
            f"MODEL={identity['model']}",
            f"FINGERPRINT={identity['fingerprint']}",
            f"BRAND={identity['brand']}",
            f"PRODUCT={identity['product']}",
            f"DEVICE={identity['device']}",
            f"RELEASE={identity.get('release', '13')}",
            f"ID={identity['buildId']}",
            f"INCREMENTAL={identity['incremental']}",
            "TYPE=user",
            "TAGS=release-keys",
            f"SECURITY_PATCH={identity.get('securityPatch', '2024-07-05')}",
            f"DEVICE_INITIAL_SDK_INT={min(first_api, 32)}",
            f"*.build.id={identity['buildId']}",
            f"*.security_patch={identity.get('securityPatch', '2024-07-05')}",
            f"*api_level={min(first_api, 32)}",
            "spoofBuild=0",
            "spoofProps=0",
            "spoofProvider=0",
            "spoofSignature=0",
            "spoofVendingFinger=0",
            "spoofVendingSdk=0",
            "verboseLogs=0",
            "",
        ]
    )


# Realistic battery for ISO VMs: a phone sitting on its AC charger (Pixel adaptive
# charging holds ~80%). Being "plugged in" matters: with stay_on_while_plugged_in=7 the
# screen/CPU stay awake. An unplugged VM sleeps 10 s after the keyguard appears and the
# guest enters s2idle autosuspend, which freezes ADB/VNC (and resume is unreliable on KVM).
# Level must be set before present=1 (a 0% present battery triggers a low-battery shutdown).
ISO_BATTERY_LINES: tuple[str, ...] = (
    "dumpsys battery set level 83 >/dev/null 2>&1 || true",
    "dumpsys battery set temp 312 >/dev/null 2>&1 || true",
    "dumpsys battery set present 1 >/dev/null 2>&1 || true",
    "dumpsys battery set usb 0 >/dev/null 2>&1 || true",
    "dumpsys battery set wireless 0 >/dev/null 2>&1 || true",
    "dumpsys battery set ac 1 >/dev/null 2>&1 || true",
    "dumpsys battery set status 2 >/dev/null 2>&1 || true",
    "settings put global stay_on_while_plugged_in 7 >/dev/null 2>&1 || true",
)


def build_ksu_iso_scripts(identity: dict[str, Any]) -> dict[str, str]:
    """Build KernelSU pre-Zygote post-fs-data and post-boot service scripts for BlissOS ISO VMs."""
    q = shlex.quote
    post_fs_lines = [
        "#!/system/bin/sh",
        "# Runs synchronously via KernelSU BEFORE Zygote so Java android.os.Build.* sees the real phone.",
        "RP=/data/adb/ksu/bin/resetprop",
        "[ -x /data/local/tmp/resetprop ] && [ ! -x \"$RP\" ] && RP=/data/local/tmp/resetprop",
        "chmod 640 /sys/fs/selinux/enforce 2>/dev/null || true",
        "chmod 440 /sys/fs/selinux/policy 2>/dev/null || true",
    ] + build_resetprop_lines(identity, resetprop_bin='"$RP"', is_emulator=False)

    service_lines = [
        "#!/system/bin/sh",
        "for _i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do",
        '  [ "$(getprop sys.boot_completed)" = "1" ] && break',
        "  sleep 2",
        "done",
        "RP=/data/adb/ksu/bin/resetprop",
        "[ -x /data/local/tmp/resetprop ] && [ ! -x \"$RP\" ] && RP=/data/local/tmp/resetprop",
        "chmod 640 /sys/fs/selinux/enforce 2>/dev/null || true",
        "chmod 440 /sys/fs/selinux/policy 2>/dev/null || true",
        f"settings put global device_name {q(identity['marketname'])} 2>/dev/null || true",
        f"settings put secure bluetooth_name {q(identity['marketname'])} 2>/dev/null || true",
        f"settings put secure bluetooth_address {q(generate_oem_mac(identity['brand'], 'bt:' + identity['serial']))} 2>/dev/null || true",
        f'"$RP" -n gsm.operator.alpha {q(identity["carrierName"])} 2>/dev/null || true',
        f'"$RP" -n gsm.operator.numeric {q(identity["carrierNumeric"])} 2>/dev/null || true',
        f'"$RP" -n gsm.operator.iso-country {q(identity["carrierIso"])} 2>/dev/null || true',
        f'"$RP" -n gsm.sim.operator.alpha {q(identity["carrierName"])} 2>/dev/null || true',
        f'"$RP" -n gsm.sim.operator.numeric {q(identity["carrierNumeric"])} 2>/dev/null || true',
        f'"$RP" -n gsm.sim.operator.iso-country {q(identity["carrierIso"])} 2>/dev/null || true',
        '"$RP" -n gsm.sim.state READY 2>/dev/null || true',
    ] + list(ISO_BATTERY_LINES)
    for prop_key in BLISS_DELETE_PROPS:
        service_lines.append(f'"$RP" -n --delete {q(prop_key)} 2>/dev/null || true')
    return {
        "post-fs-data.sh": "\n".join(post_fs_lines) + "\n",
        "service.sh": "\n".join(service_lines) + "\n",
    }


ISO_BOOT_HOOK_VERSION = "3"
VIRT_WIFI_LOWER_IFACE = "rmnet0"
ISO_WAKE_LOCK_NAME = "wlan_ctrl"

ISO_RADIO_VINTF_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest version="1.0" type="device">
    <hal format="hidl">
        <name>android.hardware.radio</name>
        <transport>hwbinder</transport>
        <fqname>@1.1::IRadio/slot1</fqname>
    </hal>
</manifest>
"""


def build_iso_modem_conf(identity: dict[str, Any]) -> str:
    """Build deterministic virtual GSM/LTE modem config for libdroidcloud_ril.so."""
    imei = re.sub(r"\D", "", str(identity.get("imei") or ""))[:15]
    if len(imei) < 15:
        imei = generate_luhn_imei("86571206")
    op_num = re.sub(r"\D", "", str(identity.get("carrierNumeric") or "51010"))[:6] or "51010"
    op_name = str(identity.get("carrierName") or "Telkomsel").strip() or "Telkomsel"
    iso = str(identity.get("carrierIso") or "id").strip().lower()
    baseband = str(identity.get("baseband") or "MPSS.DE.3.0.c1-00381").strip()
    if not baseband or baseband.lower() == "unknown":
        baseband = "MPSS.DE.3.0.c1-00381"
    seed = f"{imei}:{identity.get('serial', '')}:{op_num}"
    digits = "".join(str(b % 10) for b in hashlib.sha256(seed.encode("utf-8")).digest())
    imsi = (op_num + digits)[:15]
    cc_map = {"id": "62", "sg": "65", "my": "60", "us": "1", "gb": "44", "jp": "81", "de": "49"}
    cc = cc_map.get(iso, "62")
    iccid_base = ("89" + cc + op_num + digits)[:19]
    # Luhn check digit for 20-digit ICCID
    total = 0
    for idx, ch in enumerate(reversed(iccid_base)):
        d = int(ch)
        if idx % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    iccid = iccid_base + str((10 - (total % 10)) % 10)
    msisdn_prefix = "62812" if iso == "id" else f"{cc}812"
    msisdn = (msisdn_prefix + digits[6:14])[:13]
    bt_mac = generate_oem_mac(str(identity.get("brand") or "google"), f"bt:{identity.get('serial', imei)}")
    bt_name = str(identity.get("marketname") or identity.get("model") or "Android Phone").strip()
    return "\n".join(
        [
            f"imei={imei}",
            "imeisv=01",
            f"imsi={imsi}",
            f"iccid={iccid}",
            f"msisdn={msisdn}",
            f"operator_long={op_name}",
            f"operator_short={op_name}",
            f"operator_numeric={op_num}",
            f"baseband={baseband}",
            f"bt_mac={bt_mac}",
            f"bt_name={bt_name}",
            "",
        ]
    )


def build_iso_boot_hook() -> dict[str, str]:
    """Files appended (as an extra initramfs cpio) to the BlissOS initrd.

    BlissOS' kernel KernelSU never injects its ``ksud`` init.rc triggers, so modules,
    ReZygisk and the identity post-fs-data.d scripts only ran after boot (requiring a
    Zygote restart). The initrd sources ``/scripts/*`` right before ``switch_root``; this
    hook bind-mounts an augmented copy of the hardware init rc (the one that runs
    ``/system/etc/init.sh``) carrying the standard KernelSU triggers plus first-boot
    wakelock, ``VIRT_WIFI`` auto-connect, and the ``android.hardware.radio@1.1`` VINTF
    manifest fragment (so ``rild`` registers ``IRadio/slot1`` cleanly with
    ``hwservicemanager``). The init.rc bind mount is removed again at post-fs-data.
    """
    rc_tail = "\n".join(
        [
            "",
            "# Droid Cloud: KernelSU native boot triggers (initrd overlay v" + ISO_BOOT_HOOK_VERSION + ")",
            "on post-fs-data",
            "    umount @RC_PATH@",
            f"    write /sys/power/wake_lock {ISO_WAKE_LOCK_NAME}",
            "    exec u:r:su:s0 root root -- /system/bin/timeout 90 /data/adb/ksud post-fs-data",
            "",
            "on boot",
            f"    write /sys/power/wake_lock {ISO_WAKE_LOCK_NAME}",
            "    exec u:r:su:s0 root root -- /system/bin/ip link set wlan0 up",
            "",
            "on zygote-start",
            "    exec u:r:su:s0 root root -- /system/bin/timeout 30 /data/adb/ksud services",
            "",
            "on property:sys.boot_completed=1",
            f"    write /sys/power/wake_lock {ISO_WAKE_LOCK_NAME}",
            "    start dc_virt_wifi",
            "    exec u:r:su:s0 root root -- /system/bin/timeout 30 /data/adb/ksud boot-completed",
            "",
            'service dc_virt_wifi /system/bin/sh -c "if [ -d /sys/class/net/wlan0 ]; then ip link set wlan0 up 2>/dev/null; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do ip -4 addr show wlan0 2>/dev/null | grep -q inet && break; cmd wifi set-wifi-enabled enabled >/dev/null 2>&1; cmd wifi connect-network VirtWifi open >/dev/null 2>&1; sleep 2; done; fi"',
            "    class late_start",
            "    user root",
            "    group root",
            "    seclabel u:r:su:s0",
            "    oneshot",
            "    disabled",
            "",
        ]
    )
    script = "\n".join(
        [
            "# Droid Cloud initrd hook (sourced by the BlissOS initrd before switch_root)",
            "dc_boot_hook()",
            "{",
            "\tlocal rc f",
            "\tmkdir -p /dc_boot",
            "\tif [ -f /tmp/fakeboot.xml ] && [ -f /droidcloud/radio_manifest.xml ]; then",
            "\t\tcat /droidcloud/radio_manifest.xml > /tmp/fakeboot.xml",
            "\t\tchmod 644 /tmp/fakeboot.xml 2>/dev/null || true",
            "\tfi",
            "\tfor f in /android/init.*.rc; do",
            "\t\tgrep -q /system/etc/init.sh \"$f\" 2>/dev/null && rc=$f && break",
            "\tdone",
            "\t[ -n \"$rc\" ] || return 0",
            "\tgrep -q 'Droid Cloud: KernelSU native boot triggers' \"$rc\" && return 0",
            "\tcat \"$rc\" > /dc_boot/hw.rc || return 0",
            "\tsed \"s#@RC_PATH@#${rc#/android}#\" /droidcloud/ksu.rc >> /dc_boot/hw.rc",
            "\tchmod 644 /dc_boot/hw.rc",
            "\tmount --bind /dc_boot/hw.rc \"$rc\" && echo \" Droid Cloud boot hook: $rc\"",
            "}",
            "dc_boot_hook",
            "",
        ]
    )
    return {
        "scripts/99-droidcloud": script,
        "droidcloud/ksu.rc": rc_tail,
        "droidcloud/radio_manifest.xml": ISO_RADIO_VINTF_XML,
    }


# Standard AOSP GSM smartphone telephony feature declaration. BlissOS ships
# tablet_core_hardware.xml without android.hardware.telephony*, causing Google Play
# Store to treat the VM as a Wi-Fi tablet and hide phone-only apps (Grab, Gojek, etc.).
ISO_TELEPHONY_FEATURES_XML = """<?xml version="1.0" encoding="utf-8"?>
<permissions>
    <feature name="android.hardware.telephony" />
    <feature name="android.hardware.telephony.gsm" />
    <feature name="android.hardware.telephony.calling" />
    <feature name="android.hardware.telephony.data" />
    <feature name="android.hardware.telephony.messaging" />
    <feature name="android.hardware.telephony.radio.access" />
    <feature name="android.hardware.telephony.subscription" />
    <feature name="android.hardware.telephony.ims" />
    <feature name="android.hardware.telephony.carrierlock" />
</permissions>
"""


def build_iso_platform_scripts() -> dict[str, str]:
    """KernelSU post-fs-data/service scripts for BlissOS platform behaviour.

    * Holds a kernel wake lock so the guest never enters s2idle autosuspend: with the
      keyguard up and the (spoofed) battery unplugged, PowerManager sleeps after 10 s and
      a suspended QEMU guest freezes ADB/VNC until an input event arrives.
    * ``VIRT_WIFI=1`` on the kernel cmdline: before system_server starts, ``eth0`` is
      renamed (EthernetTracker never claims it) and a ``virt_wifi`` ``wlan0`` is stacked
      on top so ConnectivityManager reports TRANSPORT_WIFI. Guest traffic, including the
      slirp hostfwd to adbd :5555, flows via wlan0; any failure rolls back to eth0.
    """
    low = VIRT_WIFI_LOWER_IFACE
    post_fs = "\n".join(
        [
            "#!/system/bin/sh",
            "# Droid Cloud BlissOS platform setup (runs pre-system_server via ksud post-fs-data)",
            f"echo {ISO_WAKE_LOCK_NAME} > /sys/power/wake_lock 2>/dev/null",
            "# Break MS_SHARED propagation on /proc so per-app /proc/cpuinfo bind mounts never",
            "# multiply exponentially (2^n) into PID 1 and overload the guest kernel.",
            "BB=/data/adb/ksu/bin/busybox",
            "if [ -x \"$BB\" ]; then",
            "  \"$BB\" mount --make-rprivate /proc 2>/dev/null || \"$BB\" mount -o rprivate /proc 2>/dev/null || true",
            "  if [ -f /system/etc/cpuinfo.arm64.txt ] && ! grep -q ' /proc/cpuinfo ' /proc/mounts; then",
            "    \"$BB\" mount --bind /system/etc/cpuinfo.arm64.txt /proc/cpuinfo 2>/dev/null || true",
            "    \"$BB\" mount --make-private /proc/cpuinfo 2>/dev/null || \"$BB\" mount -o private /proc/cpuinfo 2>/dev/null || true",
            "  fi",
            "fi",
            "# Expose standard smartphone telephony feature XML only inside /system/etc/permissions",
            "# (avoid overlaying all of /system via KernelSU, which hides initrd /system/vendor bind mounts).",
            "if [ -f /data/adb/droidcloud_telephony.xml ] && [ ! -f /system/etc/permissions/android.hardware.telephony.gsm.xml ]; then",
            "  mkdir -p /dev/dc_perms && cp -a /system/etc/permissions/. /dev/dc_perms/ 2>/dev/null || true",
            "  cp -f /data/adb/droidcloud_telephony.xml /dev/dc_perms/android.hardware.telephony.gsm.xml",
            "  chmod 644 /dev/dc_perms/android.hardware.telephony.gsm.xml",
            "  chcon u:object_r:system_file:s0 /dev/dc_perms/android.hardware.telephony.gsm.xml 2>/dev/null || true",
            "  mount --bind /dev/dc_perms /system/etc/permissions 2>/dev/null || true",
            "fi",
            "# Hide PC/Chromebook-only vendor permission XMLs (ethernet & freeform multi-window)",
            "# via single-file bind mounts while leaving all smartphone hardware XMLs untouched.",
            "if [ ! -f /dev/dc_empty_perms.xml ]; then",
            "  printf '<?xml version=\"1.0\" encoding=\"utf-8\"?>\\n<permissions>\\n</permissions>\\n' > /dev/dc_empty_perms.xml",
            "  chmod 644 /dev/dc_empty_perms.xml",
            "  chcon u:object_r:vendor_configs_file:s0 /dev/dc_empty_perms.xml 2>/dev/null || true",
            "  for _vf in /vendor/etc/permissions/android.hardware.ethernet.xml /vendor/etc/permissions/android.software.freeform_window_management.xml; do",
            "    [ -f \"$_vf\" ] && mount --bind /dev/dc_empty_perms.xml \"$_vf\" 2>/dev/null || true",
            "  done",
            "fi",
            "# Attach Droid Cloud virtual GSM/LTE RIL to /vendor/lib64/libreference-ril.so before rild starts",
            "# so Settings -> About phone & TelephonyManager expose real IMEI, SIM, carrier & baseband.",
            "if [ -f /data/adb/droidcloud_modem.conf ] && [ -f /data/adb/ksu/lib/libdroidcloud_ril.so ]; then",
            "  cp -f /data/adb/droidcloud_modem.conf /dev/dc_modem.conf",
            "  chmod 644 /dev/dc_modem.conf",
            "  cp -f /data/adb/ksu/lib/libdroidcloud_ril.so /dev/dc_ril.so",
            "  chmod 755 /dev/dc_ril.so",
            "  chcon u:object_r:vendor_file:s0 /dev/dc_ril.so 2>/dev/null || true",
            "  mount --bind /dev/dc_ril.so /vendor/lib64/libreference-ril.so 2>/dev/null || true",
            "fi",
            "# Start virtual Bluetooth 5.2 VHCI controller (hci0) before init.sh / btlinux-1.1",
            "if [ -x /data/adb/ksu/bin/droidcloud_bt ]; then",
            "  KM=/system/lib/modules/$(uname -r)/kernel",
            "  insmod \"$KM/crypto/ecc.ko\" 2>/dev/null || true",
            "  insmod \"$KM/crypto/ecdh_generic.ko\" 2>/dev/null || true",
            "  insmod \"$KM/net/bluetooth/bluetooth.ko\" 2>/dev/null || true",
            "  insmod \"$KM/drivers/bluetooth/hci_vhci.ko\" 2>/dev/null || true",
            "  cp -f /data/adb/ksu/bin/droidcloud_bt /dev/dc_bt && chmod 755 /dev/dc_bt",
            "  pidof dc_bt >/dev/null 2>&1 || /dev/dc_bt </dev/null >/dev/null 2>&1 &",
            "fi",
            "# Persist deterministic OEM Wi-Fi factory MAC into WifiConfigStore.xml pre-system_server",
            "# so WifiManager.getFactoryMacAddresses() & Settings never report Unavailable.",
            "WMAC=$(cat /sys/class/net/wlan0/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null || true)",
            "WCFG=/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml",
            "if [ -n \"$WMAC\" ] && [ -f \"$WCFG\" ] && [ -x \"$BB\" ]; then",
            "  if grep -q 'wifi_sta_factory_mac_address' \"$WCFG\"; then",
            "    \"$BB\" sed \"s#<string name=\\\"wifi_sta_factory_mac_address\\\">[^<]*</string>#<string name=\\\"wifi_sta_factory_mac_address\\\">$WMAC</string>#\" \"$WCFG\" > /dev/dc_wcfg && cat /dev/dc_wcfg > \"$WCFG\" && rm -f /dev/dc_wcfg",
            "  elif grep -q '<map name=\"Values\">' \"$WCFG\"; then",
            "    \"$BB\" sed \"s#<map name=\\\"Values\\\">#<map name=\\\"Values\\\">\\n<string name=\\\"wifi_sta_factory_mac_address\\\">$WMAC</string>#\" \"$WCFG\" > /dev/dc_wcfg && cat /dev/dc_wcfg > \"$WCFG\" && rm -f /dev/dc_wcfg",
            "  fi",
            "fi",
            "if grep -qw 'VIRT_WIFI=1' /proc/cmdline && [ -d /sys/class/net/eth0 ] && [ ! -d /sys/class/net/wlan0 ]; then",
            "  MOD=/system/lib/modules/$(uname -r)/kernel/drivers/net/wireless/virt_wifi.ko",
            "  grep -q '^virt_wifi ' /proc/modules || insmod \"$MOD\" 2>/dev/null || modprobe virt_wifi 2>/dev/null",
            "  if grep -q '^virt_wifi ' /proc/modules && ip link set eth0 down; then",
            "    ip addr flush dev eth0 2>/dev/null",
            f"    if ip link set eth0 name {low} && ip link set {low} up && ip link add link {low} name wlan0 type virt_wifi; then",
            "      ip link set wlan0 up",
            "    else",
            "      ip link del wlan0 2>/dev/null",
            f"      ip link set {low} down 2>/dev/null; ip link set {low} name eth0 2>/dev/null",
            "      ip link set eth0 up",
            "    fi",
            "  fi",
            "fi",
            "exit 0",
            "",
        ]
    )
    service = "\n".join(
        [
            "#!/system/bin/sh",
            "# Droid Cloud: keep the virt_wifi uplink enabled + connected (it carries ADB)",
            f"echo {ISO_WAKE_LOCK_NAME} > /sys/power/wake_lock 2>/dev/null",
            "[ -d /sys/class/net/wlan0 ] || exit 0",
            "grep -qw 'VIRT_WIFI=1' /proc/cmdline || exit 0",
            "(",
            "for _i in $(seq 1 60); do [ \"$(getprop sys.boot_completed)\" = 1 ] && break; sleep 2; done",
            "ensure_wifi() {",
            "  cmd wifi status 2>/dev/null | grep -q 'Wifi is enabled' || cmd wifi set-wifi-enabled enabled >/dev/null 2>&1",
            "  cmd wifi list-networks 2>/dev/null | grep -q VirtWifi || cmd wifi connect-network VirtWifi open >/dev/null 2>&1",
            "}",
            "ensure_wifi",
            "# Ensure virtual Bluetooth on hci0 is enabled without disturbing an already-ON stack",
            "if [ -d /sys/class/bluetooth/hci0 ]; then",
            "  chmod 666 /dev/rfkill 2>/dev/null || true",
            "  if ! dumpsys bluetooth_manager 2>/dev/null | grep -q 'state: ON'; then",
            "    pm list packages -d 2>/dev/null | grep -q 'com.android.bluetooth$' && pm enable com.android.bluetooth >/dev/null 2>&1 || true",
            "    cmd bluetooth_manager enable >/dev/null 2>&1 || true",
            "  fi",
            "fi",
            "miss=0",
            "while :; do",
            "  sleep 15",
            "  if ip -4 addr show wlan0 2>/dev/null | grep -q 'inet '; then miss=0; continue; fi",
            "  miss=$((miss + 1))",
            "  ensure_wifi",
            "  [ $miss -ge 2 ] && cmd wifi connect-network VirtWifi open >/dev/null 2>&1 && miss=0",
            "done",
            ") </dev/null >/dev/null 2>&1 &",
            "exit 0",
            "",
        ]
    )
    return {"post-fs-data.sh": post_fs, "service.sh": service}


def build_magisk_module_files(identity: dict[str, Any]) -> dict[str, str]:
    """Build Magisk module files (system.prop loaded natively by magiskd pre-Zygote + scripts)."""
    q = shlex.quote
    module_prop = (
        "id=droidcloud_identity\n"
        "name=Droid Cloud Real Device Identity\n"
        "version=v2.0\n"
        "versionCode=200\n"
        "author=Droid Cloud\n"
        f"description=Systemless real Android identity ({identity['label']})\n"
    )
    # system.prop is loaded natively by magiskd before Zygote without requiring busybox
    system_prop = "\n".join(
        f"{k}={v}" for k, v in build_identity_prop_pairs(identity, is_emulator=True)
    ) + "\n"

    post_fs_lines = [
        "#!/system/bin/sh",
        "# Runs synchronously before Zygote so android.os.Build.* sees the real device identity.",
        "RP=/system/bin/resetprop",
        "[ -x /debug_ramdisk/resetprop ] && RP=/debug_ramdisk/resetprop",
        "[ -x /sbin/resetprop ] && RP=/sbin/resetprop",
    ] + build_resetprop_lines(identity, resetprop_bin='"$RP"', is_emulator=True)

    service_lines = [
        "#!/system/bin/sh",
        "for _i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do",
        '  [ "$(getprop sys.boot_completed)" = "1" ] && break',
        "  sleep 2",
        "done",
        "RP=/system/bin/resetprop",
        "[ -x /debug_ramdisk/resetprop ] && RP=/debug_ramdisk/resetprop",
        f"settings put global device_name {q(identity['marketname'])} 2>/dev/null || true",
        f"settings put secure bluetooth_name {q(identity['marketname'])} 2>/dev/null || true",
        f"settings put secure bluetooth_address {q(generate_oem_mac(identity['brand'], 'bt:' + identity['serial']))} 2>/dev/null || true",
        f"settings put secure android_id {q(identity['androidId'])} 2>/dev/null || true",
        f'"$RP" -n gsm.operator.alpha {q(identity["carrierName"])} 2>/dev/null || true',
        f'"$RP" -n gsm.operator.numeric {q(identity["carrierNumeric"])} 2>/dev/null || true',
        f'"$RP" -n gsm.operator.iso-country {q(identity["carrierIso"])} 2>/dev/null || true',
        f'"$RP" -n gsm.sim.operator.alpha {q(identity["carrierName"])} 2>/dev/null || true',
        f'"$RP" -n gsm.sim.operator.numeric {q(identity["carrierNumeric"])} 2>/dev/null || true',
        f'"$RP" -n gsm.sim.operator.iso-country {q(identity["carrierIso"])} 2>/dev/null || true',
        '"$RP" -n gsm.sim.state READY 2>/dev/null || true',
    ]

    return {
        "module.prop": module_prop,
        "system.prop": system_prop,
        "post-fs-data.sh": "\n".join(post_fs_lines) + "\n",
        "service.sh": "\n".join(service_lines) + "\n",
    }


def _int(value: Any) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except ValueError:
        return 0


def score_identity_audit(props: dict[str, str], backend: str = "avd") -> dict[str, Any]:
    """Evaluate how cleanly an Android VM presents as a real physical smartphone."""
    warnings: list[str] = []
    score = 100
    bad_tokens = ("sdk_gphone", "emu64", "ranchu", "goldfish", "generic", "android-x86", "qemu", "bliss_x86")

    def has_bad(val: str) -> bool:
        low = (val or "").lower()
        return any(tok in low for tok in bad_tokens)

    if not props.get("model") or has_bad(props.get("model", "")):
        warnings.append(f"Model looks virtualized ({props.get('model') or 'empty'})")
        score -= 22
    if not props.get("fingerprint") or has_bad(props.get("fingerprint", "")):
        warnings.append("Build fingerprint contains emulator/x86 signature")
        score -= 25
    if props.get("tags") and props.get("tags") != "release-keys":
        warnings.append(f"Build tags is '{props.get('tags')}' instead of 'release-keys'")
        score -= 10
    if has_bad(props.get("device", "")) or has_bad(props.get("brand", "")):
        warnings.append("Brand/device codename contains emulator or QEMU marker")
        score -= 15
    if has_bad(props.get("hardware", "")) or has_bad(props.get("board", "")):
        warnings.append(f"Hardware/board ({props.get('hardware')}/{props.get('board')}) reveals virtual platform")
        score -= 12
    if props.get("qemu") == "1" or props.get("bootQemu") == "1":
        warnings.append("ro.kernel.qemu / ro.boot.qemu flag is active")
        score -= 15
    if "emulator" in (props.get("characteristics") or "").lower():
        warnings.append("ro.build.characteristics is set to 'emulator'")
        score -= 8
    if (props.get("serial") or "").upper().startswith("EMULATOR"):
        warnings.append("ro.serialno uses default EMULATOR prefix")
        score -= 8
    if has_bad(props.get("deviceName", "")):
        warnings.append(f"Settings device_name is '{props.get('deviceName')}'")
        score -= 8
    for dmi_key in ("dmiBiosVendor", "dmiChassisVendor", "dmiSysVendor", "dmiProductName"):
        dmi_val = props.get(dmi_key, "")
        if dmi_val and any(tok in dmi_val.lower() for tok in ("qemu", "seabios", "bochs", "virtual", "vmware", "kvm")):
            warnings.append(f"Hardware DMI {dmi_key} exposes VM ({dmi_val})")
            score -= 8
    if props.get("cpuHypervisor") == "1":
        warnings.append("CPU hypervisor flag exposed in /proc/cpuinfo")
        score -= 8
    if (props.get("macAddress") or "").lower().startswith("52:54:00:"):
        warnings.append("Network interface uses QEMU default MAC prefix (52:54:00)")
        score -= 6
    if props.get("netTransport") == "ETHERNET":
        warnings.append("Active network transport is ETHERNET (phones use Wi-Fi/cellular; enable VIRT_WIFI)")
        score -= 5
    if props.get("emulatedStorage") == "missing":
        warnings.append("Emulated storage (/sdcard) is not mounted (MediaProvider failing)")
        score -= 10
    if _int(props.get("publicVolumes")) > 0 or _int(props.get("opticalDrives")) > 0:
        warnings.append("Optical drive / installer media is visible to Android storage")
        score -= 6
    if _int(props.get("cpuinfoX86Leak")) > 0:
        # The ARM cpuinfo over-mount is missing, so apps reading /proc/cpuinfo see the
        # x86 host CPU (GenuineIntel/AuthenticAMD) on a device that claims to be ARM.
        warnings.append("/proc/cpuinfo exposes the x86 host CPU (ARM cpuinfo over-mount missing)")
        score -= 8
    if _int(props.get("cpuinfoMounts")) > 2:
        warnings.append(
            f"/proc/cpuinfo has {props.get('cpuinfoMounts')} stacked bind mounts (shared mount propagation leak)"
        )
        score -= 8
    if props.get("telephonyFeature") == "0":
        warnings.append(
            "Device is missing android.hardware.telephony feature (Play Store treats it as a Wi-Fi tablet)"
        )
        score -= 6
    if backend == "iso" and "imei" in props and not re.fullmatch(r"\d{15}", str(props.get("imei") or "")):
        warnings.append("Telephony IMEI is missing/unknown (virtual RIL not active)")
        score -= 5
    if props.get("batteryPresent") == "false":
        warnings.append("Battery reports present=false (VM indicator)")
        score -= 5
    # Play Protect certification is a Google-side registration state, not a VM leak: an
    # uncertified device returns empty Play Integrity verdicts. Only fixable by registering
    # the GSF android_id at google.com/android/uncertified (a one-time manual step).
    if props.get("playCertified") == "0":
        gsf = props.get("gsfAndroidId") or "unknown"
        warnings.append(
            f"Device is not Play Protect certified; register GSF android_id {gsf} at google.com/android/uncertified"
        )
        score -= 5
    try:
        leaky_cnt = int(props.get("leakyPropCount") or "0")
    except ValueError:
        leaky_cnt = 0
    if leaky_cnt > 0:
        warnings.append(f"{leaky_cnt} leftover BlissOS/QEMU properties still present")
        score -= min(12, leaky_cnt * 2)

    score = max(0, min(100, score))
    return {
        "score": score,
        "status": "real" if score >= 85 and not warnings else ("partial" if score >= 55 else "emulator"),
        "warnings": warnings,
        "backend": backend,
        "props": props,
    }


def list_isos() -> list[dict[str, Any]]:
    """Combine curated community ISO catalog with any local .iso files dropped in ISO_DIR."""
    ISO_DIR.mkdir(parents=True, exist_ok=True)
    known_files: set[str] = set()
    items: list[dict[str, Any]] = []
    for entry in ISO_CATALOG:
        path = ISO_DIR / entry["filename"]
        known_files.add(entry["filename"])
        installed = path.is_file() and path.stat().st_size > 100 * 1024 * 1024
        size_mb = round(path.stat().st_size / (1024 * 1024)) if path.is_file() else 0
        items.append(
            {
                **entry,
                "installed": installed,
                "sizeMb": size_mb,
                "local": False,
            }
        )
    for iso_path in sorted(ISO_DIR.glob("*.iso")):
        if iso_path.name in known_files:
            continue
        size_mb = round(iso_path.stat().st_size / (1024 * 1024))
        slug = re.sub(r"[^a-z0-9]+", "-", iso_path.stem.lower()).strip("-")[:40] or "custom-iso"
        items.append(
            {
                "id": f"local-{slug}",
                "name": iso_path.stem,
                "filename": iso_path.name,
                "androidVersion": "Community Android ISO",
                "apiLevel": 33,
                "variant": "Local ISO Image",
                "sizeLabel": f"{size_mb} MB",
                "url": "",
                "source": "Local Storage",
                "recommendedProfile": "pixel-7-pro-a13",
                "installed": True,
                "sizeMb": size_mb,
                "local": True,
            }
        )
    return items
