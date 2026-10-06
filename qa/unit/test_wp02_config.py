"""
WP-02 L1：配置与路径（FR-CFG-01~03）

判定依据：docs/requirements.md 4.8 节；附录 A 的 IMPL-10/11/19/20 只作为线索。
所有用例都把 HOME、仓库根和数据目录隔离到 tmp_path，不受本机已安装资源的影响。
"""

import io
import os
import subprocess
import sys
import tarfile
import zipfile

import pytest

import yukkuri.config as config_mod
import yukkuri.model_downloader as md
from yukkuri.config import AQUESTALK_VOICES, AppConfig

pytestmark = pytest.mark.l1

IS_WIN = sys.platform == "win32"
LIB_NAME = "AquesTalk.dll" if IS_WIN else "libAquesTalk.so"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def env_snapshot():
    """_load_env_file 直接写 os.environ，用快照整体恢复"""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def sandbox(tmp_path, monkeypatch, env_snapshot):
    """隔离 HOME、仓库根（config.__file__）和数据目录"""
    home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    data = tmp_path / "data"
    for d in (home, fake_repo / "yukkuri", data):
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(config_mod, "__file__", str(fake_repo / "yukkuri" / "config.py"))
    for k in ("YUKKURI_DATA_DIR", "YUKKURI_MODELS_DIR", "XDG_DATA_HOME", "AQUESTALK_DEV_KEY", "AQUESTALK_USR_KEY"):
        monkeypatch.delenv(k, raising=False)

    class S:
        pass

    s = S()
    s.home, s.repo, s.data, s.tmp = str(home), str(fake_repo), str(data), tmp_path
    return s


