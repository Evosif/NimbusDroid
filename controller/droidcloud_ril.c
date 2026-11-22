/*
 * Droid Cloud Virtual GSM/LTE RIL (libdroidcloud_ril.so)
 * Pure-bionic AOSP RIL v12 shared library for BlissOS ISO VMs.
 *
 * Loaded directly by BlissOS /vendor/bin/hw/rild (android.hardware.radio@1.1::IRadio/slot1).
 * Reads device identity from /dev/dc_modem.conf (synced from /data/adb/droidcloud_modem.conf
 * at post-fs-data) and supplies real IMEI, IMEI SV, Baseband, SIM status, ICCID, IMSI,
 * MSISDN (phone number), carrier operator, LTE registration, and signal strength to
 * Android TelephonyManager & Settings -> About phone.
 */

#include <stddef.h>
#include <stdarg.h>

typedef signed char int8_t;
typedef unsigned char uint8_t;
typedef short int16_t;
typedef unsigned short uint16_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef long int64_t;
typedef unsigned long uint64_t;

/* Bionic libc / liblog imports */
extern void *memset(void *s, int c, size_t n);
extern void *memcpy(void *dest, const void *src, size_t n);
extern size_t strlen(const char *s);
extern int strcmp(const char *a, const char *b);
extern int strncmp(const char *a, const char *b, size_t n);
extern int open(const char *path, int flags, ...);
extern long read(int fd, void *buf, size_t count);
extern int close(int fd);
extern int pthread_create(unsigned long *thread, const void *attr, void *(*fn)(void *), void *arg);
extern int pthread_detach(unsigned long thread);
extern int usleep(unsigned int usec);
extern int __android_log_print(int prio, const char *tag, const char *fmt, ...);

#define O_RDONLY 0
#define RIL_VERSION 12

/* RIL_Errno */
#define RIL_E_SUCCESS 0
#define RIL_E_GENERIC_FAILURE 2
#define RIL_E_REQUEST_NOT_SUPPORTED 6

/* RIL_RadioState (AOSP RIL >= 10) */
#define RADIO_STATE_OFF 0
#define RADIO_STATE_UNAVAILABLE 1
#define RADIO_STATE_ON 10

/* RIL Request IDs */
#define RIL_REQUEST_GET_SIM_STATUS 1
#define RIL_REQUEST_GET_CURRENT_CALLS 9
#define RIL_REQUEST_GET_IMSI 11
#define RIL_REQUEST_QUERY_CLIP 18
#define RIL_REQUEST_SIGNAL_STRENGTH 19
#define RIL_REQUEST_VOICE_REGISTRATION_STATE 20
#define RIL_REQUEST_DATA_REGISTRATION_STATE 21
#define RIL_REQUEST_OPERATOR 22
#define RIL_REQUEST_RADIO_POWER 23
#define RIL_REQUEST_SETUP_DATA_CALL 27
#define RIL_REQUEST_SIM_IO 28
#define RIL_REQUEST_GET_IMEI 38
#define RIL_REQUEST_GET_IMEISV 39
#define RIL_REQUEST_QUERY_NETWORK_SELECTION_MODE 45
#define RIL_REQUEST_SET_NETWORK_SELECTION_AUTOMATIC 46
#define RIL_REQUEST_BASEBAND_VERSION 51
#define RIL_REQUEST_DATA_CALL_LIST 57
#define RIL_REQUEST_SCREEN_STATE 61
#define RIL_REQUEST_SET_SUPP_SVC_NOTIFICATION 62
#define RIL_REQUEST_SET_BAND_MODE 66
#define RIL_REQUEST_SET_PREFERRED_NETWORK_TYPE 73
#define RIL_REQUEST_GET_PREFERRED_NETWORK_TYPE 74
#define RIL_REQUEST_GET_NEIGHBORING_CELL_IDS 79
#define RIL_REQUEST_SET_LOCATION_UPDATES 80
#define RIL_REQUEST_VOICE_RADIO_TECH 95
#define RIL_REQUEST_DEVICE_IDENTITY 98
#define RIL_REQUEST_REPORT_STK_SERVICE_IS_RUNNING 102
#define RIL_REQUEST_GET_CELL_INFO_LIST 108
#define RIL_REQUEST_SET_UNSOL_CELL_INFO_LIST_RATE 109
#define RIL_REQUEST_SET_INITIAL_ATTACH_APN 110
#define RIL_REQUEST_IMS_REGISTRATION_STATE 111
#define RIL_REQUEST_ALLOW_DATA 123
#define RIL_REQUEST_SHUTDOWN 128
#define RIL_REQUEST_GET_RADIO_CAPABILITY 130
#define RIL_REQUEST_SET_RADIO_CAPABILITY 131
#define RIL_REQUEST_GET_ACTIVITY_INFO 135
#define RIL_REQUEST_SET_UNSOLICITED_RESPONSE_FILTER 137
#define RIL_REQUEST_SET_SIM_CARD_POWER 138
#define RIL_REQUEST_SET_SIGNAL_STRENGTH_REPORTING_CRITERIA 140
#define RIL_REQUEST_SET_LINK_CAPACITY_REPORTING_CRITERIA 141
#define RIL_REQUEST_SET_CARRIER_INFO_IMSI_ENCRYPTION 141

