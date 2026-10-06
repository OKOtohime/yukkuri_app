"""
测试包装：把 model_downloader 的下载地址改到本地模型服务后执行 setup_models.main()。

只改本进程内存中的常量，不改产品文件。地址来自环境变量 QA_MODEL_URLS（JSON：vad、sensevoice、vosk-zh/ja/en）。
其余命令行参数原样交给 setup_models.py。
"""

import json
import os
import runpy
import sys

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("QA_SUT_REPO") or os.path.dirname(QA_DIR)

urls = json.loads(os.environ.get("QA_MODEL_URLS", "{}"))
import yukkuri.model_downloader as md  # noqa: E402

if "vad" in urls:
    md.VAD_URL = urls["vad"]
if "sensevoice" in urls:
    md.SENSEVOICE_URL = urls["sensevoice"]
for lang in ("zh", "ja", "en"):
    if f"vosk-{lang}" in urls:
        md.VOSK_URLS[lang] = urls[f"vosk-{lang}"]

sys.argv = ["setup_models.py", *sys.argv[1:]]
runpy.run_path(os.path.join(REPO, "setup_models.py"), run_name="__main__")