def _touch(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


# ---------------------------------------------------------------- FR-CFG-01

# (编号, 环境变量, 仓库有 pyproject, 仓库可写, 平台, 平台环境变量, 期望目录(相对 sandbox 的模板))
DATA_DIR_RULES = [
    ("R1", {"YUKKURI_DATA_DIR": "{tmp}/explicit"}, True, True, "linux", {}, "{tmp}/explicit"),
    ("R2", {"YUKKURI_MODELS_DIR": "{tmp}/legacy"}, False, True, "linux", {}, "{tmp}/legacy"),
    ("R3", {"YUKKURI_DATA_DIR": "{tmp}/a", "YUKKURI_MODELS_DIR": "{tmp}/b"}, False, True, "linux", {}, "{tmp}/a"),
    ("R4", {}, True, True, "linux", {}, "{repo}"),
    ("R5", {}, True, False, "linux", {}, "{home}/.local/share/yukkuri"),
    ("R6", {}, False, True, "linux", {}, "{home}/.local/share/yukkuri"),
    ("R7", {}, False, True, "linux", {"XDG_DATA_HOME": "{tmp}/xdg"}, "{tmp}/xdg/yukkuri"),
    ("R8", {}, False, True, "win32", {"LOCALAPPDATA": "{tmp}/la", "APPDATA": "{tmp}/ra"}, "{tmp}/la/yukkuri"),
    ("R9", {}, False, True, "win32", {"APPDATA": "{tmp}/ra"}, "{tmp}/ra/yukkuri"),
    ("R10", {}, False, True, "win32", {}, "{home}/yukkuri"),
]


@pytest.mark.parametrize("rule", DATA_DIR_RULES, ids=[r[0] for r in DATA_DIR_RULES])
def test_fr_cfg_01_data_dir_decision_table(sandbox, monkeypatch, rule):
    _, env, has_pyproject, writable, platform, plat_env, expected = rule
    fmt = dict(tmp=str(sandbox.tmp), repo=sandbox.repo, home=sandbox.home)
    for k in ("LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**env, **plat_env}.items():
        monkeypatch.setenv(k, v.format(**fmt))
    if has_pyproject:
        _touch(os.path.join(sandbox.repo, "pyproject.toml"))
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda p, m: (writable if os.path.abspath(p) == sandbox.repo else real_access(p, m)))
    monkeypatch.setattr(sys, "platform", platform)

    got = config_mod.get_default_data_dir()

    assert os.path.normpath(got) == os.path.normpath(os.path.abspath(expected.format(**fmt)))
    # 非源码安装时绝不能落在包所在目录
    if not (has_pyproject and writable) and not env:
        assert not os.path.normpath(got).startswith(os.path.normpath(sandbox.repo))


def test_fr_cfg_01_data_dir_is_created(sandbox, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    target = os.path.join(sandbox.home, ".local", "share", "yukkuri")
    assert not os.path.exists(target)
    assert os.path.normpath(config_mod.get_default_data_dir()) == target
    assert os.path.isdir(target)


def test_fr_cfg_01_data_dir_uncreatable_does_not_raise(sandbox, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    blocker = os.path.join(sandbox.tmp, "blocker")
    _touch(blocker)  # 普通文件挡住目录创建
    monkeypatch.setenv("XDG_DATA_HOME", blocker)
    assert config_mod.get_default_data_dir() == os.path.join(blocker, "yukkuri")


# ---------------------------------------------------------------- FR-CFG-02

def _write_env(dirpath, content: bytes):
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, ".env"), "wb") as f:
        f.write(content)


@pytest.mark.parametrize("locations, expected", [
    (["cwd"], "from_cwd"),
    (["repo"], "from_repo"),
    (["data"], "from_data"),
    (["cwd", "repo", "data"], "from_cwd"),
    (["repo", "data"], "from_repo"),
])
def test_fr_cfg_02_env_file_search_order(sandbox, monkeypatch, locations, expected):
    cwd = os.path.join(sandbox.tmp, "cwd")
    os.makedirs(cwd)
    dirs = {"cwd": cwd, "repo": sandbox.repo, "data": sandbox.data}
    for loc in locations:
        _write_env(dirs[loc], f"AQUESTALK_DEV_KEY=from_{loc}\n".encode())
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(config_mod, "get_default_data_dir", lambda: sandbox.data)

    config_mod._load_env_file()

    assert AppConfig().dev_key == expected


def test_fr_cfg_02_env_does_not_override_os_env(sandbox, monkeypatch):
    monkeypatch.chdir(sandbox.repo)
    _write_env(sandbox.repo, b"AQUESTALK_DEV_KEY=from_file\nAQUESTALK_USR_KEY=usr_from_file\n")
    monkeypatch.setenv("AQUESTALK_DEV_KEY", "from_os")
    config_mod._load_env_file()
    cfg = AppConfig()
    assert cfg.dev_key == "from_os"
    assert cfg.usr_key == "usr_from_file"


ENV_FORMATS = [
    # (id, 文件内容, 期望值, 缺陷编号或 None)
    ("readme_example", b"# comment\nAQUESTALK_DEV_KEY=your_dev_key_here\n\nAQUESTALK_USR_KEY=u\n", "your_dev_key_here", None),
    ("double_quotes", b'AQUESTALK_DEV_KEY="k1"\n', "k1", None),
    ("single_quotes", b"AQUESTALK_DEV_KEY='k1'\n", "k1", None),
    ("spaces_around_eq", b"AQUESTALK_DEV_KEY = k1 \n", "k1", None),
    ("value_contains_eq", b"AQUESTALK_DEV_KEY=a=b==\n", "a=b==", None),
    ("crlf", b"AQUESTALK_DEV_KEY=k1\r\n", "k1", None),
    ("utf8_bom", b"\xef\xbb\xbfAQUESTALK_DEV_KEY=k1\r\n", "k1", "DEF-204"),
    ("utf8_bom_key_not_first", b"\xef\xbb\xbf# keys\r\nAQUESTALK_DEV_KEY=k1\r\n", "k1", None),
]


@pytest.mark.parametrize("case", ENV_FORMATS, ids=[c[0] for c in ENV_FORMATS])
def test_fr_cfg_02_env_parsing(sandbox, monkeypatch, request, case):
    _, content, expected, defect = case
    if defect:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason=defect))
    monkeypatch.chdir(sandbox.repo)
    _write_env(sandbox.repo, content)
    config_mod._load_env_file()
    assert AppConfig().dev_key == expected


