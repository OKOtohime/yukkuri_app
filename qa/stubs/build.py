"""
构建 AquesTalk 桩库（WP-04）。产物在 qa/stubs/build/（不入库）。

    python qa/stubs/build.py            # 本机平台（Linux: gcc → .so）
    python qa/stubs/build.py --windows  # 交叉编译 Windows DLL（llvm-mingw），复制到 W1 的 qa/stubs/build/

产物：
    build/linux/default/libAquesTalk.so    build/linux/no_utf8/libAquesTalk.so
    build/win/default/AquesTalk.dll        build/win/no_utf8/AquesTalk.dll
    build/*/empty/...（不导出任何 AquesTalk 符号）
"""

import argparse
import os
import shutil
import subprocess
import sys

STUB_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(STUB_DIR, "aqtk_stub.c")
BUILD = os.path.join(STUB_DIR, "build")
MINGW = os.environ.get("QA_MINGW_GCC") or shutil.which("x86_64-w64-mingw32-gcc") or "/opt/llvm-mingw/bin/x86_64-w64-mingw32-gcc"


def build_linux() -> list:
    out = []
    for variant, flags in (("default", []), ("no_utf8", ["-DNO_UTF8"]), ("empty", ["-DEMPTY_LIB"])):
        d = os.path.join(BUILD, "linux", variant)
        os.makedirs(d, exist_ok=True)
        target = os.path.join(d, "libAquesTalk.so")
        subprocess.run(["gcc", "-shared", "-fPIC", "-O1", "-Wall", *flags, "-o", target, SRC, "-ldl"], check=True)
        out.append(target)
    return out


def build_windows() -> list:
    out = []
    for variant, flags in (("default", []), ("no_utf8", ["-DNO_UTF8"]), ("empty", ["-DEMPTY_LIB"])):
        d = os.path.join(BUILD, "win", variant)
        os.makedirs(d, exist_ok=True)
        target = os.path.join(d, "AquesTalk.dll")
        subprocess.run([MINGW, "-shared", "-O1", "-Wall", *flags, "-o", target, SRC, "-static"], check=True)
        out.append(target)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", action="store_true")
    args = ap.parse_args()
    built = build_windows() if args.windows else build_linux()
    print("\n".join(built))
    sys.exit(0)