/* RIL Unsolicited IDs */
#define RIL_UNSOL_RESPONSE_RADIO_STATE_CHANGED 1000
#define RIL_UNSOL_RESPONSE_VOICE_NETWORK_STATE_CHANGED 1002
#define RIL_UNSOL_RESPONSE_SIM_STATUS_CHANGED 1019
#define RIL_UNSOL_VOICE_RADIO_TECH_CHANGED 1035

/* AOSP RIL ABI structs (verified byte-exact against BlissOS /vendor/lib64/libril.so) */
struct timeval_compat {
    long tv_sec;
    long tv_usec;
};

typedef struct {
    void (*OnRequestComplete)(void *t, int e, void *response, size_t responselen);
    void (*OnUnsolicitedResponse)(int unsolResponse, const void *data, size_t datalen);
    void (*RequestTimedCallback)(void (*callback)(void *param), void *param, const struct timeval_compat *relativeTime);
    void (*OnRequestAck)(void *t);
} RIL_Env;

typedef struct {
    int version;
    void (*onRequest)(int request, void *data, size_t datalen, void *t);
    int (*onStateRequest)(void);
    int (*supports)(int requestCode);
    void (*onCancel)(void *t);
    const char *(*getVersion)(void);
} RIL_RadioFunctions;

typedef struct {
    int app_type;       /* 1 = APPTYPE_SIM, 2 = APPTYPE_USIM */
    int app_state;      /* 5 = APPSTATE_READY */
    int perso_substate; /* 2 = PERSOSUBSTATE_READY */
    char *aid_ptr;
    char *app_label_ptr;
    int pin1_replaced;
    int pin1;           /* 3 = PINSTATE_DISABLED */
    int pin2;           /* 3 = PINSTATE_DISABLED */
} RIL_AppStatus;

typedef struct {
    int card_state;                      /* 1 = CARDSTATE_PRESENT */
    int universal_pin_state;             /* 3 = PINSTATE_DISABLED */
    int gsm_umts_subscription_app_index; /* 0 */
    int cdma_subscription_app_index;     /* -1 */
    int ims_subscription_app_index;      /* -1 */
    int num_applications;                /* 1 */
    RIL_AppStatus applications[8];
} RIL_CardStatus_v6;

typedef struct {
    int gw_signalStrength;
    int gw_bitErrorRate;
    int cdma_dbm;
    int cdma_ecio;
    int evdo_dbm;
    int evdo_ecio;
    int evdo_snr;
    int lte_signalStrength;
    int lte_rsrp;
    int lte_rsrq;
    int lte_rssnr;
    int lte_cqi;
    int lte_timingAdvance;
    int tdscdma_rscp;
} RIL_SignalStrength_v10;

typedef struct {
    int command;
    int fileid;
    char *path;
    int p1;
    int p2;
    int p3;
    char *data;
    char *pin2;
    char *aidPtr;
} RIL_SIM_IO_v6;

