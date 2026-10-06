"""
WP-02 L1：run.sh 加载 .env（FR-CFG-04，仅 Linux）

做法：把 run.sh 复制到临时目录（SCRIPT_DIR），旁边放 .env；
PYTHON_CMD 指向桩脚本，桩脚本打印它看到的环境变量后退出。
"""

import os
import shutil
import stat
import subprocess

import pytest

pytestmark = [pytest.mark.l1, pytest.mark.linux_only]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def run_sh(tmp_path):
    shutil.copy(os.path.join(REPO_ROOT, "run.sh"), tmp_path / "run.sh")
    stub = tmp_path / "stub_python"
    stub.write_text('#!/bin/sh\necho "DEV=${AQUESTALK_DEV_KEY:-<unset>}"\necho "ARGS=$*"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def run(env_file: str, extra_env=None):
        (tmp_path / ".env").write_text(env_file)
        env = {k: v for k, v in os.environ.items() if not k.startswith("AQUESTALK")}
        env["PYTHON_CMD"] = str(stub)
        env.update(extra_env or {})
        out = subprocess.run(["bash", str(tmp_path / "run.sh"), "--voice", "f2"], cwd=tmp_path,
                             env=env, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)

    return run


def test_fr_cfg_04_run_sh_loads_env(run_sh):
    got = run_sh("# key\nAQUESTALK_DEV_KEY=from_env_file\n")
    assert got["DEV"] == "from_env_file"
    assert got["ARGS"] == "-m yukkuri.cli --voice f2"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-206")
def test_fr_cfg_04_run_sh_env_precedence(run_sh):
    """FR-TTS-05：系统环境变量 > .env。经 run.sh 启动时也必须成立"""
    got = run_sh("AQUESTALK_DEV_KEY=from_env_file\n", {"AQUESTALK_DEV_KEY": "from_os_env"})
    assert got["DEV"] == "from_os_env"
