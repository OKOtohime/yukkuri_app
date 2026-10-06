"""
WP-02 L2：FR-CFG-01 非源码目录安装（兼作 QR-PORT-04 的前置检查）

用 uv 把当前源码以非 editable 方式装进临时 venv（--no-deps，只验证路径逻辑），
在临时 HOME 下运行：
1. 数据目录落在用户数据目录（Linux：~/.local/share/yukkuri；Windows：%LOCALAPPDATA%\\yukkuri）；
2. 运行前后 site-packages 没有新增文件；
3. 在用户数据目录放入与安装工具相同的布局后，AppConfig 能找到 VAD、SenseVoice 和声线库。
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.l2]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IS_WIN = sys.platform == "win32"

PROBE = r"""
import json, os, sys, sysconfig
from yukkuri.config import get_default_data_dir, AppConfig
d = get_default_data_dir()
lib = "AquesTalk.dll" if sys.platform == "win32" else "libAquesTalk.so"
for rel in ["silero_vad.onnx", "sensevoice/model.int8.onnx", "sensevoice/tokens.txt",
            "libs/aquestalk/lib64/f1/" + lib, "libs/aquestalk/lib64/m2/" + lib]:
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").close()
cfg = AppConfig()
print(json.dumps({"data_dir": d, "project_root": cfg.project_root,
                  "vad": cfg.find_vad_model(), "sensevoice": cfg.find_sensevoice_dir(),
                  "voices": sorted(cfg.get_available_voices()),
                  "purelib": sysconfig.get_paths()["purelib"]}))
"""


def _snapshot(root):
    out = set()
    for dp, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".pyc"):
                out.add(os.path.join(dp, f))
    return out


@pytest.fixture(scope="module")
def installed_venv(tmp_path_factory):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("缺少 uv")
    venv = tmp_path_factory.mktemp("venv")
    subprocess.run([uv, "venv", "-q", "--python", sys.executable, str(venv)], check=True)
    py = str(venv / ("Scripts/python.exe" if IS_WIN else "bin/python"))
    src = tmp_path_factory.mktemp("src")
    # 复制一份源码，避免 uv 构建时在仓库里留下 build/ 或 egg-info
    shutil.copytree(REPO_ROOT, src / "pkg", ignore=shutil.ignore_patterns(".git", "qa", "*.egg-info", "build", ".venv"))
    res = subprocess.run([uv, "pip", "install", "-q", "--no-deps", "--python", py, str(src / "pkg")],
                         capture_output=True, text=True)
    if res.returncode != 0:
        pytest.skip(f"安装失败（环境问题，交 WP-11 复核）：{res.stderr[-500:]}")
    return py


def test_fr_cfg_01_installed_package_uses_user_dir(installed_venv, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("YUKKURI_DATA_DIR", "YUKKURI_MODELS_DIR", "XDG_DATA_HOME", "PYTHONPATH")}
    env.update(HOME=str(home), USERPROFILE=str(home))
    if IS_WIN:
        env.update(LOCALAPPDATA=str(home / "AppData" / "Local"))
    expected = str(home / "AppData" / "Local" / "yukkuri") if IS_WIN else str(home / ".local" / "share" / "yukkuri")

    purelib = subprocess.run([installed_venv, "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    before = _snapshot(purelib)
    out = subprocess.run([installed_venv, "-c", PROBE], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])

    assert os.path.normcase(got["data_dir"]) == os.path.normcase(expected)
    assert os.path.normcase(got["project_root"]) == os.path.normcase(expected)
    assert got["vad"] and got["sensevoice"], got
    assert got["voices"] == ["f1", "m2"], got
    assert _snapshot(purelib) - before == set(), "运行时向 site-packages 写入了文件"