typedef struct {
    int sw1;
    int sw2;
    char *simResponse;
} RIL_SIM_IO_Response;

typedef struct {
    int version;
    int session;
    int phase;
    int rat;
    char logicalModemUuid[64];
    int status;
} RIL_RadioCapability;

_Static_assert(sizeof(RIL_CardStatus_v6) == 0x198, "RIL_CardStatus_v6 must be 408 bytes");
_Static_assert(sizeof(RIL_SignalStrength_v10) == 0x38, "RIL_SignalStrength_v10 must be 56 bytes");
_Static_assert(sizeof(RIL_SIM_IO_Response) == 0x10, "RIL_SIM_IO_Response must be 16 bytes");

/* Runtime modem configuration loaded from /dev/dc_modem.conf */
static const RIL_Env *g_env = 0;
static int g_radio_state = RADIO_STATE_ON;
static int g_pref_net = 9; /* LTE/GSM/WCDMA */

static char g_imei[32] = "351850890127593";
static char g_imeisv[8] = "01";
static char g_imsi[32] = "510101234567890";
static char g_iccid[32] = "89621000012345678901";
static char g_msisdn[32] = "6281212345678";
static char g_op_long[64] = "Telkomsel";
static char g_op_short[64] = "Telkomsel";
static char g_op_numeric[16] = "51010";
static char g_baseband[96] = "MPSS.DE.3.0.c1-00381";

