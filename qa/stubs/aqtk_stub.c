/*
 * AquesTalk1 桩库（WP-04 L1）：导出与评价版同名的 5 个符号，供 ctypes 真实加载。
 *
 * - 返回合法的 8 kHz 16 bit 单声道 WAV。采样数 = 800 * 100 / speed；数据区开头写入
 *   "VOICE=<本库所在目录名>;SPEED=<speed>;IF=<utf8|sjis>;"，用例据此识别声线、语速和编码接口。
 * - 音标以 "#E<n>" 开头时返回 NULL，并把 n 写回 *size（模拟 AquesTalk 错误码）。
 * - SetDevKey/SetUsrKey：只有 "VALID-DEV" / "VALID-USR" 返回 0，其他返回 1。
 * - 环境变量 QA_STUB_LOG 指定的文件逐行追加调用记录：
 *     LOAD <voice>            （首次调用任一函数时）
 *     SetDevKey <voice> <key>
 *     SetUsrKey <voice> <key>
 *     Synthe <voice> <utf8|sjis> <speed> <音标的十六进制>
 *     FreeWave <voice>
 * - 编译时定义 NO_UTF8 则不导出 AquesTalk_Synthe_Utf8（覆盖 IMPL-14 的 Shift-JIS 分支）。
 * - 编译时定义 EMPTY_LIB 则不导出任何 AquesTalk 符号（模拟放错的库文件）。
 */
#if !defined(_WIN32) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#define EXPORT __declspec(dllexport)
#define CALLCONV __stdcall
#else
#include <dlfcn.h>
#define EXPORT __attribute__((visibility("default")))
#define CALLCONV
#endif

static char g_voice[256];

static void resolve_voice(void)
{
    char path[1024] = {0};
    if (g_voice[0])
        return;
#ifdef _WIN32
    HMODULE mod = NULL;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)&resolve_voice, &mod);
    GetModuleFileNameA(mod, path, sizeof(path) - 1);
#else
    Dl_info info;
    if (dladdr((void *)&resolve_voice, &info) && info.dli_fname)
        strncpy(path, info.dli_fname, sizeof(path) - 1);
#endif
    /* 取库文件所在目录的名字 */
    char *end = path + strlen(path);
    while (end > path && end[-1] != '/' && end[-1] != '\\')
        end--;
    if (end > path)
        end--; /* 指向分隔符 */
    *end = '\0';
    char *start = end;
    while (start > path && start[-1] != '/' && start[-1] != '\\')
        start--;
    strncpy(g_voice, start[0] ? start : "unknown", sizeof(g_voice) - 1);
}

static void log_line(const char *fmt, const char *a, const char *b, int n)
{
    char logpath[1024] = {0};
#ifdef _WIN32
    if (!GetEnvironmentVariableA("QA_STUB_LOG", logpath, sizeof(logpath)))
        return;
#else
    const char *p = getenv("QA_STUB_LOG");
    if (!p)
        return;
    strncpy(logpath, p, sizeof(logpath) - 1);
#endif
    FILE *f = fopen(logpath, "ab");
    if (!f)
        return;
    fprintf(f, fmt, g_voice, a ? a : "", b ? b : "", n);
    fclose(f);
}

static void touch(void)
{
    if (!g_voice[0]) {
        resolve_voice();
        log_line("LOAD %s%s%s\n", NULL, NULL, 0);
    }
}

static void hex(const unsigned char *s, char *out, size_t cap)
{
    size_t i = 0;
    for (; s[i] && 2 * i + 2 < cap; i++)
        sprintf(out + 2 * i, "%02x", s[i]);
    out[2 * i] = '\0';
}

static void put32(unsigned char *p, unsigned v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put16(unsigned char *p, unsigned v) { p[0] = v; p[1] = v >> 8; }

static unsigned char *synth(const char *koe, int speed, int *size, const char *iface)
{
    touch();
    static char hx[8192];
    hex((const unsigned char *)koe, hx, sizeof(hx));
    char sp[32];
    sprintf(sp, "%d", speed);
    {
        char buf[8300];
        snprintf(buf, sizeof(buf), "%s %s", iface, sp);
        log_line("Synthe %s %s %s\n", buf, hx, 0);
    }
    if (koe[0] == '\0') {
        *size = 103;
        return NULL;
    }
    if (koe[0] == '#' && koe[1] == 'E') {
        *size = atoi(koe + 2);
        return NULL;
    }
    if (speed < 50 || speed > 300) {
        /* 真库对越界语速静默钳位；桩记录原值后同样钳位，保持可比 */
        speed = speed < 50 ? 50 : 300;
    }
    unsigned nsamp = 800u * 100u / (unsigned)speed;
    unsigned datalen = nsamp * 2;
    unsigned char *w = (unsigned char *)calloc(1, 44 + datalen);
    memcpy(w, "RIFF", 4); put32(w + 4, 36 + datalen); memcpy(w + 8, "WAVEfmt ", 8);
    put32(w + 16, 16); put16(w + 20, 1); put16(w + 22, 1); put32(w + 24, 8000);
    put32(w + 28, 16000); put16(w + 32, 2); put16(w + 34, 16);
    memcpy(w + 36, "data", 4); put32(w + 40, datalen);
    char tag[400];
    int n = snprintf(tag, sizeof(tag), "VOICE=%s;SPEED=%d;IF=%s;", g_voice, speed, iface);
    if ((unsigned)n < datalen)
        memcpy(w + 44, tag, n);
    *size = (int)(44 + datalen);
    return w;
}

#ifndef EMPTY_LIB
#ifndef NO_UTF8
EXPORT unsigned char *CALLCONV AquesTalk_Synthe_Utf8(const char *koe, int iSpeed, int *pSize)
{
    return synth(koe, iSpeed, pSize, "utf8");
}
#endif

EXPORT unsigned char *CALLCONV AquesTalk_Synthe(const char *koe, int iSpeed, int *pSize)
{
    return synth(koe, iSpeed, pSize, "sjis");
}

EXPORT void CALLCONV AquesTalk_FreeWave(unsigned char *wav)
{
    touch();
    log_line("FreeWave %s%s%s\n", NULL, NULL, 0);
    free(wav);
}

EXPORT int CALLCONV AquesTalk_SetDevKey(const char *key)
{
    touch();
    log_line("SetDevKey %s %s%s\n", key, NULL, 0);
    return strcmp(key, "VALID-DEV") == 0 ? 0 : 1;
}

EXPORT int CALLCONV AquesTalk_SetUsrKey(const char *key)
{
    touch();
    log_line("SetUsrKey %s %s%s\n", key, NULL, 0);
    return strcmp(key, "VALID-USR") == 0 ? 0 : 1;
}
#else
EXPORT int qa_stub_empty(void) { return 0; }
#endif /* EMPTY_LIB */
