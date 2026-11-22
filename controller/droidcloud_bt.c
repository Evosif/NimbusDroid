/*
 * Droid Cloud Virtual Bluetooth 5.2 HCI Controller (droidcloud_bt)
 * Pure-bionic user-space VHCI responder for BlissOS ISO VMs.
 *
 * Opens /dev/vhci (provided by kernel hci_vhci.ko), creates Linux hci0 with a
 * deterministic OEM Bluetooth MAC from /dev/dc_modem.conf (bt_mac=...), and
 * handles standard BR/EDR + BLE HCI init/control commands so Android's
 * android.hardware.bluetooth@1.1-service.btlinux brings up Bluetooth cleanly
 * and exposes a real Bluetooth address to BluetoothAdapter & Settings.
 */

#include <stddef.h>

typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef int int32_t;

extern void *memset(void *s, int c, size_t n);
extern void *memcpy(void *dest, const void *src, size_t n);
extern size_t strlen(const char *s);
extern int strcmp(const char *a, const char *b);
extern int open(const char *path, int flags, ...);
extern long read(int fd, void *buf, size_t count);
extern long write(int fd, const void *buf, size_t count);
extern int close(int fd);
extern unsigned int sleep(unsigned int seconds);
extern int usleep(unsigned int usec);
extern void _exit(int status) __attribute__((noreturn));
extern int __android_log_print(int prio, const char *tag, const char *fmt, ...);

#define O_RDONLY 0
#define O_RDWR   2

#define HCI_COMMAND_PKT 0x01
#define HCI_EVENT_PKT   0x04
#define HCI_VENDOR_PKT  0xFF
#define HCI_PRIMARY     0x00

static uint8_t g_bdaddr[6] = {0xE2, 0x63, 0x0E, 0x2E, 0xCC, 0x64}; /* LSB first */
static char g_name[64] = "Android Phone";

static int hex_val(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void parse_mac(const char *s) {
    uint8_t b[6];
    int idx = 0;
    while (*s && idx < 6) {
        while (*s == ':' || *s == '-' || *s == ' ') s++;
        int hi = hex_val(s[0]);
        int lo = s[0] ? hex_val(s[1]) : -1;
        if (hi < 0 || lo < 0) return;
        b[idx++] = (uint8_t)((hi << 4) | lo);
        s += 2;
    }
    if (idx == 6) {
        /* HCI BD_ADDR is transmitted LSB-first */
        for (int i = 0; i < 6; i++) g_bdaddr[i] = b[5 - i];
    }
}

static void load_config(void) {
    int fd = open("/dev/dc_modem.conf", O_RDONLY);
    if (fd < 0) fd = open("/data/adb/droidcloud_modem.conf", O_RDONLY);
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
        char *eq = line;
        while (*eq && *eq != '=') eq++;
        if (*eq != '=') continue;
        *eq = '\0';
        const char *val = eq + 1;
        if (strcmp(line, "bt_mac") == 0) parse_mac(val);
        else if (strcmp(line, "bt_name") == 0 && *val) {
            size_t i = 0;
            while (val[i] && i + 1 < sizeof(g_name)) { g_name[i] = val[i]; i++; }
            g_name[i] = '\0';
        }
    }
}

static void send_cmd_complete(int fd, uint16_t opcode, uint8_t status,
                              const uint8_t *params, size_t plen) {
    uint8_t pkt[270];
    if (plen > 250) plen = 250;
    pkt[0] = HCI_EVENT_PKT;
    pkt[1] = 0x0E; /* Command Complete */
    pkt[2] = (uint8_t)(4 + plen);
    pkt[3] = 0x01; /* Num HCI Command Packets */
    pkt[4] = (uint8_t)(opcode & 0xFF);
    pkt[5] = (uint8_t)((opcode >> 8) & 0xFF);
    pkt[6] = status;
    if (plen && params) memcpy(pkt + 7, params, plen);
    (void)write(fd, pkt, 7 + plen);
}