static void copy_str(char *dst, size_t max, const char *src) {
    size_t i = 0;
    if (!src || !max) return;
    while (src[i] && i + 1 < max) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

static void load_config(void) {
    int fd = open("/dev/dc_modem.conf", O_RDONLY);
    if (fd < 0) fd = open("/data/vendor/radio/droidcloud_modem.conf", O_RDONLY);
    if (fd < 0) return;

    char buf[2048];
    long n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return;
    buf[n] = '\0';

    char *p = buf;
    while (*p) {
        char *line = p;
        while (*p && *p != '\n' && *p != '\r') p++;
        if (*p) *p++ = '\0';
        while (*p == '\n' || *p == '\r') p++;
        if (!*line || *line == '#') continue;

        char *eq = line;
        while (*eq && *eq != '=') eq++;
        if (*eq != '=') continue;
        *eq = '\0';
        const char *val = eq + 1;
        if (!*val) continue;

        if (strcmp(line, "imei") == 0) copy_str(g_imei, sizeof(g_imei), val);
        else if (strcmp(line, "imeisv") == 0) copy_str(g_imeisv, sizeof(g_imeisv), val);
        else if (strcmp(line, "imsi") == 0) copy_str(g_imsi, sizeof(g_imsi), val);
        else if (strcmp(line, "iccid") == 0) copy_str(g_iccid, sizeof(g_iccid), val);
        else if (strcmp(line, "msisdn") == 0) copy_str(g_msisdn, sizeof(g_msisdn), val);
        else if (strcmp(line, "operator_long") == 0) copy_str(g_op_long, sizeof(g_op_long), val);
        else if (strcmp(line, "operator_short") == 0) copy_str(g_op_short, sizeof(g_op_short), val);
        else if (strcmp(line, "operator_numeric") == 0) copy_str(g_op_numeric, sizeof(g_op_numeric), val);
        else if (strcmp(line, "baseband") == 0) copy_str(g_baseband, sizeof(g_baseband), val);
    }
    __android_log_print(4, "DroidCloudRIL", "Loaded modem config: imei=%s op=%s (%s) bb=%s",
                        g_imei, g_op_long, g_op_numeric, g_baseband);
}

static void hex_byte(char *out, uint8_t b) {
    static const char hex[] = "0123456789ABCDEF";
    out[0] = hex[(b >> 4) & 0x0F];
    out[1] = hex[b & 0x0F];
}

static void bytes_to_hex(char *dst, const uint8_t *src, size_t len) {
    for (size_t i = 0; i < len; i++) {
        hex_byte(dst + i * 2, src[i]);
    }
    dst[len * 2] = '\0';
}

/* Encode decimal digits into swapped-nibble GSM BCD, padded with 0xF */
static void encode_swapped_bcd(uint8_t *dst, size_t dst_len, const char *digits) {
    memset(dst, 0xFF, dst_len);
    size_t d = 0;
    for (size_t i = 0; i < dst_len; i++) {
        uint8_t lo = 0x0F, hi = 0x0F;
        while (digits[d] && (digits[d] < '0' || digits[d] > '9')) d++;
        if (digits[d]) { lo = (uint8_t)(digits[d++] - '0'); }
        while (digits[d] && (digits[d] < '0' || digits[d] > '9')) d++;
        if (digits[d]) { hi = (uint8_t)(digits[d++] - '0'); }
        dst[i] = (uint8_t)((hi << 4) | lo);
    }
}

static void handle_sim_io(const RIL_SIM_IO_v6 *io, void *t) {
    RIL_SIM_IO_Response resp;
    char hexbuf[128];
    uint8_t raw[64];

    if (!io) {
        g_env->OnRequestComplete(t, RIL_E_GENERIC_FAILURE, 0, 0);
        return;
    }

    int cmd = io->command;
    int fid = io->fileid;

    if (cmd == 0xC0) {
        /* GSM 11.11 15-byte GET_RESPONSE */
        memset(raw, 0, 15);
        raw[4] = (uint8_t)((fid >> 8) & 0xFF);
        raw[5] = (uint8_t)(fid & 0xFF);
        raw[6] = 0x04; /* EF */
        raw[12] = 0x02;
        if (fid == 0x2FE2) { raw[3] = 10; }
        else if (fid == 0x6F07) { raw[3] = 9; }
        else if (fid == 0x6FAD) { raw[3] = 4; }
        else if (fid == 0x6F46) { raw[3] = 17; }
        else if (fid == 0x6F38) { raw[3] = 8; }
        else if (fid == 0x6F40) { raw[3] = 28; raw[13] = 0x01; raw[14] = 28; }
        else {
            resp.sw1 = 0x6A; resp.sw2 = 0x82; resp.simResponse = "";
            g_env->OnRequestComplete(t, RIL_E_SUCCESS, &resp, sizeof(resp));
            return;
        }
        bytes_to_hex(hexbuf, raw, 15);
        resp.sw1 = 0x90; resp.sw2 = 0x00; resp.simResponse = hexbuf;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &resp, sizeof(resp));
        return;
    }

    if (cmd == 0xB0) {
        /* READ_BINARY */
        if (fid == 0x2FE2) {
            encode_swapped_bcd(raw, 10, g_iccid);
            bytes_to_hex(hexbuf, raw, 10);
        } else if (fid == 0x6F07) {
            /* EF_IMSI: length byte (8) + parity/odd nibble + swapped BCD */
            memset(raw, 0xFF, 9);
            raw[0] = 0x08;
            size_t imsi_len = strlen(g_imsi);
            uint8_t d0 = imsi_len > 0 ? (uint8_t)(g_imsi[0] - '0') : 5;
            raw[1] = (uint8_t)((d0 << 4) | 0x09); /* odd parity + type IMSI */
            encode_swapped_bcd(raw + 2, 7, imsi_len > 1 ? g_imsi + 1 : "");
            bytes_to_hex(hexbuf, raw, 9);
        } else if (fid == 0x6FAD) {
            raw[0] = 0x00; raw[1] = 0x00; raw[2] = 0x00;
            raw[3] = (strlen(g_op_numeric) >= 6) ? 0x03 : 0x02;
            bytes_to_hex(hexbuf, raw, 4);
        } else if (fid == 0x6F46) {
            memset(raw, 0xFF, 17);
            raw[0] = 0x01; /* display condition */
            size_t n = strlen(g_op_long);
            if (n > 16) n = 16;
            memcpy(raw + 1, g_op_long, n);
            bytes_to_hex(hexbuf, raw, 17);
        } else if (fid == 0x6F38) {
            memset(raw, 0xFF, 8);
            bytes_to_hex(hexbuf, raw, 8);
        } else {
            resp.sw1 = 0x6A; resp.sw2 = 0x82; resp.simResponse = "";
            g_env->OnRequestComplete(t, RIL_E_SUCCESS, &resp, sizeof(resp));
            return;
        }
        resp.sw1 = 0x90; resp.sw2 = 0x00; resp.simResponse = hexbuf;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &resp, sizeof(resp));
        return;
    }

    if (cmd == 0xB2 && fid == 0x6F40) {
        /* EF_MSISDN (28-byte record: 14 alpha + 1 len + 1 ton/npi + 10 bcd + 2 ext) */
        memset(raw, 0xFF, 28);
        size_t digits = 0;
        for (size_t i = 0; g_msisdn[i]; i++) {
            if (g_msisdn[i] >= '0' && g_msisdn[i] <= '9') digits++;
        }
        size_t bcd_bytes = (digits + 1) / 2;
        if (bcd_bytes > 10) bcd_bytes = 10;
        raw[14] = (uint8_t)(1 + bcd_bytes);
        raw[15] = 0x91; /* international TON/NPI */
        encode_swapped_bcd(raw + 16, 10, g_msisdn);
        bytes_to_hex(hexbuf, raw, 28);
        resp.sw1 = 0x90; resp.sw2 = 0x00; resp.simResponse = hexbuf;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &resp, sizeof(resp));
        return;
    }

    resp.sw1 = 0x6A; resp.sw2 = 0x82; resp.simResponse = "";
    g_env->OnRequestComplete(t, RIL_E_SUCCESS, &resp, sizeof(resp));
}