@pytest.mark.parametrize("module", ["yukkuri.cli", "yukkuri.gui.app"])
def test_fr_cfg_02_env_reaches_cli_and_gui(tmp_path, module):
    """真实子进程：在工作目录放 .env，导入入口模块后构造 AppConfig"""
    if module == "yukkuri.gui.app":
        pytest.importorskip("customtkinter")
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / ".env").write_text("AQUESTALK_DEV_KEY=cli_gui_key\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AQUESTALK")}
    env.update(PYTHONPATH=REPO_ROOT, YUKKURI_DATA_DIR=str(tmp_path / "data"))
    code = f"import {module}; from yukkuri.config import AppConfig; print(AppConfig().dev_key)"
    out = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "cli_gui_key"


# ---------------------------------------------------------------- FR-CFG-03

def _fake_download_factory(platform_lib=LIB_NAME):
    """按 URL 生成与官方包同构的最小归档，替代 urlretrieve"""

    def fake_download(url, target_path, progress_callback=None):
        if url == md.VAD_URL:
            _touch(target_path, b"onnx")
        elif url == md.SENSEVOICE_URL:
            top = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
            with tarfile.open(target_path, "w:bz2") as tf:
                for name, data in ((f"{top}/model.int8.onnx", b"int8"), (f"{top}/tokens.txt", b"t"),
                                   (f"{top}/model.onnx", b"fp32")):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
        elif url in md.VOSK_URLS.values():
            top = os.path.splitext(os.path.basename(url))[0]
            with zipfile.ZipFile(target_path, "w") as zf:
                zf.writestr(f"{top}/am/final.mdl", b"m")
                zf.writestr(f"{top}/conf/model.conf", b"c")
        else:
            raise AssertionError(f"unexpected url {url}")
        if progress_callback:
            progress_callback(1, 1)

    return fake_download


def _install(kind, root, tmp):
    if kind == "vad":
        md.download_vad(root=root)
    elif kind == "sensevoice":
        md.download_sensevoice(root=root)
    elif kind.startswith("vosk_"):
        md.download_vosk(lang=kind.split("_")[1], root=root)
    elif kind == "aquestalk_zip":
        z = os.path.join(tmp, "aqtk.zip")
        with zipfile.ZipFile(z, "w") as zf:
            for v in AQUESTALK_VOICES:
                zf.writestr(f"aqtk1/lib64/{v}/{LIB_NAME}", v.encode())
        md.install_aquestalk_from_archive(z, root=root)
    elif kind == "aquestalk_dir":
        src = os.path.join(tmp, "aqtk_dir", "aqtk1", "lib64")
        for v in AQUESTALK_VOICES:
            _touch(os.path.join(src, v, LIB_NAME), v.encode())
        md.install_aquestalk_from_dir(os.path.join(tmp, "aqtk_dir"), root=root)
    elif kind.startswith("sh_vosk_"):
        # setup_models.sh 的目录约定：zh→model_cn，ja→model_ja，en→model_en（在仓库根目录）
        lang = kind.split("_")[2]
        d = {"zh": "model_cn", "ja": "model_ja", "en": "model_en"}[lang]
        _touch(os.path.join(root, d, "am", "final.mdl"))
    else:
        raise AssertionError(kind)


def _loader_finds(kind, root):
    if kind == "vad":
        return AppConfig(project_root=root).find_vad_model() is not None
    if kind == "sensevoice":
        return AppConfig(project_root=root).find_sensevoice_dir() is not None
    if "vosk_" in kind:
        lang = kind.split("_")[-1]
        return AppConfig(project_root=root, lang=lang).find_vosk_model_dir() is not None
    if kind.startswith("aquestalk"):
        return set(AppConfig(project_root=root).get_available_voices()) == set(AQUESTALK_VOICES)
    raise AssertionError(kind)