static void handle_command(int fd, uint16_t opcode, const uint8_t *data, size_t len) {
    uint8_t p[252];
    memset(p, 0, sizeof(p));

    switch (opcode) {
    case 0x0C03: /* HCI_Reset */
    case 0x0C01: /* Set_Event_Mask */
    case 0x0C63: /* Set_Event_Mask_Page_2 */
    case 0x0C05: /* Set_Event_Filter */
    case 0x0C13: /* Write_Local_Name */
    case 0x0C16: /* Write_Connection_Accept_Timeout */
    case 0x0C18: /* Write_Page_Timeout */
    case 0x0C1A: /* Write_Scan_Enable */
    case 0x0C1C: /* Write_Page_Scan_Activity */
    case 0x0C1E: /* Write_Inquiry_Scan_Activity */
    case 0x0C20: /* Write_Authentication_Enable */
    case 0x0C24: /* Write_Class_of_Device */
    case 0x0C26: /* Write_Voice_Setting */
    case 0x0C33: /* Host_Buffer_Size */
    case 0x0C3A: /* Write_Current_IAC_LAP */
    case 0x0C43: /* Write_Inquiry_Scan_Type */
    case 0x0C45: /* Write_Inquiry_Mode */
    case 0x0C47: /* Write_Page_Scan_Type */
    case 0x0C52: /* Write_Extended_Inquiry_Response */
    case 0x0C56: /* Write_Simple_Pairing_Mode */
    case 0x0C59: /* Write_Default_Erroneous_Data_Reporting */
    case 0x0C6D: /* Write_LE_Host_Supported */
    case 0x0C7A: /* Write_Secure_Connections_Host_Support */
    case 0x0C7C: /* Read/Write_Authenticated_Payload_Timeout */
    case 0x0C7D: /* Set_Ecosystem_Base_Interval */
    case 0x0C7F: /* Set_Min_Encryption_Key_Size */
    case 0x080F: /* Write_Default_Link_Policy_Settings */
    case 0x2001: /* LE_Set_Event_Mask */
    case 0x2005: /* LE_Set_Random_Address */
    case 0x2006: /* LE_Set_Advertising_Parameters */
    case 0x2008: /* LE_Set_Advertising_Data */
    case 0x2009: /* LE_Set_Scan_Response_Data */
    case 0x200A: /* LE_Set_Advertising_Enable */
    case 0x200B: /* LE_Set_Scan_Parameters */
    case 0x200C: /* LE_Set_Scan_Enable */
    case 0x2010: /* LE_Clear_Filter_Accept_List */
    case 0x2024: /* LE_Write_Suggested_Default_Data_Length */
    case 0x2029: /* LE_Clear_Resolving_List */
    case 0x202D: /* LE_Set_Address_Resolution_Enable */
    case 0x202E: /* LE_Set_Resolvable_Private_Address_Timeout */
    case 0x2031: /* LE_Set_Default_PHY */
    case 0x2035: /* LE_Set_Advertising_Set_Random_Address */
    case 0x2036: /* LE_Set_Extended_Advertising_Parameters */
    case 0x2037: /* LE_Set_Extended_Advertising_Data */
    case 0x2038: /* LE_Set_Extended_Scan_Response_Data */
    case 0x2039: /* LE_Set_Extended_Advertising_Enable */
    case 0x203D: /* LE_Clear_Advertising_Sets */
    case 0x2041: /* LE_Set_Extended_Scan_Parameters */
    case 0x2042: /* LE_Set_Extended_Scan_Enable */
    case 0x204E: /* LE_Set_Privacy_Mode */
        send_cmd_complete(fd, opcode, 0x00, 0, 0);
        break;

    case 0x1001: /* Read_Local_Version_Information */
        p[0] = 0x0B; /* HCI Version: Bluetooth 5.2 */
        p[1] = 0x00; p[2] = 0x01; /* HCI Revision */
        p[3] = 0x0B; /* LMP Version: Bluetooth 5.2 */
        p[4] = 0x1D; p[5] = 0x00; /* Manufacturer: Qualcomm (0x001D) */
        p[6] = 0x00; p[7] = 0x01; /* LMP Subversion */
        send_cmd_complete(fd, opcode, 0x00, p, 8);
        break;

    case 0x1002: /* Read_Local_Supported_Commands (64 bytes) */
        memset(p, 0, 64);
        /* Advertise standard BR/EDR + LE commands (no vendor/ISO extensions) */
        p[0]  = 0x20;
        p[2]  = 0x80;
        p[5]  = 0xC0;
        p[6]  = 0xFF;
        p[7]  = 0xFF;
        p[8]  = 0xFF;
        p[9]  = 0xFF;
        p[10] = 0xFF;
        p[11] = 0xFF;
        p[12] = 0xFF;
        p[14] = 0xF8;
        p[15] = 0xFF;
        p[16] = 0xFF;
        p[17] = 0xFF;
        p[18] = 0xFF;
        p[22] = 0x04;
        p[24] = 0x60;
        p[25] = 0xF7;
        p[26] = 0xFF;
        p[27] = 0xFF;
        p[28] = 0x7F;
        p[32] = 0x30;
        p[33] = 0xFF;
        p[34] = 0xFF;
        p[35] = 0xFF;
        p[36] = 0x07;
        send_cmd_complete(fd, opcode, 0x00, p, 64);
        break;

    case 0x1003: /* Read_Local_Supported_Features (8 bytes) */
        p[0] = 0xFF; p[1] = 0xFE; p[2] = 0x8F; p[3] = 0xFE;
        p[4] = 0xDB; p[5] = 0xFF; p[6] = 0x7B; p[7] = 0x87;
        send_cmd_complete(fd, opcode, 0x00, p, 8);
        break;

    case 0x1004: { /* Read_Local_Extended_Features */
        uint8_t page = (len >= 1) ? data[0] : 0;
        p[0] = page;
        p[1] = 2; /* max page number */
        if (page == 0) {
            p[2] = 0xFF; p[3] = 0xFE; p[4] = 0x8F; p[5] = 0xFE;
            p[6] = 0xDB; p[7] = 0xFF; p[8] = 0x7B; p[9] = 0x87;
        } else if (page == 1) {
            p[2] = 0x0B; /* SSP host, LE host, SC host */
        } else if (page == 2) {
            p[2] = 0x01; /* SC controller */
        }
        send_cmd_complete(fd, opcode, 0x00, p, 10);
        break;
    }

    case 0x1005: /* Read_Buffer_Size */
        p[0] = 0xFD; p[1] = 0x03; /* ACL MTU: 1021 */
        p[2] = 0xFF;              /* SCO MTU: 255 */
        p[3] = 0x08; p[4] = 0x00; /* Total ACL packets: 8 */
        p[5] = 0x08; p[6] = 0x00; /* Total SCO packets: 8 */
        send_cmd_complete(fd, opcode, 0x00, p, 7);
        break;

    case 0x1009: /* Read_BD_ADDR */
        memcpy(p, g_bdaddr, 6);
        send_cmd_complete(fd, opcode, 0x00, p, 6);
        break;

    case 0x100B: /* Read_Local_Supported_Codecs */
        p[0] = 2; p[1] = 0x02; p[2] = 0x05; /* CVSD, mSBC */
        p[3] = 0; /* vendor codecs */
        send_cmd_complete(fd, opcode, 0x00, p, 4);
        break;

    case 0x0C0D: /* Read_Stored_Link_Key: max_keys(2), num_keys(2) */
        send_cmd_complete(fd, opcode, 0x00, p, 4);
        break;

    case 0x0C11: /* Write_Stored_Link_Key: num_keys_written(1) */
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x0C12: /* Delete_Stored_Link_Key: num_keys_deleted(2) */
        send_cmd_complete(fd, opcode, 0x00, p, 2);
        break;

    case 0x0C14: /* Read_Local_Name (248 bytes) */
        memset(p, 0, 248);
        memcpy(p, g_name, strlen(g_name));
        send_cmd_complete(fd, opcode, 0x00, p, 248);
        break;

    case 0x080E: /* Read_Default_Link_Policy_Settings */
    case 0x0C15: /* Read_Connection_Accept_Timeout */
    case 0x0C17: /* Read_Page_Timeout */
        p[0] = 0x00; p[1] = 0x20;
        send_cmd_complete(fd, opcode, 0x00, p, 2);
        break;

    case 0x0C09: /* Read_PIN_Type */
    case 0x0C19: /* Read_Scan_Enable */
    case 0x0C1F: /* Read_Authentication_Enable */
    case 0x0C21: /* Read_Encryption_Mode */
    case 0x0C42: /* Read_Inquiry_Scan_Type */
    case 0x0C44: /* Read_Inquiry_Mode */
    case 0x0C46: /* Read_Page_Scan_Type */
    case 0x0C55: /* Read_Simple_Pairing_Mode */
    case 0x0C5A: /* Read_Default_Erroneous_Data_Reporting */
    case 0x0C79: /* Read_Secure_Connections_Host_Support */
        p[0] = (opcode == 0x0C55 || opcode == 0x0C79) ? 0x01 : 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x0C51: /* Read_Extended_Inquiry_Response (fec + 240 bytes) */
        send_cmd_complete(fd, opcode, 0x00, p, 241);
        break;

    case 0x0C36: /* Read_Link_Supervision_Timeout */
        send_cmd_complete(fd, opcode, 0x00, p, 4);
        break;

    case 0x0C1B: /* Read_Page_Scan_Activity */
    case 0x0C1D: /* Read_Inquiry_Scan_Activity */
        p[0] = 0x00; p[1] = 0x08; p[2] = 0x12; p[3] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 4);
        break;

    case 0x0C23: /* Read_Class_of_Device */
        p[0] = 0x0C; p[1] = 0x02; p[2] = 0x5A; /* Smartphone */
        send_cmd_complete(fd, opcode, 0x00, p, 3);
        break;

    case 0x0C25: /* Read_Voice_Setting */
        p[0] = 0x60; p[1] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 2);
        break;

    case 0x0C38: /* Read_Number_Of_Supported_IAC */
        p[0] = 1;
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x0C39: /* Read_Current_IAC_LAP */
        p[0] = 1; p[1] = 0x33; p[2] = 0x8B; p[3] = 0x9E;
        send_cmd_complete(fd, opcode, 0x00, p, 4);
        break;

    case 0x0C58: /* Read_Inquiry_Response_Transmit_Power_Level */
        p[0] = 0x04;
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x0C6C: /* Read_LE_Host_Supported */
        p[0] = 0x01; p[1] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 2);
        break;

    case 0x2002: /* LE_Read_Buffer_Size */
        p[0] = 0xFB; p[1] = 0x00; /* 251 bytes */
        p[2] = 0x08;              /* 8 packets */
        send_cmd_complete(fd, opcode, 0x00, p, 3);
        break;

    case 0x2003: /* LE_Read_Local_Supported_Features */
        p[0] = 0xFF; p[1] = 0x01; p[2] = 0x00; p[3] = 0x00;
        p[4] = 0x00; p[5] = 0x00; p[6] = 0x00; p[7] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 8);
        break;

    case 0x2007: /* LE_Read_Advertising_Physical_Channel_Tx_Power */
        p[0] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x200F: /* LE_Read_Filter_Accept_List_Size */
    case 0x202A: /* LE_Read_Resolving_List_Size */
        p[0] = 16;
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x2018: /* LE_Rand (8 bytes) */
        p[0] = 0x42; p[1] = 0x19; p[2] = 0xA7; p[3] = 0x3C;
        p[4] = 0x91; p[5] = 0x5E; p[6] = 0x08; p[7] = 0xD4;
        send_cmd_complete(fd, opcode, 0x00, p, 8);
        break;

    case 0x201C: /* LE_Read_Supported_States */
        p[0] = 0xFF; p[1] = 0xFF; p[2] = 0xFF; p[3] = 0xFF;
        p[4] = 0xFF; p[5] = 0x03; p[6] = 0x00; p[7] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 8);
        break;

    case 0x2023: /* LE_Read_Suggested_Default_Data_Length */
        p[0] = 0xFB; p[1] = 0x00; p[2] = 0x48; p[3] = 0x08;
        send_cmd_complete(fd, opcode, 0x00, p, 4);
        break;

    case 0x202F: /* LE_Read_Maximum_Data_Length */
        p[0] = 0xFB; p[1] = 0x00; p[2] = 0x48; p[3] = 0x08;
        p[4] = 0xFB; p[5] = 0x00; p[6] = 0x48; p[7] = 0x08;
        send_cmd_complete(fd, opcode, 0x00, p, 8);
        break;

    case 0x203A: /* LE_Read_Maximum_Advertising_Data_Length */
        p[0] = 0x1F; p[1] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 2);
        break;

    case 0x203B: /* LE_Read_Number_of_Supported_Advertising_Sets */
        p[0] = 1;
        send_cmd_complete(fd, opcode, 0x00, p, 1);
        break;

    case 0x204B: /* LE_Read_Transmit_Power */
        p[0] = 0xEC; p[1] = 0x0A; /* -20 dBm to +10 dBm */
        send_cmd_complete(fd, opcode, 0x00, p, 2);
        break;

    case 0x2060: /* LE_Read_Buffer_Size_V2 */
        p[0] = 0xFB; p[1] = 0x00; p[2] = 0x08;
        p[3] = 0x00; p[4] = 0x00; p[5] = 0x00;
        send_cmd_complete(fd, opcode, 0x00, p, 6);
        break;

    default:
        __android_log_print(5, "DroidCloudBT", "Unhandled opcode=0x%04x len=%u", opcode, (unsigned)len);
        /* Vendor-specific (OGF 0x3F) -> Unknown HCI Command so stack uses standard path */
        if ((opcode >> 10) == 0x3F) {
            send_cmd_complete(fd, opcode, 0x01, 0, 0);
        } else {
            send_cmd_complete(fd, opcode, 0x00, 0, 0);
        }
        break;
    }
}

