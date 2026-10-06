#!/usr/bin/env python3
"""
setup_models.sh 用的 curl 替身（WP-04 FR-MDL-09）：把真实下载地址按文件名映射到本地模型服务。

只支持 setup_models.sh 用到的形式：curl -SL -# -o <file> <url>
映射来自环境变量 QA_MODEL_URLS（与 run_setup_models.py 相同的 JSON）。调用记录追加到 QA_CURL_LOG。
"""

import json
import os
import sys
import urllib.request

KEYS = {
    "silero_vad.onnx": "vad",
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2": "sensevoice",
    "vosk-model-small-cn-0.22.zip": "vosk-zh",
    "vosk-model-small-ja-0.22.zip": "vosk-ja",
    "vosk-model-small-en-us-0.15.zip": "vosk-en",
}


def main(argv):
    out, url = None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-o":
            out = argv[i + 1]
            i += 2
            continue
        if not a.startswith("-"):
            url = a
        i += 1
    log = os.environ.get("QA_CURL_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{url} -> {out}\n")
    urls = json.loads(os.environ.get("QA_MODEL_URLS", "{}"))
    key = KEYS.get(url.rsplit("/", 1)[-1]) if url else None
    if not key or key not in urls or not out:
        sys.stderr.write(f"curl_shim: unmapped url {url}\n")
        return 22
    try:
        with urllib.request.urlopen(urls[key], timeout=30) as r, open(out, "wb") as f:
            f.write(r.read())
    except Exception as e:
        sys.stderr.write(f"curl: {e}\n")
        return 22
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
