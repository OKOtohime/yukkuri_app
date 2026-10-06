"""
QA 测试公共配置：标记注册、资源探测与按资源跳过。

资源缺失时用例应 skip 而不是 ERROR（对照 QR-MNT-01 的要求，测试方自身也遵守）。
"""

import os
import shutil
import sys

import pytest

QA_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(QA_DIR)
CORPORA_DIR = os.path.join(QA_DIR, "data", "corpora")
MANIFEST_DIR = os.path.join(QA_DIR, "data", "manifests")

MARKERS = {
    "l1": "L1 单元/组件测试",
    "l2": "L2 集成测试（真实模型或库）",
    "l3": "L3 系统测试（完整进程 + 虚拟音频设备）",
    "perf": "性能测试（QR-PERF）",
    "soak": "长时浸泡测试（耗时，默认不跑）",
    "manual": "需要人工参与",
    "linux_only": "仅 Linux",
    "windows_only": "仅 Windows",
    "requires_models": "需要 VAD 和 SenseVoice 模型",
    "requires_aquestalk": "需要 AquesTalk 声线库",
    "requires_pipewire": "需要 PipeWire 和 pactl",
    "requires_corpora": "需要本地评测语料",
}


def pytest_configure(config):
    for name, desc in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {desc}")


def _app_config():
    from yukkuri.config import AppConfig
    return AppConfig()


def has_models() -> bool:
    cfg = _app_config()
    return bool(cfg.find_vad_model() and cfg.find_sensevoice_dir())


def has_aquestalk() -> bool:
    return bool(_app_config().get_available_voices())


def has_pipewire() -> bool:
    return sys.platform.startswith("linux") and all(shutil.which(t) for t in ("pactl", "pw-play", "pw-record"))


def has_corpora() -> bool:
    return os.path.isdir(CORPORA_DIR) and any(
        os.path.isdir(os.path.join(CORPORA_DIR, d)) for d in os.listdir(CORPORA_DIR)
    )


_PROBES = {
    "requires_models": (has_models, "缺少 VAD/SenseVoice 模型（检查 YUKKURI_DATA_DIR）"),
    "requires_aquestalk": (has_aquestalk, "缺少 AquesTalk 声线库"),
    "requires_pipewire": (has_pipewire, "缺少 PipeWire 工具"),
    "requires_corpora": (has_corpora, "缺少评测语料（运行 qa/eval/asr/prepare_corpora.py）"),
    "linux_only": (lambda: sys.platform.startswith("linux"), "仅 Linux"),
    "windows_only": (lambda: sys.platform == "win32", "仅 Windows"),
}


def pytest_collection_modifyitems(config, items):
    cache = {}
    for item in items:
        for marker, (probe, reason) in _PROBES.items():
            if item.get_closest_marker(marker) is None:
                continue
            if marker not in cache:
                cache[marker] = probe()
            if not cache[marker]:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture
def app_config():
    """按当前 YUKKURI_DATA_DIR 构造的 AppConfig"""
    return _app_config()


@pytest.fixture(scope="session")
def corpora_dir():
    return CORPORA_DIR


@pytest.fixture(scope="session")
def manifest_dir():
    return MANIFEST_DIR