static void on_request(int request, void *data, size_t datalen, void *t) {
    (void)datalen;
    switch (request) {
    case RIL_REQUEST_GET_SIM_STATUS: {
        RIL_CardStatus_v6 cs;
        memset(&cs, 0, sizeof(cs));
        cs.card_state = 1; /* CARDSTATE_PRESENT */
        cs.universal_pin_state = 3; /* PINSTATE_DISABLED */
        cs.gsm_umts_subscription_app_index = 0;
        cs.cdma_subscription_app_index = -1;
        cs.ims_subscription_app_index = -1;
        cs.num_applications = 1;
        cs.applications[0].app_type = 1; /* APPTYPE_SIM */
        cs.applications[0].app_state = 5; /* APPSTATE_READY */
        cs.applications[0].perso_substate = 2; /* PERSOSUBSTATE_READY */
        cs.applications[0].aid_ptr = "";
        cs.applications[0].app_label_ptr = g_op_short;
        cs.applications[0].pin1_replaced = 0;
        cs.applications[0].pin1 = 3;
        cs.applications[0].pin2 = 3;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &cs, sizeof(cs));
        break;
    }
    case RIL_REQUEST_GET_IMSI:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, g_imsi, strlen(g_imsi) + 1);
        break;
    case RIL_REQUEST_GET_IMEI:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, g_imei, strlen(g_imei) + 1);
        break;
    case RIL_REQUEST_GET_IMEISV:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, g_imeisv, strlen(g_imeisv) + 1);
        break;
    case RIL_REQUEST_DEVICE_IDENTITY: {
        char *resp[4];
        resp[0] = g_imei;
        resp[1] = g_imeisv;
        resp[2] = "";
        resp[3] = "";
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, resp, sizeof(resp));
        break;
    }
    case RIL_REQUEST_BASEBAND_VERSION:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, g_baseband, strlen(g_baseband) + 1);
        break;
    case RIL_REQUEST_OPERATOR: {
        char *resp[3];
        resp[0] = g_op_long;
        resp[1] = g_op_short;
        resp[2] = g_op_numeric;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, resp, sizeof(resp));
        break;
    }
    case RIL_REQUEST_VOICE_REGISTRATION_STATE: {
        char *resp[15];
        memset(resp, 0, sizeof(resp));
        resp[0] = "1";        /* Registered, home */
        resp[1] = "1a2b";     /* LAC/TAC */
        resp[2] = "00123456"; /* CID */
        resp[3] = "14";       /* RADIO_TECH_LTE */
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, resp, sizeof(resp));
        break;
    }
    case RIL_REQUEST_DATA_REGISTRATION_STATE: {
        char *resp[6];
        memset(resp, 0, sizeof(resp));
        resp[0] = "1";        /* Registered, home */
        resp[1] = "1a2b";     /* TAC */
        resp[2] = "00123456"; /* CID */
        resp[3] = "14";       /* RADIO_TECH_LTE */
        resp[4] = "0";
        resp[5] = "1";        /* max data calls */
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, resp, sizeof(resp));
        break;
    }
    case RIL_REQUEST_SIGNAL_STRENGTH: {
        RIL_SignalStrength_v10 sig;
        sig.gw_signalStrength = 24;
        sig.gw_bitErrorRate = 99;
        sig.cdma_dbm = -1;
        sig.cdma_ecio = -1;
        sig.evdo_dbm = -1;
        sig.evdo_ecio = -1;
        sig.evdo_snr = -1;
        sig.lte_signalStrength = 24;
        sig.lte_rsrp = 85;  /* -85 dBm (stored positive in RIL v10) */
        sig.lte_rsrq = 8;   /* -8 dB */
        sig.lte_rssnr = 150; /* 15.0 dB */
        sig.lte_cqi = 12;
        sig.lte_timingAdvance = 0x7fffffff;
        sig.tdscdma_rscp = 0x7fffffff;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &sig, sizeof(sig));
        break;
    }
    case RIL_REQUEST_SIM_IO:
        handle_sim_io((const RIL_SIM_IO_v6 *)data, t);
        break;
    case RIL_REQUEST_RADIO_POWER: {
        int on = (data && (*(int *)data) != 0) ? 1 : 0;
        g_radio_state = on ? RADIO_STATE_ON : RADIO_STATE_OFF;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, 0, 0);
        g_env->OnUnsolicitedResponse(RIL_UNSOL_RESPONSE_RADIO_STATE_CHANGED, 0, 0);
        if (on) {
            g_env->OnUnsolicitedResponse(RIL_UNSOL_RESPONSE_SIM_STATUS_CHANGED, 0, 0);
            g_env->OnUnsolicitedResponse(RIL_UNSOL_RESPONSE_VOICE_NETWORK_STATE_CHANGED, 0, 0);
        }
        break;
    }
    case RIL_REQUEST_QUERY_NETWORK_SELECTION_MODE: {
        int mode = 0; /* automatic */
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &mode, sizeof(mode));
        break;
    }
    case RIL_REQUEST_GET_PREFERRED_NETWORK_TYPE:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &g_pref_net, sizeof(g_pref_net));
        break;
    case RIL_REQUEST_SET_PREFERRED_NETWORK_TYPE:
        if (data) g_pref_net = *(int *)data;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, 0, 0);
        break;
    case RIL_REQUEST_VOICE_RADIO_TECH: {
        int tech = 14; /* RADIO_TECH_LTE */
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &tech, sizeof(tech));
        break;
    }
    case RIL_REQUEST_IMS_REGISTRATION_STATE: {
        int resp[2] = {0, 1};
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, resp, sizeof(resp));
        break;
    }
    case RIL_REQUEST_GET_RADIO_CAPABILITY: {
        RIL_RadioCapability cap;
        memset(&cap, 0, sizeof(cap));
        cap.version = 1;
        cap.session = 0;
        cap.phase = 0; /* RC_PHASE_CONFIGURED */
        cap.rat = 0x4000 | 0x0008 | 0x0004; /* LTE | UMTS | GSM */
        copy_str(cap.logicalModemUuid, sizeof(cap.logicalModemUuid), "modem0");
        cap.status = 1; /* RC_STATUS_SUCCESS */
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &cap, sizeof(cap));
        break;
    }
    case RIL_REQUEST_SET_RADIO_CAPABILITY:
        if (data && datalen == sizeof(RIL_RadioCapability)) {
            RIL_RadioCapability cap;
            memcpy(&cap, data, sizeof(cap));
            cap.status = 1;
            g_env->OnRequestComplete(t, RIL_E_SUCCESS, &cap, sizeof(cap));
        } else {
            g_env->OnRequestComplete(t, RIL_E_SUCCESS, 0, 0);
        }
        break;
    case RIL_REQUEST_DATA_CALL_LIST:
        /* Keep cellular data inactive so Wi-Fi/ADB remains primary route */
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, 0, 0);
        break;
    case RIL_REQUEST_SETUP_DATA_CALL:
        g_env->OnRequestComplete(t, RIL_E_GENERIC_FAILURE, 0, 0);
        break;
    case RIL_REQUEST_QUERY_CLIP: {
        int clip = 1;
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, &clip, sizeof(clip));
        break;
    }
    case RIL_REQUEST_SET_NETWORK_SELECTION_AUTOMATIC:
    case RIL_REQUEST_SCREEN_STATE:
    case RIL_REQUEST_SET_SUPP_SVC_NOTIFICATION:
    case RIL_REQUEST_SET_BAND_MODE:
    case RIL_REQUEST_SET_LOCATION_UPDATES:
    case RIL_REQUEST_REPORT_STK_SERVICE_IS_RUNNING:
    case RIL_REQUEST_SET_UNSOL_CELL_INFO_LIST_RATE:
    case RIL_REQUEST_SET_INITIAL_ATTACH_APN:
    case RIL_REQUEST_ALLOW_DATA:
    case RIL_REQUEST_SHUTDOWN:
    case RIL_REQUEST_SET_UNSOLICITED_RESPONSE_FILTER:
    case RIL_REQUEST_SET_SIM_CARD_POWER:
    case RIL_REQUEST_SET_SIGNAL_STRENGTH_REPORTING_CRITERIA:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, 0, 0);
        break;
    case RIL_REQUEST_GET_CURRENT_CALLS:
    case RIL_REQUEST_GET_NEIGHBORING_CELL_IDS:
    case RIL_REQUEST_GET_CELL_INFO_LIST:
        g_env->OnRequestComplete(t, RIL_E_SUCCESS, 0, 0);
        break;
    default:
        g_env->OnRequestComplete(t, RIL_E_REQUEST_NOT_SUPPORTED, 0, 0);
        break;
    }
}