__attribute__((used)) int dc_bt_main(void) {
    load_config();
    for (;;) {
        int fd = open("/dev/vhci", O_RDWR);
        if (fd < 0) {
            sleep(1);
            continue;
        }
        uint8_t create_pkt[2] = {HCI_VENDOR_PKT, HCI_PRIMARY};
        if (write(fd, create_pkt, sizeof(create_pkt)) != (long)sizeof(create_pkt)) {
            close(fd);
            sleep(1);
            continue;
        }
        __android_log_print(4, "DroidCloudBT", "Created virtual hci0 BD_ADDR=%02x:%02x:%02x:%02x:%02x:%02x",
                            g_bdaddr[5], g_bdaddr[4], g_bdaddr[3], g_bdaddr[2], g_bdaddr[1], g_bdaddr[0]);

        uint8_t buf[512];
        for (;;) {
            long n = read(fd, buf, sizeof(buf));
            if (n <= 0) break;
            if (buf[0] == HCI_COMMAND_PKT && n >= 4) {
                uint16_t opcode = (uint16_t)(buf[1] | (buf[2] << 8));
                size_t plen = buf[3];
                if (plen + 4 > (size_t)n) plen = (size_t)n - 4;
                handle_command(fd, opcode, buf + 4, plen);
            }
        }
        close(fd);
        usleep(500000);
    }
    return 0;
}

__attribute__((naked, noreturn, visibility("default")))
void _start(void) {
    __asm__ volatile (
        "andq $-16, %rsp\n"
        "call dc_bt_main\n"
        "movl %eax, %edi\n"
        "call _exit\n"
    );
}