def _check_says_ready(kind, root):
    st = md.check_model_status(root)
    if kind in ("vad", "sensevoice"):
        return st[kind]
    if "vosk_" in kind:
        return st["vosk_" + kind.split("_")[-1]]
    if kind.startswith("aquestalk"):
        return st["aquestalk_count"] == len(AQUESTALK_VOICES)
    raise AssertionError(kind)


INSTALLERS = [
    ("vad", None), ("sensevoice", None),
    ("vosk_zh", "DEF-201"), ("vosk_ja", None), ("vosk_en", None),
    ("aquestalk_zip", None), ("aquestalk_dir", None),
    ("sh_vosk_zh", None), ("sh_vosk_ja", None), ("sh_vosk_en", None),
]


@pytest.mark.parametrize("kind, defect", INSTALLERS, ids=[k for k, _ in INSTALLERS])
def test_fr_cfg_03_installer_layout_is_loadable(sandbox, monkeypatch, request, kind, defect):
    if defect:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason=defect))
    monkeypatch.setattr(md, "download_file", _fake_download_factory())
    _install(kind, sandbox.data, str(sandbox.tmp))
    assert _loader_finds(kind, sandbox.data), f"{kind}: 安装后加载侧找不到"


@pytest.mark.parametrize("kind, defect", INSTALLERS, ids=[k for k, _ in INSTALLERS])
def test_fr_cfg_03_check_agrees_with_loader(sandbox, monkeypatch, request, kind, defect):
    """--check（check_model_status）与加载侧的结论必须一致"""
    monkeypatch.setattr(md, "download_file", _fake_download_factory())
    _install(kind, sandbox.data, str(sandbox.tmp))
    assert _check_says_ready(kind, sandbox.data) == _loader_finds(kind, sandbox.data)


def test_fr_cfg_03_sensevoice_int8_fallback(sandbox):
    d = os.path.join(sandbox.data, "sensevoice")
    _touch(os.path.join(d, "model.onnx"))
    _touch(os.path.join(d, "tokens.txt"))
    assert AppConfig(project_root=sandbox.data).find_sensevoice_dir() == os.path.abspath(d)


def test_fr_cfg_03_custom_model_path_wins(sandbox):
    custom = os.path.join(sandbox.tmp, "custom")
    os.makedirs(custom)
    _touch(os.path.join(sandbox.data, "sensevoice", "model.int8.onnx"))
    _touch(os.path.join(sandbox.data, "sensevoice", "tokens.txt"))
    cfg = AppConfig(project_root=sandbox.data, custom_model_path=custom)
    assert cfg.find_sensevoice_dir() == os.path.abspath(custom)


def test_fr_cfg_03_voice_libraries_not_aliased_dir_layout(sandbox):
    """只导入 f1、m1 两种声线（标准目录布局）时，只报告这两种"""
    lib64 = os.path.join(sandbox.data, "libs", "aquestalk", "lib64")
    for v in ("f1", "m1"):
        _touch(os.path.join(lib64, v, LIB_NAME))
    assert set(AppConfig(project_root=sandbox.data).get_available_voices()) == {"f1", "m1"}


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-205")
def test_fr_cfg_03_voice_libraries_not_aliased_single_file_import(sandbox):
    """FR-MDL-05 允许导入单个 .so/.dll。导入后只应有一种声线可用，
    其余声线不能因为共用目录而被判为"已安装"（FR-TTS-02/03 的前提）"""
    single = os.path.join(sandbox.tmp, LIB_NAME)
    _touch(single, b"one")
    md.install_aquestalk_from_archive(single, root=sandbox.data)
    voices = AppConfig(project_root=sandbox.data).get_available_voices()
    assert len(voices) == 1, f"导入 1 个库文件后报告 {len(voices)} 种声线: {sorted(voices)}"


def test_fr_cfg_03_library_suffix_matches_platform(sandbox):
    _touch(os.path.join(sandbox.data, "libs", "aquestalk", "lib64", "f2", LIB_NAME))
    p = AppConfig(project_root=sandbox.data).find_aquestalk_library("f2")
    assert p is not None and p.endswith(".dll" if IS_WIN else ".so")