static int on_state_request(void) {
    return g_radio_state;
}

static int supports_request(int requestCode) {
    (void)requestCode;
    return 1;
}

static void on_cancel(void *t) {
    (void)t;
}

static const char *get_version(void) {
    return "droidcloud-ril 1.0";
}

static const RIL_RadioFunctions g_callbacks = {
    .version = RIL_VERSION,
    .onRequest = on_request,
    .onStateRequest = on_state_request,
    .supports = supports_request,
    .onCancel = on_cancel,
    .getVersion = get_version,
};

static void *notify_thread(void *arg) {
    (void)arg;
    usleep(1200000);
    if (g_env && g_env->OnUnsolicitedResponse) {
        g_env->OnUnsolicitedResponse(RIL_UNSOL_RESPONSE_RADIO_STATE_CHANGED, 0, 0);
        g_env->OnUnsolicitedResponse(RIL_UNSOL_RESPONSE_SIM_STATUS_CHANGED, 0, 0);
        g_env->OnUnsolicitedResponse(RIL_UNSOL_RESPONSE_VOICE_NETWORK_STATE_CHANGED, 0, 0);
        int tech = 14;
        g_env->OnUnsolicitedResponse(RIL_UNSOL_VOICE_RADIO_TECH_CHANGED, &tech, sizeof(tech));
    }
    return 0;
}

__attribute__((visibility("default")))
const RIL_RadioFunctions *RIL_Init(const RIL_Env *env, int argc, char **argv) {
    (void)argc;
    (void)argv;
    g_env = env;
    load_config();
    unsigned long tid = 0;
    if (pthread_create(&tid, 0, notify_thread, 0) == 0) {
        pthread_detach(tid);
    }
    return &g_callbacks;
}
