#include <stddef.h>
#include <stdarg.h>
#include <stdbool.h>

typedef signed char int8_t;
typedef unsigned char uint8_t;
typedef short int16_t;
typedef unsigned short uint16_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef long int64_t;
typedef unsigned long uint64_t;
typedef unsigned long uintptr_t;

typedef uint8_t  jboolean;
typedef int32_t  jint;
typedef int32_t  jsize;
typedef void    *jobject;
typedef jobject  jclass;
typedef jobject  jstring;
typedef jobject  jarray;
typedef jarray   jobjectArray;
typedef jarray   jintArray;
typedef void    *jfieldID;
typedef void    *jmethodID;

#define JNI_FALSE 0
#define JNI_TRUE  1
#define JNI_OK    0

typedef struct {
    const char *name;
    const char *signature;
    void       *fnPtr;
} JNINativeMethod;

struct JNIInvokeInterface_;
typedef const struct JNIInvokeInterface_ *JavaVM;

struct JNINativeInterface_;
typedef const struct JNINativeInterface_ *JNIEnv;

/* Minimal JNI 1.6 function table with exact ABI slot indices. */
struct JNINativeInterface_ {
    void *pad0[6];                                                              /* 0..5   */
    jclass       (*FindClass)(JNIEnv *, const char *);                          /* 6  0x30 */
    void *pad1[10];                                                             /* 7..16  */
    void         (*ExceptionClear)(JNIEnv *);                                   /* 17 0x88 */
    void *pad2[5];                                                              /* 18..22 */
    void         (*DeleteLocalRef)(JNIEnv *, jobject);                          /* 23 0xb8 */
    void *pad3[9];                                                              /* 24..32 */
    jmethodID    (*GetMethodID)(JNIEnv *, jclass, const char *, const char *);  /* 33 0x108 */
    void *pad4[27];                                                             /* 34..60 */
    void         (*CallVoidMethod)(JNIEnv *, jobject, jmethodID, ...);          /* 61 0x1e8 */
    void *pad5[82];                                                             /* 62..143 */
    jfieldID     (*GetStaticFieldID)(JNIEnv *, jclass, const char *, const char *); /* 144 0x480 */
    jobject      (*GetStaticObjectField)(JNIEnv *, jclass, jfieldID);           /* 145 0x488 */
    void *pad6[8];                                                              /* 146..153 */
    void         (*SetStaticObjectField)(JNIEnv *, jclass, jfieldID, jobject);  /* 154 0x4d0 */
    void         (*SetStaticBooleanField)(JNIEnv *, jclass, jfieldID, jboolean);/* 155 0x4d8 */
    void *pad7[11];                                                             /* 156..166 */
    jstring      (*NewStringUTF)(JNIEnv *, const char *);                       /* 167 0x538 */
    void *pad8[1];                                                              /* 168    */
    const char  *(*GetStringUTFChars)(JNIEnv *, jstring, jboolean *);           /* 169 0x548 */
    void         (*ReleaseStringUTFChars)(JNIEnv *, jstring, const char *);     /* 170 0x550 */
    void *pad9[1];                                                              /* 171    */
    jobjectArray (*NewObjectArray)(JNIEnv *, jsize, jclass, jobject);           /* 172 0x560 */
    void *pad10[1];                                                             /* 173    */
    void         (*SetObjectArrayElement)(JNIEnv *, jobjectArray, jsize, jobject); /* 174 0x570 */
    void *pad11[44];                                                            /* 175..218 */
    jint         (*GetJavaVM)(JNIEnv *, JavaVM **);                             /* 219 0x6d8 */
};

struct JNIInvokeInterface_ {
    void *pad0[4];                                                              /* 0..3   */
    jint (*AttachCurrentThread)(JavaVM *, void **, void *);                     /* 4  0x20 */
    jint (*DetachCurrentThread)(JavaVM *);                                      /* 5  0x28 */
};

