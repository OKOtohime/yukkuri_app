"""
AquesTalk 合成工作进程：从 stdin 逐行读 JSON 字符串（音标），向 stdout 逐行写返回码。

返回码：0 表示合成成功（返回了 WAV），否则是 AquesTalk 写入 size 的错误码。
放在子进程里运行，库崩溃（段错误）时只有这个进程退出，调用方据此记"崩溃"并定位输入。

与产品代码一致：库导出 AquesTalk_Synthe_Utf8 时用 UTF-8，否则用 Shift-JIS 且 errors='ignore'
（IMPL-14，`yukkuri/tts/aquestalk1.py`）。每行输出 "<code>\t<wav字节数>"。
"""

import ctypes
import json
import sys


def open_lib(path):
    lib = ctypes.cdll.LoadLibrary(path)
    if hasattr(lib, "AquesTalk_Synthe_Utf8"):
        fn, enc = lib.AquesTalk_Synthe_Utf8, "utf-8"
    else:
        fn, enc = lib.AquesTalk_Synthe, "shift-jis"
    fn.restype = ctypes.POINTER(ctypes.c_ubyte)
    fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.AquesTalk_FreeWave.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    return lib, fn, enc


def synth(lib, fn, enc, koe, speed=100):
    size = ctypes.c_int(0)
    ptr = fn(koe.encode(enc, errors="ignore"), speed, ctypes.byref(size))
    if not ptr:
        return size.value, 0
    n = size.value
    lib.AquesTalk_FreeWave(ptr)
    return 0, n


def main():
    lib, fn, enc = open_lib(sys.argv[1])
    sys.stdout.write(f"#encoding\t{enc}\n")
    sys.stdout.flush()
    for line in sys.stdin:
        koe = json.loads(line)
        code, n = synth(lib, fn, enc, koe)
        sys.stdout.write(f"{code}\t{n}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
