"""
WP-04 L1：FR-MDL-09 —— setup_models.sh 在 Linux 上提供与 setup_models.py 等价的能力。

setup_models.sh 在脚本所在目录工作（cd "$SCRIPT_DIR"），所以把脚本复制到沙箱目录执行，该目录就是它的数据目录；
YUKKURI_DATA_DIR 也指向同一目录，使脚本中调用的 Python 部分（AquesTalk 导入）与之一致。
下载用 PATH 中的 curl 替身（qa/harness/curl_shim.py）转到本地模型服务。

"等价"的判定：同一请求下，所请求的每一项在 --check 与程序加载侧都就绪、内容与发布物一致
（不要求目录布局逐项相同：Vosk 中文 .sh 装到 model_cn、.py 装到 model_zh，后者是 DEF-201）。
"""

import os
import shutil
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wp04_util import (  # noqa: E402
    QA_DIR, REPO, installed_hashes, isolated_env, loader_view, model_status, official_package, package_lib64_hashes,
)
from model_server import ModelServer, build_artifacts  # noqa: E402
from test_wp04_mdl import STATUS_KEY, content_ok  # noqa: E402

pytestmark = [pytest.mark.l1, pytest.mark.linux_only]


@pytest.fixture(scope="module")
def server():
    with ModelServer(build_artifacts()) as s:
        yield s


@pytest.fixture
def sh_env(tmp_path, server):
    """返回 run(args, cwd=None, data_dir=None) → (CompletedProcess, 数据目录)"""
    import json
    script_dir = tmp_path / "app"
    script_dir.mkdir()
    shutil.copy(os.path.join(REPO, "setup_models.sh"), script_dir / "setup_models.sh")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    curl = shim_dir / "curl"
    curl.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{os.path.join(QA_DIR, 'harness', 'curl_shim.py')}' \"$@\"\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    venv_bin = os.path.dirname(sys.executable)
    home = str(tmp_path / "home")

    def run(args, cwd=None, data_dir=None):
        data = str(data_dir or script_dir)
        env = isolated_env(home, data, QA_MODEL_URLS=json.dumps(server.url_map()), QA_CURL_LOG=str(tmp_path / "curl.log"))
        env["PATH"] = os.pathsep.join([str(shim_dir), venv_bin, "/usr/bin", "/bin"])
        r = subprocess.run(["bash", str(script_dir / "setup_models.sh"), *args], cwd=str(cwd or tmp_path), env=env,
                           capture_output=True, text=True, timeout=180)
        return r, str(script_dir)

    run.home = home
    run.script_dir = str(script_dir)
    return run


def ready(data, item, arts):
    return bool(model_status(data)[STATUS_KEY[item]] and loader_view(data)[STATUS_KEY[item]] and content_ok(data, item, arts))


SH_CASES = [
    ([], ["vad", "sensevoice"]),
    (["--vad"], ["vad"]),
    (["--sensevoice"], ["sensevoice"]),
    (["--vosk"], ["vosk-zh"]),
    (["--vosk", "zh"], ["vosk-zh"]),
    (["--vosk", "all"], ["vosk-zh", "vosk-ja", "vosk-en"]),
    (["--all"], ["vad", "sensevoice", "vosk-zh", "vosk-ja", "vosk-en"]),
]


@pytest.mark.parametrize("args,items", SH_CASES, ids=[" ".join(a) or "(无参数)" for a, _ in SH_CASES])
def test_fr_mdl_09_sh_downloads_equivalent(sh_env, server, args, items):
    r, data = sh_env(args)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    not_ready = [i for i in items if not ready(data, i, server.artifacts)]
    assert not_ready == [], not_ready


def test_fr_mdl_09_sh_skips_existing(sh_env, server):
    sh_env(["--vad", "--sensevoice"])
    log = os.path.join(os.path.dirname(sh_env.script_dir), "curl.log")
    os.remove(log)
    r, _ = sh_env(["--vad", "--sensevoice"])
    assert r.returncode == 0 and not os.path.exists(log)


def _extract_pkg(pkg, dest):
    import zipfile
    with zipfile.ZipFile(pkg) as zf:
        zf.extractall(dest)
        return os.path.join(dest, zf.namelist()[0].split("/")[0])


@pytest.mark.parametrize("form", ["zip", "dir"])
def test_fr_mdl_09_sh_aquestalk_import(sh_env, tmp_path, form):
    pkg = official_package()
    src = pkg if form == "zip" else _extract_pkg(pkg, str(tmp_path / "x"))
    r, data = sh_env(["--aquestalk", src])
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    assert installed_hashes(data) == package_lib64_hashes(pkg)


def test_fr_mdl_09_sh_aquestalk_autodetect(sh_env):
    """与 setup_models.py 相同的位置（~/Downloads/官方包名）"""
    pkg = official_package()
    dl = os.path.join(sh_env.home, "Downloads")
    os.makedirs(dl, exist_ok=True)
    shutil.copy(pkg, os.path.join(dl, os.path.basename(pkg)))
    r, data = sh_env(["--aquestalk"])
    assert r.returncode == 0
    assert installed_hashes(data) == package_lib64_hashes(pkg)


SPECIAL = {
    "space": "my dir/aqtk1_lnx_200.zip",
    "chinese": "下载 文件/aqtk1_lnx_200.zip",
    "single_quote": "it's/aqtk1_lnx_200.zip",
}


@pytest.mark.parametrize("kind", list(SPECIAL))
def test_fr_mdl_09_sh_paths_with_special_chars(sh_env, tmp_path, request, kind):
    """setup_models.py 能导入的路径，setup_models.sh 也应能导入"""
    if kind == "single_quote":
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-412"))
    pkg = official_package()
    target = tmp_path / "in" / SPECIAL[kind]
    target.parent.mkdir(parents=True)
    shutil.copy(pkg, target)
    r, data = sh_env(["--aquestalk", str(target)])
    assert installed_hashes(data) == package_lib64_hashes(pkg), (r.stdout + r.stderr)[-500:]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-412")
def test_fr_mdl_09_sh_relative_path_from_other_cwd(sh_env, tmp_path):
    """在下载目录里执行 <项目>/setup_models.sh --aquestalk aqtk1_lnx_200.zip（相对路径）；setup_models.py 支持这种用法"""
    pkg = official_package()
    cwd = tmp_path / "Downloads"
    cwd.mkdir()
    shutil.copy(pkg, cwd / os.path.basename(pkg))
    r, data = sh_env(["--aquestalk", os.path.basename(pkg)], cwd=cwd)
    assert installed_hashes(data) == package_lib64_hashes(pkg), (r.stdout + r.stderr)[-500:]


def test_obs_403_sh_ignores_data_dir_for_downloads(sh_env, tmp_path, server):
    """记录：设置了 YUKKURI_DATA_DIR 时，.sh 的下载落在脚本目录，导入落在 YUKKURI_DATA_DIR（OBS-403）"""
    other = tmp_path / "other_data"
    other.mkdir()
    pkg = official_package()
    r, script_dir = sh_env(["--vad", "--aquestalk", pkg], data_dir=other)
    assert os.path.exists(os.path.join(script_dir, "silero_vad.onnx"))
    assert not os.path.exists(other / "silero_vad.onnx")
    assert installed_hashes(str(other)) == package_lib64_hashes(pkg)