_Static_assert(offsetof(struct JNINativeInterface_, FindClass)             == 0x30,  "FindClass");
_Static_assert(offsetof(struct JNINativeInterface_, ExceptionClear)        == 0x88,  "ExceptionClear");
_Static_assert(offsetof(struct JNINativeInterface_, DeleteLocalRef)        == 0xb8,  "DeleteLocalRef");
_Static_assert(offsetof(struct JNINativeInterface_, GetMethodID)           == 0x108, "GetMethodID");
_Static_assert(offsetof(struct JNINativeInterface_, CallVoidMethod)        == 0x1e8, "CallVoidMethod");
_Static_assert(offsetof(struct JNINativeInterface_, GetStaticFieldID)      == 0x480, "GetStaticFieldID");
_Static_assert(offsetof(struct JNINativeInterface_, GetStaticObjectField)  == 0x488, "GetStaticObjectField");
_Static_assert(offsetof(struct JNINativeInterface_, SetStaticObjectField)  == 0x4d0, "SetStaticObjectField");
_Static_assert(offsetof(struct JNINativeInterface_, SetStaticBooleanField) == 0x4d8, "SetStaticBooleanField");
_Static_assert(offsetof(struct JNINativeInterface_, NewStringUTF)          == 0x538, "NewStringUTF");
_Static_assert(offsetof(struct JNINativeInterface_, GetStringUTFChars)     == 0x548, "GetStringUTFChars");
_Static_assert(offsetof(struct JNINativeInterface_, ReleaseStringUTFChars) == 0x550, "ReleaseStringUTFChars");
_Static_assert(offsetof(struct JNINativeInterface_, NewObjectArray)        == 0x560, "NewObjectArray");
_Static_assert(offsetof(struct JNINativeInterface_, SetObjectArrayElement) == 0x570, "SetObjectArrayElement");
_Static_assert(offsetof(struct JNINativeInterface_, GetJavaVM)             == 0x6d8, "GetJavaVM");
_Static_assert(offsetof(struct JNIInvokeInterface_, AttachCurrentThread)   == 0x20,  "AttachCurrentThread");
_Static_assert(offsetof(struct JNIInvokeInterface_, DetachCurrentThread)   == 0x28,  "DetachCurrentThread");

/* Android bionic libc / liblog imports (linked directly against device bionic .so) */
extern int pthread_create(void *thread, const void *attr, void *(*start_routine)(void *), void *arg);
extern int pthread_detach(unsigned long thread);
extern int usleep(unsigned int usec);
extern int __android_log_print(int prio, const char *tag, const char *fmt, ...);

