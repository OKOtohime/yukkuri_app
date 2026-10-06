"""
QR-MNT-01：在"干净环境"中运行上游单测（跨平台）

干净环境 = 源码副本（不含 qa/ 和 .git）+ 空数据目录 + 临时 HOME/USERPROFILE/LOCALAPPDATA
+ 去掉音频服务相关环境变量（XDG_RUNTIME_DIR、PULSE_SERVER、PIPEWIRE_*），程序找不到模型、声线库和声音服务器。
结果：<out>/junit-upstream-<tag>-clean.xml、<out>/cov-upstream-<tag>-clean.json

用法：python qa/eval/mnt/run_upstream_clean.py --tag R1 --out qa/reports/wp02
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DROP_PREFIXES = ("YUKKURI_", "AQUESTALK_", "XDG_", "PULSE_", "PIPEWIRE_", "DBUS_", "PYTHONPATH", "VIRTUAL_ENV")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="yk-clean-") as tmp:
        src = os.path.join(tmp, "src")
        everywhere = shutil.ignore_patterns(".git", "__pycache__", "*.egg-info", "*.onnx")
        top_level = shutil.ignore_patterns("qa", ".venv", ".claude", "libs", "model", "model_*", "sensevoice",
                                           "sherpa-onnx*", ".env")

        def ignore(d, names):
            hit = set(everywhere(d, names))
            if os.path.abspath(d) == REPO:
                hit |= set(top_level(d, names))
            return hit

        shutil.copytree(REPO, src, ignore=ignore)
        home = os.path.join(tmp, "home")
        data = os.path.join(tmp, "data")
        os.makedirs(home)
        os.makedirs(data)
        env = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
        env.update(HOME=home, USERPROFILE=home, LOCALAPPDATA=os.path.join(home, "AppData", "Local"),
                   APPDATA=os.path.join(home, "AppData", "Roaming"), YUKKURI_DATA_DIR=data,
                   PYTHONIOENCODING="utf-8")
        cmd = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--color=no",
               "--cov=yukkuri", f"--cov-report=json:{os.path.join(out, f'cov-upstream-{args.tag}-clean.json')}",
               f"--junitxml={os.path.join(out, f'junit-upstream-{args.tag}-clean.xml')}"]
        res = subprocess.run(cmd, cwd=src, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = "\n".join(res.stdout.splitlines()[-15:])
        print(tail)
        with open(os.path.join(out, f"upstream-{args.tag}-clean.log"), "w", encoding="utf-8") as f:
            f.write(res.stdout + "\n" + res.stderr)
        return res.returncode


if __name__ == "__main__":
    sys.exit(main())
