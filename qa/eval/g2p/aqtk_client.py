"""
调用 aqtk_worker 子进程批量合成，崩溃时定位到具体输入。

用法：
    with AqtkClient(lib_path) as c:
        results = c.run(koe_list)   # [(code, wav_bytes)]；崩溃的输入 code = CRASH
"""

import json
import os
import subprocess
import sys

CRASH = -1
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aqtk_worker.py")


class AqtkClient:
    def __init__(self, lib_path, python=None):
        self.lib_path = lib_path
        self.python = python or sys.executable
        self.encoding = None
        self.crashes = []
        self._proc = None

    def _start(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self._proc = subprocess.Popen(
            [self.python, WORKER, self.lib_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, env=env,
        )
        head = self._proc.stdout.readline()
        if not head.startswith("#encoding"):
            raise RuntimeError(f"工作进程启动失败: {head!r}")
        self.encoding = head.split("\t")[1].strip()

    def _stop(self):
        if self._proc:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            self._proc.wait(timeout=10)
            self._proc = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, *exc):
        self._stop()

    def synth(self, koe):
        """合成一条；工作进程退出则记为崩溃并重启"""
        if self._proc is None:
            self._start()
        try:
            self._proc.stdin.write(json.dumps(koe, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        except (BrokenPipeError, OSError):
            line = ""
        if not line:
            rc = self._proc.wait(timeout=10)
            self._proc = None
            self.crashes.append({"koe": koe, "exit_code": rc})
            return CRASH, 0
        code, n = line.rstrip("\n").split("\t")
        return int(code), int(n)

    def run(self, koes):
        return [self.synth(k) for k in koes]
