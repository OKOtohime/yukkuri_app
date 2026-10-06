"""
本地模型下载服务（WP-04）：提供与真实发布物目录结构一致的假模型，并按 URL 前缀注入故障。

URL 形式：http://127.0.0.1:<port>/<mode>/<artifact>
    artifact: silero_vad.onnx | sensevoice.tar.bz2 | vosk-zh.zip | vosk-ja.zip | vosk-en.zip
    mode:
        ok          正常
        404         HTTP 404
        trunc_cl    发送 Content-Length（全长）后只发一半就断开
        trunc_nocl  不发 Content-Length，发一半后关闭连接（客户端无法判断是否完整）
        slow        限速 SLOW_BPS 字节/秒，可以完成
        stall       发完响应头和前 STALL_BYTES 字节后停滞，直到服务关闭
        redirect_http  302 到同一服务的 http 地址（QR-SEC-04 记录用）

假模型的目录结构按真实发布物：
    SenseVoice：sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/{model.int8.onnx, model.onnx, tokens.txt, ...}
                （顶层目录名和文件名与 WP-01 在 R1 真实下载解压的结果一致；归档内成员顺序未核对）
    Vosk：      vosk-model-small-<x>/{am/final.mdl, conf/..., graph/..., ivector/...}
                （en 由 test_wp04_real_download.py 在 R1 真实下载核对）
"""

import hashlib
import io
import os
import random
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENSE_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
VOSK_TOPS = {
    "zh": "vosk-model-small-cn-0.22",
    "ja": "vosk-model-small-ja-0.22",
    "en": "vosk-model-small-en-us-0.15",
}
SLOW_BPS = 200_000
STALL_BYTES = 4096


def _rand(n: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(n)


def build_artifacts(scale: int = 1) -> dict:
    """生成假模型。scale 放大文件体积（磁盘空间不足用例需要大于 tmpfs 容量）"""
    arts = {"silero_vad.onnx": _rand(640_000 * scale, 1)}

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        # 成员顺序与真实发布物一致：README、model.int8.onnx、model.onnx、tokens.txt、test_wavs
        for name, data in (
            ("README.md", b"fake sensevoice\n"),
            ("model.int8.onnx", _rand(600_000 * scale, 2)),
            ("model.onnx", _rand(300_000 * scale, 3)),
            ("tokens.txt", b"<blk> 0\n<sos/eos> 1\n" * 200),
            ("test_wavs/zh.wav", _rand(20_000, 4)),
        ):
            ti = tarfile.TarInfo(f"{SENSE_DIR}/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    arts["sensevoice.tar.bz2"] = buf.getvalue()

    for lang, top in VOSK_TOPS.items():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr(f"{top}/", b"")
            zf.writestr(f"{top}/README", b"fake vosk\n")
            zf.writestr(f"{top}/am/final.mdl", _rand(300_000 * scale, 10 + len(lang)))
            zf.writestr(f"{top}/conf/model.conf", b"--sample-frequency=16000\n")
            zf.writestr(f"{top}/graph/Gr.fst", _rand(50_000, 20 + len(lang)))
            zf.writestr(f"{top}/ivector/final.ie", _rand(20_000, 30 + len(lang)))
        arts[f"vosk-{lang}.zip"] = buf.getvalue()
    return arts


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class ModelServer:
    """with ModelServer() as s: s.url("ok", "silero_vad.onnx")"""

    def __init__(self, artifacts: dict = None, host: str = "127.0.0.1"):
        self.artifacts = artifacts or build_artifacts()
        self.requests = []  # (mode, artifact)
        self._stop = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = self.path.strip("/").split("/", 1)
                mode, name = (parts + [""])[:2]
                server.requests.append((mode, name))
                data = server.artifacts.get(name)
                if mode == "404" or data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mode == "redirect_http":
                    self.send_response(302)
                    self.send_header("Location", f"http://{self.headers['Host']}/ok/{name}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                if mode != "trunc_nocl":
                    self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    if mode in ("trunc_cl", "trunc_nocl"):
                        self.wfile.write(data[: len(data) // 2])
                        self.wfile.flush()
                        self.close_connection = True
                        self.connection.shutdown(2)
                    elif mode == "slow":
                        step = SLOW_BPS // 10
                        for i in range(0, len(data), step):
                            if server._stop.is_set():
                                return
                            self.wfile.write(data[i : i + step])
                            self.wfile.flush()
                            time.sleep(0.1)
                    elif mode == "stall":
                        self.wfile.write(data[:STALL_BYTES])
                        self.wfile.flush()
                        server._stop.wait()
                    else:
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer((host, 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def url(self, mode: str, artifact: str) -> str:
        return f"http://127.0.0.1:{self.port}/{mode}/{artifact}"

    def url_map(self, modes: dict = None) -> dict:
        """生成给 run_setup_models.py 的 QA_MODEL_URLS。modes: {"vad": "trunc_cl", "vosk-ja": "404", ...}"""
        modes = modes or {}
        return {
            "vad": self.url(modes.get("vad", "ok"), "silero_vad.onnx"),
            "sensevoice": self.url(modes.get("sensevoice", "ok"), "sensevoice.tar.bz2"),
            "vosk-zh": self.url(modes.get("vosk-zh", "ok"), "vosk-zh.zip"),
            "vosk-ja": self.url(modes.get("vosk-ja", "ok"), "vosk-ja.zip"),
            "vosk-en": self.url(modes.get("vosk-en", "ok"), "vosk-en.zip"),
        }

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port_url(artifact: str) -> str:
    """一个没有服务监听的地址（连接被拒绝，模拟断网）"""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/ok/{artifact}"
