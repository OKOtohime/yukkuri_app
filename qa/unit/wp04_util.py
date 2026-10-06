"""
WP-04 公共工具：AquesTalk 桩库、隔离的子进程环境、setup_models 调用、真实安装包定位、文件快照。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(QA_DIR)
STUB_BUILD = os.path.join(QA_DIR, "stubs", "build")
REPORT_DIR = os.path.join(QA_DIR, "reports", "wp04")
IS_WIN = sys.platform == "win32"
LIBNAME = "AquesTalk.dll" if IS_WIN else "libAquesTalk.so"
VOICES = ["f1", "f2", "f3", "m1", "m2", "imd1", "jgr", "dvd", "r1"]

if os.path.join(QA_DIR, "harness") not in sys.path:
    sys.path.insert(0, os.path.join(QA_DIR, "harness"))


def ensure_report_dir() -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    return REPORT_DIR


# ---------------------------------------------------------------- 桩库

def stub_lib(variant: str = "default") -> str:
    """桩库路径；Linux 上缺失时现场用 gcc 编译，Windows 需要预先交叉编译好的 DLL"""
    plat = "win" if IS_WIN else "linux"
    path = os.path.join(STUB_BUILD, plat, variant, LIBNAME)
    if not os.path.exists(path) and not IS_WIN and shutil.which("gcc"):
        subprocess.run([sys.executable, os.path.join(QA_DIR, "stubs", "build.py")], check=True, capture_output=True)
    if not os.path.exists(path):
        pytest.skip(f"缺少 AquesTalk 桩库 {path}（运行 qa/stubs/build.py）")
    return path


def make_stub_data(root: str, voices=VOICES, variant: str = "default") -> str:
    """在数据目录 root 下按官方导入布局放桩库：libs/aquestalk/lib64/<voice>/<LIBNAME>。
    逐个复制（不同 inode），保证 dlopen 把每个声线当作独立的库加载。"""
    src = stub_lib(variant)
    for v in voices:
        d = os.path.join(root, "libs", "aquestalk", "lib64", v)
        os.makedirs(d, exist_ok=True)
        shutil.copy(src, os.path.join(d, LIBNAME))
    return root


def read_log(path) -> list:
    """桩库调用记录 → [(op, voice, *args)]"""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        return [tuple(line.decode("utf-8", "replace").rstrip("\r\n").split(" ")) for line in f if line.strip()]


def wav_tag(wav: bytes) -> dict:
    """解析桩库写在数据区开头的 VOICE/SPEED/IF 标记"""
    raw = wav[44:44 + 400].split(b"\0", 1)[0].decode("ascii", "replace")
    return dict(kv.split("=", 1) for kv in raw.split(";") if "=" in kv)


# ---------------------------------------------------------------- 隔离环境

_STRIP_PREFIXES = ("AQUESTALK_", "YUKKURI_", "QA_STUB_LOG")
_PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def isolated_env(home: str, data: str = None, **extra) -> dict:
    """子进程环境：去掉密钥、数据目录和代理变量，HOME 指向沙箱，让 ~ 下的候选路径都落在沙箱里"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES) and k not in _PROXY_VARS}
    os.makedirs(home, exist_ok=True)
    env.update(HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8", NO_PROXY="*", no_proxy="*")
    if IS_WIN:
        env["LOCALAPPDATA"] = os.path.join(home, "AppData", "Local")
    if data:
        env["YUKKURI_DATA_DIR"] = data
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def isolate_home(monkeypatch, tmp_path) -> str:
    """进程内用例：把 HOME 指到沙箱，并去掉密钥与数据目录变量"""
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    for k in list(os.environ):
        if k.startswith(("AQUESTALK_", "YUKKURI_")):
            monkeypatch.delenv(k, raising=False)
    return home


def run_cli(args, env, cwd, timeout=120) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "yukkuri.cli", *args], env=env, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def run_setup(args, env, urls: dict = None, cwd=None, timeout=180) -> subprocess.CompletedProcess:
    """经 qa/harness/run_setup_models.py 执行 setup_models.py；urls 为 None 时不改下载地址"""
    env = dict(env)
    if urls is not None:
        env["QA_MODEL_URLS"] = json.dumps(urls)
    return subprocess.run([sys.executable, os.path.join(QA_DIR, "harness", "run_setup_models.py"), *args],
                          env=env, cwd=cwd or REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def model_status(data: str) -> dict:
    from yukkuri.model_downloader import check_model_status
    return check_model_status(data)


def loader_view(data: str) -> dict:
    """程序加载侧的结论（与 --check 分开看）。
    加载侧还会搜索 ~/.local/share/yukkuri、~/.cache/yukkuri 等位置，判定时把 HOME 指向空目录，只看 data"""
    import tempfile
    from yukkuri.config import AppConfig
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    with tempfile.TemporaryDirectory() as empty_home:
        os.environ["HOME"] = os.environ["USERPROFILE"] = empty_home
        try:
            out = {}
            cfg = AppConfig(project_root=data)
            out["vad"] = cfg.find_vad_model()
            out["sensevoice"] = cfg.find_sensevoice_dir()
            for lang in ("zh", "ja", "en"):
                out[f"vosk_{lang}"] = AppConfig(project_root=data, lang=lang).find_vosk_model_dir()
            out["voices"] = sorted(cfg.get_available_voices())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return out


# ---------------------------------------------------------------- 真实安装包

def official_package() -> str:
    """本平台的 AquesTalk 评价版官方包（不入库）"""
    name = "aqtk1_win_200.zip" if IS_WIN else "aqtk1_lnx_200.zip"
    candidates = [
        os.environ.get("QA_AQTK_ZIP", ""),
        os.path.join(QA_DIR, name),
        os.path.join(os.path.dirname(REPO), name),               # W1: C:\yk\qa\aqtk1_win_200.zip
        os.path.expanduser(f"~/Projects/yukkuri_app/qa/{name}"),  # R1 主检出（worktree 中没有被忽略的 zip）
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    pytest.skip(f"缺少 AquesTalk 官方包 {name}（设置 QA_AQTK_ZIP）")


def package_lib64_hashes(zip_path: str) -> dict:
    """官方包中 lib64/<voice>/<库> 的 sha256 —— 导入后每种声线的库应与之逐字节相同"""
    import zipfile
    out = {}
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            parts = n.split("/")
            if len(parts) >= 3 and parts[-3] == "lib64" and parts[-2] in VOICES and parts[-1] == LIBNAME:
                out[parts[-2]] = hashlib.sha256(zf.read(n)).hexdigest()
    return out


def installed_hashes(data: str) -> dict:
    out = {}
    for v in VOICES:
        p = os.path.join(data, "libs", "aquestalk", "lib64", v, LIBNAME)
        if os.path.exists(p):
            with open(p, "rb") as f:
                out[v] = hashlib.sha256(f.read()).hexdigest()
    return out


# ---------------------------------------------------------------- 文件快照

def snapshot(root: str) -> dict:
    """{相对路径: (类型, 大小, mtime_ns)}，不跟随符号链接"""
    out = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        for n in dns + fns:
            p = os.path.join(dp, n)
            st = os.lstat(p)
            kind = "link" if os.path.islink(p) else ("dir" if os.path.isdir(p) else "file")
            out[os.path.relpath(p, root)] = (kind, st.st_size if kind == "file" else 0, st.st_mtime_ns)
    return out


def changed_paths(before: dict, after: dict) -> list:
    return sorted(p for p, v in after.items() if before.get(p) != v and v[0] != "dir")