static int my_strcmp(const char *a, const char *b) {
    if (!a || !b) return -1;
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

static bool my_starts_with(const char *s, const char *p) {
    if (!s || !p) return false;
    while (*p) {
        if (*s++ != *p++) return false;
    }
    return true;
}

static void copy_str(char *dst, size_t cap, const char *src) {
    if (!dst || cap == 0) return;
    size_t i = 0;
    if (src) {
        while (src[i] && i + 1 < cap) {
            dst[i] = src[i];
            i++;
        }
    }
    dst[i] = 0;
}

static void basename_copy(char *dst, size_t cap, const char *path) {
    if (!path || !*path) {
        dst[0] = 0;
        return;
    }
    const char *last = path;
    for (const char *p = path; *p; p++) {
        if (*p == '/' && *(p + 1) != '\0') last = p + 1;
    }
    copy_str(dst, cap, last);
}

static bool should_disable_ppu(const char *pkg, const char *proc) {
    const char *s = (pkg && *pkg) ? pkg : proc;
    if (!s || !*s) return false;
    return my_starts_with(s, "com.google.") ||
           my_starts_with(s, "com.samsung.android.") ||
           my_strcmp(s, "com.android.vending") == 0 ||
           my_strcmp(s, "com.android.chrome") == 0 ||
           my_strcmp(s, "com.netflix.mediaclient") == 0 ||
           my_strcmp(s, "com.snapchat.android") == 0 ||
           my_strcmp(s, "com.breel.wallpapers20") == 0 ||
           my_strcmp(s, "com.nothing.smartcenter") == 0 ||
           my_starts_with(proc, "com.google.");
}

typedef struct {
    jint *uid;
    jint *gid;
    jintArray *gids;
    jint *runtime_flags;
    jobjectArray *rlimits;
    jint *mount_external;
    jstring *se_info;
    jstring *nice_name;
    jstring *instruction_set;
    jstring *app_data_dir;
} AppSpecializeArgs;

struct api_table;
typedef struct {
    long api_version;
    void *impl;
    void (*preAppSpecialize)(void *, AppSpecializeArgs *);
    void (*postAppSpecialize)(void *, const AppSpecializeArgs *);
    void (*preServerSpecialize)(void *, void *);
    void (*postServerSpecialize)(void *, const void *);
} module_abi;

struct api_table {
    void *impl;
    bool (*registerModule)(struct api_table *, module_abi *);
    void (*hookJniNativeMethods)(JNIEnv *, const char *, JNINativeMethod *, int);
    void (*pltHookRegister)(unsigned long, unsigned long, const char *, void *, void **);
    bool (*exemptFd)(int);
    bool (*pltHookCommit)(void);
    int  (*connectCompanion)(void *);
    void (*setOption)(void *, int);
    int  (*getModuleDir)(void *);
    uint32_t (*getFlags)(void *);
};

static struct api_table *g_api = 0;
static JNIEnv *g_env = 0;
static JavaVM *g_vm = 0;
static bool g_target = false;
static bool g_is_settings = false;
static char g_proc[128];
static char g_pkg[128];

typedef jstring (*orig_native_get1_fn)(JNIEnv *, jclass, jstring);
typedef jstring (*orig_native_get2_fn)(JNIEnv *, jclass, jstring, jstring);
static orig_native_get1_fn g_orig_get1 = 0;
static orig_native_get2_fn g_orig_get2 = 0;

static jstring map_settings_prop(JNIEnv *env, jclass cls, const char *key, jstring jdef) {
    if (!key) return 0;
    if (my_strcmp(key, "ro.bliss.serialnumber") == 0) {
        jstring k = (*env)->NewStringUTF(env, "ro.serialno");
        jstring r = g_orig_get2 ? g_orig_get2(env, cls, k, jdef) : (g_orig_get1 ? g_orig_get1(env, cls, k) : 0);
        if (k) (*env)->DeleteLocalRef(env, k);
        return r;
    }
    if (my_strcmp(key, "ro.bliss.cpuname") == 0) {
        char out[128];
        out[0] = 0;
        jstring km = (*env)->NewStringUTF(env, "ro.soc.manufacturer");
        jstring kd = (*env)->NewStringUTF(env, "ro.soc.model");
        jstring empty = (*env)->NewStringUTF(env, "");
        jstring vm = g_orig_get2 ? g_orig_get2(env, cls, km, empty) : (g_orig_get1 ? g_orig_get1(env, cls, km) : 0);
        jstring vd = g_orig_get2 ? g_orig_get2(env, cls, kd, empty) : (g_orig_get1 ? g_orig_get1(env, cls, kd) : 0);
        const char *sm = vm ? (*env)->GetStringUTFChars(env, vm, 0) : 0;
        const char *sd = vd ? (*env)->GetStringUTFChars(env, vd, 0) : 0;
        size_t pos = 0;
        if (sm && *sm) {
            for (size_t i = 0; sm[i] && pos + 2 < sizeof(out); i++) out[pos++] = sm[i];
        }
        if (sd && *sd) {
            if (pos > 0 && pos + 2 < sizeof(out)) out[pos++] = ' ';
            for (size_t i = 0; sd[i] && pos + 1 < sizeof(out); i++) out[pos++] = sd[i];
        }
        out[pos] = 0;
        if (sm) (*env)->ReleaseStringUTFChars(env, vm, sm);
        if (sd) (*env)->ReleaseStringUTFChars(env, vd, sd);
        if (vm) (*env)->DeleteLocalRef(env, vm);
        if (vd) (*env)->DeleteLocalRef(env, vd);
        if (km) (*env)->DeleteLocalRef(env, km);
        if (kd) (*env)->DeleteLocalRef(env, kd);
        if (empty) (*env)->DeleteLocalRef(env, empty);
        if (out[0]) return (*env)->NewStringUTF(env, out);
        return 0;
    }
    if (my_strcmp(key, "ro.bliss.version") == 0) {
        jstring k = (*env)->NewStringUTF(env, "ro.build.version.incremental");
        jstring r = g_orig_get2 ? g_orig_get2(env, cls, k, jdef) : (g_orig_get1 ? g_orig_get1(env, cls, k) : 0);
        if (k) (*env)->DeleteLocalRef(env, k);
        return r;
    }
    if (my_strcmp(key, "ro.bliss.codename") == 0 || my_strcmp(key, "ro.bliss.device") == 0) {
        jstring k = (*env)->NewStringUTF(env, "ro.product.device");
        jstring r = g_orig_get2 ? g_orig_get2(env, cls, k, jdef) : (g_orig_get1 ? g_orig_get1(env, cls, k) : 0);
        if (k) (*env)->DeleteLocalRef(env, k);
        return r;
    }
    if (my_strcmp(key, "ro.bliss.build.status") == 0) {
        return (*env)->NewStringUTF(env, "Official");
    }
    return 0;
}

static jstring hooked_native_get1(JNIEnv *env, jclass cls, jstring jkey) {
    if (jkey) {
        const char *key = (*env)->GetStringUTFChars(env, jkey, 0);
        jstring mapped = 0;
        if (key && my_starts_with(key, "ro.bliss.")) {
            jstring empty = (*env)->NewStringUTF(env, "");
            mapped = map_settings_prop(env, cls, key, empty);
            if (empty) (*env)->DeleteLocalRef(env, empty);
        }
        if (key) (*env)->ReleaseStringUTFChars(env, jkey, key);
        if (mapped) return mapped;
    }
    return g_orig_get1 ? g_orig_get1(env, cls, jkey) : 0;
}

static jstring hooked_native_get2(JNIEnv *env, jclass cls, jstring jkey, jstring jdef) {
    if (jkey) {
        const char *key = (*env)->GetStringUTFChars(env, jkey, 0);
        jstring mapped = 0;
        if (key && my_starts_with(key, "ro.bliss.")) {
            mapped = map_settings_prop(env, cls, key, jdef);
        }
        if (key) (*env)->ReleaseStringUTFChars(env, jkey, key);
        if (mapped) return mapped;
    }
    return g_orig_get2 ? g_orig_get2(env, cls, jkey, jdef) : jdef;
}

static void hook_settings_props(JNIEnv *env) {
    if (!g_api || !g_api->hookJniNativeMethods) return;
    JNINativeMethod methods[2] = {
        {"native_get", "(Ljava/lang/String;)Ljava/lang/String;", (void *)hooked_native_get1},
        {"native_get", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", (void *)hooked_native_get2},
    };
    g_api->hookJniNativeMethods(env, "android/os/SystemProperties", methods, 2);
    g_orig_get1 = (orig_native_get1_fn)methods[0].fnPtr;
    g_orig_get2 = (orig_native_get2_fn)methods[1].fnPtr;
    __android_log_print(4, "DroidCloudUnPPU", "Hooked SystemProperties for com.android.settings");
}

static void clear_static_map(JNIEnv *env, jclass ppu_cls, jmethodID map_clear, const char *field_name) {
    jfieldID fid = (*env)->GetStaticFieldID(env, ppu_cls, field_name, "Ljava/util/Map;");
    if (!fid) {
        (*env)->ExceptionClear(env);
        return;
    }
    jobject map = (*env)->GetStaticObjectField(env, ppu_cls, fid);
    if (map) {
        (*env)->CallVoidMethod(env, map, map_clear);
        (*env)->ExceptionClear(env);
        (*env)->DeleteLocalRef(env, map);
    } else {
        (*env)->ExceptionClear(env);
    }
}

static void set_static_str_array(JNIEnv *env, jclass ppu_cls, const char *field_name, jobjectArray arr) {
    jfieldID fid = (*env)->GetStaticFieldID(env, ppu_cls, field_name, "[Ljava/lang/String;");
    if (!fid) {
        (*env)->ExceptionClear(env);
        return;
    }
    (*env)->SetStaticObjectField(env, ppu_cls, fid, arr);
    (*env)->ExceptionClear(env);
}

static void clear_flags_only(JNIEnv *env) {
    jclass cls = (*env)->FindClass(env, "com/android/internal/util/bliss/PixelPropsUtils");
    if (!cls) {
        (*env)->ExceptionClear(env);
        return;
    }
    jfieldID f_gms = (*env)->GetStaticFieldID(env, cls, "sIsGms", "Z");
    if (f_gms) (*env)->SetStaticBooleanField(env, cls, f_gms, JNI_FALSE);
    else (*env)->ExceptionClear(env);
    jfieldID f_fin = (*env)->GetStaticFieldID(env, cls, "sIsFinsky", "Z");
    if (f_fin) (*env)->SetStaticBooleanField(env, cls, f_fin, JNI_FALSE);
    else (*env)->ExceptionClear(env);
    (*env)->DeleteLocalRef(env, cls);
}

/*
 * Completely disable BlissOS framework PixelPropsUtils spoofing in this app process
 * BEFORE ActivityThread -> Instrumentation.newApplication() invokes PixelPropsUtils.setProps().
 *
 * Without this, BlissOS framework.jar overwrites android.os.Build in Java:
 *   - com.google.android.gms.unstable -> google/zork/zork_cheets (Chromebook!)
 *   - com.google.android.gms / com.android.vending / com.android.chrome -> Pixel 5 / Pixel 7 Pro
 *   - com.google.android.apps.photos / snapchat -> Pixel XL
 * By placing the current package in packagesToKeep and clearing every spoof map/list,
 * PixelPropsUtils.setProps() returns immediately on line 1, leaving our real device identity intact.
 */
static void disable_ppu(JNIEnv *env) {
    jclass cls = (*env)->FindClass(env, "com/android/internal/util/bliss/PixelPropsUtils");
    if (!cls) {
        (*env)->ExceptionClear(env);
        return;
    }
    jclass str_cls = (*env)->FindClass(env, "java/lang/String");
    if (str_cls) {
        jobjectArray keep = (*env)->NewObjectArray(env, 2, str_cls, 0);
        if (keep) {
            jstring s_pkg = (*env)->NewStringUTF(env, g_pkg[0] ? g_pkg : g_proc);
            jstring s_proc = (*env)->NewStringUTF(env, g_proc[0] ? g_proc : g_pkg);
            if (s_pkg) {
                (*env)->SetObjectArrayElement(env, keep, 0, s_pkg);
                (*env)->DeleteLocalRef(env, s_pkg);
            }
            if (s_proc) {
                (*env)->SetObjectArrayElement(env, keep, 1, s_proc);
                (*env)->DeleteLocalRef(env, s_proc);
            }
            set_static_str_array(env, cls, "packagesToKeep", keep);
            (*env)->DeleteLocalRef(env, keep);
        }
        jobjectArray empty = (*env)->NewObjectArray(env, 0, str_cls, 0);
        if (empty) {
            set_static_str_array(env, cls, "extraPackagesToChange", empty);
            set_static_str_array(env, cls, "packagesToChangePixel7Pro", empty);
            set_static_str_array(env, cls, "packagesToChangePixelXL", empty);
            (*env)->DeleteLocalRef(env, empty);
        }
        (*env)->DeleteLocalRef(env, str_cls);
    } else {
        (*env)->ExceptionClear(env);
    }

    jclass map_cls = (*env)->FindClass(env, "java/util/Map");
    if (map_cls) {
        jmethodID map_clear = (*env)->GetMethodID(env, map_cls, "clear", "()V");
        if (map_clear) {
            clear_static_map(env, cls, map_clear, "propsToChange");
            clear_static_map(env, cls, map_clear, "propsToChangePixel7Pro");
            clear_static_map(env, cls, map_clear, "propsToChangePixel5");
            clear_static_map(env, cls, map_clear, "propsToChangePixelXL");
        } else {
            (*env)->ExceptionClear(env);
        }
        (*env)->DeleteLocalRef(env, map_cls);
    } else {
        (*env)->ExceptionClear(env);
    }

    jfieldID f_gms = (*env)->GetStaticFieldID(env, cls, "sIsGms", "Z");
    if (f_gms) (*env)->SetStaticBooleanField(env, cls, f_gms, JNI_FALSE);
    else (*env)->ExceptionClear(env);

    jfieldID f_fin = (*env)->GetStaticFieldID(env, cls, "sIsFinsky", "Z");
    if (f_fin) (*env)->SetStaticBooleanField(env, cls, f_fin, JNI_FALSE);
    else (*env)->ExceptionClear(env);

    (*env)->DeleteLocalRef(env, cls);
}

static void *worker_thread(void *arg) {
    (void)arg;
    if (!g_vm) return 0;
    JNIEnv *env = 0;
    if ((*g_vm)->AttachCurrentThread(g_vm, (void **)&env, 0) != JNI_OK || !env) return 0;
    for (int i = 0; i < 15; i++) {
        clear_flags_only(env);
        usleep(100000);
    }
    (*g_vm)->DetachCurrentThread(g_vm);
    return 0;
}

static void pre_app(void *impl, AppSpecializeArgs *args) {
    (void)impl;
    g_target = false;
    g_is_settings = false;
    g_proc[0] = 0;
    g_pkg[0] = 0;
    if (!g_env || !args || !args->nice_name || !*(args->nice_name)) {
        if (g_api && g_api->setOption) g_api->setOption(g_api->impl, 1);
        return;
    }
    const char *name = (*g_env)->GetStringUTFChars(g_env, *(args->nice_name), 0);
    if (name) {
        copy_str(g_proc, sizeof(g_proc), name);
        (*g_env)->ReleaseStringUTFChars(g_env, *(args->nice_name), name);
    }
    if (args->app_data_dir && *(args->app_data_dir)) {
        const char *dir = (*g_env)->GetStringUTFChars(g_env, *(args->app_data_dir), 0);
        if (dir) {
            basename_copy(g_pkg, sizeof(g_pkg), dir);
            (*g_env)->ReleaseStringUTFChars(g_env, *(args->app_data_dir), dir);
        }
    }
    if (!g_pkg[0] && g_proc[0]) {
        copy_str(g_pkg, sizeof(g_pkg), g_proc);
        for (size_t i = 0; g_pkg[i]; i++) {
            if (g_pkg[i] == ':') { g_pkg[i] = 0; break; }
        }
    }
    if (my_strcmp(g_pkg, "com.android.settings") == 0 || my_strcmp(g_proc, "com.android.settings") == 0) {
        g_is_settings = true;
    } else if (should_disable_ppu(g_pkg, g_proc)) {
        g_target = true;
    }
    if (!g_target && !g_is_settings && g_api && g_api->setOption) {
        g_api->setOption(g_api->impl, 1); /* DLCLOSE_MODULE_LIBRARY */
    }
}

static void post_app(void *impl, const AppSpecializeArgs *args) {
    (void)impl;
    (void)args;
    if (!g_env) return;
    if (g_is_settings) {
        hook_settings_props(g_env);
        return;
    }
    if (!g_target) return;
    disable_ppu(g_env);
    __android_log_print(4, "DroidCloudUnPPU", "Disabled BlissOS PixelPropsUtils in %s (pkg=%s)", g_proc, g_pkg);
    if ((*g_env)->GetJavaVM(g_env, &g_vm) == JNI_OK && g_vm) {
        unsigned long tid = 0;
        if (pthread_create(&tid, 0, worker_thread, 0) == 0) {
            pthread_detach(tid);
        }
    }
}

static void noop_srv(void *impl, void *args) {
    (void)impl; (void)args;
    if (g_api && g_api->setOption) g_api->setOption(g_api->impl, 1);
}

static void noop_srv_const(void *impl, const void *args) {
    (void)impl; (void)args;
}

static int g_dummy_impl = 1;
static module_abi g_abi = {
    .api_version = 4,
    .impl = &g_dummy_impl,
    .preAppSpecialize = pre_app,
    .postAppSpecialize = post_app,
    .preServerSpecialize = noop_srv,
    .postServerSpecialize = noop_srv_const,
};

__attribute__((visibility("default")))
void zygisk_module_entry(struct api_table *table, JNIEnv *env) {
    g_api = table;
    g_env = env;
    if (!table || !table->registerModule) return;
    table->registerModule(table, &g_abi);
}
