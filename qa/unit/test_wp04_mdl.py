"""
WP-04 L1：模型下载与状态（FR-MDL-01/02/04/07、IMPL-22、QR-SEC-04 检查）。

setup_models.py 在子进程中真实执行，下载地址经 qa/harness/run_setup_models.py 改到本地模型服务
（qa/harness/model_server.py，提供与真实发布物目录结构一致的假模型）。
"""

import hashlib
import io
import os
import re
import shutil
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wp04_util import (  # noqa: E402
    IS_WIN, REPO, isolated_env, loader_view, make_stub_data, model_status, run_setup,
)
from model_server import SENSE_DIR, VOSK_TOPS, ModelServer, build_artifacts  # noqa: E402

pytestmark = pytest.mark.l1

ITEM_REQ = {"vad": "silero_vad.onnx", "sensevoice": "sensevoice.tar.bz2",
            "vosk-zh": "vosk-zh.zip", "vosk-ja": "vosk-ja.zip", "vosk-en": "vosk-en.zip"}
REQ_ITEM = {v: k for k, v in ITEM_REQ.items()}


@pytest.fixture(scope="module")
def server():
    with ModelServer(build_artifacts()) as s:
        yield s


@pytest.fixture
def sandbox(tmp_path, server):
    server.requests.clear()
    data = str(tmp_path / "data")
    os.makedirs(data)
    env = isolated_env(str(tmp_path / "home"), data)

    def run(args, modes=None, urls=None, env_override=None, **kw):
        return run_setup(args, env_override or env, urls if urls is not None else server.url_map(modes), **kw)

    return type("Sandbox", (), {"data": data, "env": env, "run": staticmethod(run), "tmp": tmp_path})


def requested(server, mode="ok"):
    return {REQ_ITEM[n] for m, n in server.requests if m == mode}


def member(arts, archive, name) -> bytes:
    data = arts[archive]
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.read(name)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tf:
        return tf.extractfile(name).read()


def sha_file(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def content_ok(data, item, arts) -> bool:
    """落盘内容与服务端发布物逐字节一致"""
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    try:
        if item == "vad":
            return sha_file(os.path.join(data, "silero_vad.onnx")) == sha(arts["silero_vad.onnx"])
        if item == "sensevoice":
            d = os.path.join(data, SENSE_DIR)
            return (sha_file(os.path.join(d, "model.int8.onnx")) == sha(member(arts, "sensevoice.tar.bz2", f"{SENSE_DIR}/model.int8.onnx"))
                    and sha_file(os.path.join(d, "tokens.txt")) == sha(member(arts, "sensevoice.tar.bz2", f"{SENSE_DIR}/tokens.txt")))
        lang = item.split("-")[1]
        top = VOSK_TOPS[lang]
        # 优先看加载侧实际选中的目录（.sh 把中文装到 model_cn），找不到时看 .py 的落盘位置 model_<lang>
        target = loader_view(data)[f"vosk_{lang}"] or os.path.join(data, f"model_{lang}")
        return sha_file(os.path.join(target, "am", "final.mdl")) == sha(member(arts, f"vosk-{lang}.zip", f"{top}/am/final.mdl"))
    except (FileNotFoundError, KeyError):
        return False


STATUS_KEY = {"vad": "vad", "sensevoice": "sensevoice", "vosk-zh": "vosk_zh", "vosk-ja": "vosk_ja", "vosk-en": "vosk_en"}


# ---------------------------------------------------------------- FR-MDL-01

def test_fr_mdl_01_default_downloads_vad_and_sensevoice(sandbox, server):
    r = sandbox.run([])
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert requested(server) == {"vad", "sensevoice"}
    st = model_status(sandbox.data)
    assert st["vad"] and st["sensevoice"]
    lv = loader_view(sandbox.data)
    assert lv["vad"] and lv["sensevoice"]
    assert content_ok(sandbox.data, "vad", server.artifacts) and content_ok(sandbox.data, "sensevoice", server.artifacts)


def test_fr_mdl_01_existing_skipped(sandbox, server):
    assert sandbox.run([]).returncode == 0
    server.requests.clear()
    r = sandbox.run([])
    assert r.returncode == 0
    assert server.requests == []
    assert r.stdout.count("跳过") >= 2


def _bare_path_env(sandbox):
    """PATH 中只有一个空目录：没有 bash、curl、tar、unzip（解释器用绝对路径启动）"""
    empty = sandbox.tmp / "emptybin"
    empty.mkdir(exist_ok=True)
    env = dict(sandbox.env)
    env["PATH"] = str(empty)
    if IS_WIN:
        env["PATH"] = str(empty) + os.pathsep + os.path.dirname(sys.executable)
    return env


def test_fr_mdl_01_no_external_tools(sandbox, server):
    env = _bare_path_env(sandbox)
    r = sandbox.run([], env_override=env)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    st = model_status(sandbox.data)
    assert st["vad"] and st["sensevoice"]
    assert content_ok(sandbox.data, "sensevoice", server.artifacts)


@pytest.mark.parametrize("tools", ["system_tar", "no_tools"])
def test_impl_22_unquantized_model_excluded(sandbox, server, tools):
    env = _bare_path_env(sandbox) if tools == "no_tools" else None
    if tools == "system_tar" and not shutil.which("tar"):
        pytest.skip("本机没有 tar")
    assert sandbox.run(["--sensevoice"], env_override=env).returncode == 0
    assert not os.path.exists(os.path.join(sandbox.data, SENSE_DIR, "model.onnx"))
    assert os.path.exists(os.path.join(sandbox.data, SENSE_DIR, "model.int8.onnx"))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-414")
def test_fr_mdl_01_readme_default_scans_aquestalk(sandbox, server):
    """README："python setup_models.py  # 下载 Silero-VAD + SenseVoice 并自动扫描 AquesTalk"。
    官方包放在 ~/Downloads（FR-MDL-06 评审 Q4 的位置）时，无参数运行后应已导入声线"""
    from wp04_util import official_package
    pkg = official_package()
    dl = os.path.join(sandbox.env["HOME"], "Downloads")
    os.makedirs(dl, exist_ok=True)
    shutil.copy(pkg, os.path.join(dl, os.path.basename(pkg)))
    assert sandbox.run([]).returncode == 0
    if IS_WIN:
        pytest.skip("Windows 包名不在扫描候选中（DEF-411），此处只在 Linux 判定")
    assert model_status(sandbox.data)["aquestalk_count"] == 9


# ---------------------------------------------------------------- FR-MDL-02

SELECTIVE = [
    (["--vad"], {"vad"}),
    (["--sensevoice"], {"vad", "sensevoice"}),  # SenseVoice 模式需要 VAD，连带下载不判为越界
    (["--vosk"], {"vosk-zh"}),
    (["--vosk", "zh"], {"vosk-zh"}),
    (["--vosk", "ja"], {"vosk-ja"}),
    (["--vosk", "en"], {"vosk-en"}),
    (["--vosk", "all"], {"vosk-zh", "vosk-ja", "vosk-en"}),
    (["--all"], {"vad", "sensevoice", "vosk-zh", "vosk-ja", "vosk-en"}),
]


@pytest.mark.parametrize("args,expected", SELECTIVE, ids=[" ".join(a) for a, _ in SELECTIVE])
def test_fr_mdl_02_selective_requests(sandbox, server, args, expected):
    r = sandbox.run(args)
    assert r.returncode == 0, r.stdout[-1500:]
    assert requested(server) == expected


@pytest.mark.parametrize("args,expected", SELECTIVE, ids=[" ".join(a) for a, _ in SELECTIVE])
def test_fr_mdl_02_selective_ready(sandbox, server, args, expected, request):
    """下载的每一项，--check 与程序加载侧都认可，内容与发布物一致"""
    if "vosk-zh" in expected:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-201"))
    assert sandbox.run(args).returncode == 0
    st = model_status(sandbox.data)
    lv = loader_view(sandbox.data)
    bad = [i for i in expected if not (st[STATUS_KEY[i]] and lv[STATUS_KEY[i]] and content_ok(sandbox.data, i, server.artifacts))]
    assert bad == [], f"未就绪: {bad}"


def test_fr_mdl_02_invalid_vosk_lang(sandbox, server):
    r = sandbox.run(["--vosk", "ko"])
    assert r.returncode == 2
    assert all(x in r.stderr for x in ("zh", "ja", "en"))
    assert server.requests == []


# ---------------------------------------------------------------- FR-MDL-04

def test_fr_mdl_04_progress_shown(sandbox, server):
    r = sandbox.run(["--vad"])
    assert r.returncode == 0
    pcts = [int(p) for p in re.findall(r"(\d{1,3})%", r.stdout)]
    assert len(pcts) >= 3 and pcts[-1] == 100 and pcts == sorted(pcts)


@pytest.mark.parametrize("failing,mode", [("vad", "404"), ("sensevoice", "404"), ("vosk-ja", "404"), ("vosk-en", "404")])
def test_fr_mdl_04_one_fails_others_continue(sandbox, server, failing, mode):
    """--all 时某一项失败：该项给出包含原因的错误提示，其他项照常完成（vosk-zh 受 DEF-201 影响不计入"其他项"）"""
    r = sandbox.run(["--all"], modes={failing: mode})
    out = r.stdout + r.stderr
    label = {"vad": "Silero-VAD", "sensevoice": "SenseVoice", "vosk-ja": "Vosk [ja]", "vosk-en": "Vosk [en]"}[failing]
    err_lines = [ln for ln in out.splitlines() if "[错误]" in ln and label in ln]
    assert err_lines and "404" in err_lines[0], out[-1500:]
    others = {"vad", "sensevoice", "vosk-ja", "vosk-en"} - {failing}
    st = model_status(sandbox.data)
    assert all(st[STATUS_KEY[i]] for i in others), {i: st[STATUS_KEY[i]] for i in others}
    assert not st[STATUS_KEY[failing]]


# ---------------------------------------------------------------- FR-MDL-07

def _place(data, arts, items):
    for item in items:
        if item == "vad":
            with open(os.path.join(data, "silero_vad.onnx"), "wb") as f:
                f.write(arts["silero_vad.onnx"])
        elif item == "sensevoice":
            with tarfile.open(fileobj=io.BytesIO(arts["sensevoice.tar.bz2"]), mode="r:bz2") as tf:
                tf.extractall(data, filter="data") if hasattr(tarfile, "data_filter") else tf.extractall(data)
        elif item.startswith("vosk-"):
            lang = item.split("-")[1]
            target = {"zh": "model_cn"}.get(lang, f"model_{lang}")  # zh 用加载侧认可的布局（setup_models.sh 的布局）
            with zipfile.ZipFile(io.BytesIO(arts[f"vosk-{lang}.zip"])) as zf:
                zf.extractall(data)
            os.rename(os.path.join(data, VOSK_TOPS[lang]), os.path.join(data, target))


CHECK_LABELS = {"vad": "Silero-VAD", "sensevoice": "SenseVoice", "vosk-zh": "Vosk 中文", "vosk-ja": "Vosk 日文", "vosk-en": "Vosk 英文"}
CHECK_STATES = [
    ([], []),
    (["vad"], []),
    (["sensevoice"], []),
    (["vad", "sensevoice"], ["f1"]),
    (["vosk-zh", "vosk-ja", "vosk-en"], ["f1", "m1"]),
    (["vad", "sensevoice", "vosk-en"], ["f1", "f2", "f3", "m1", "m2", "imd1", "jgr", "dvd", "r1"]),
]


@pytest.mark.parametrize("items,voices", CHECK_STATES, ids=[f"{'+'.join(i) or 'none'}|{len(v)}v" for i, v in CHECK_STATES])
def test_fr_mdl_07_check_states(sandbox, server, items, voices):
    _place(sandbox.data, server.artifacts, items)
    if voices:
        make_stub_data(sandbox.data, voices)
    r = sandbox.run(["--check"])
    assert r.returncode == 0
    assert server.requests == []
    lines = r.stdout.splitlines()
    for item, label in CHECK_LABELS.items():
        line = next(ln for ln in lines if label in ln)
        assert ("[已就绪]" in line) == (item in items), line
    aq = next(ln for ln in lines if "AquesTalk" in ln)
    if voices:
        assert f"{len(voices)} 种声线" in aq and all(v in aq for v in voices), aq
    else:
        assert "[未安装]" in aq


# ---------------------------------------------------------------- QR-SEC-04

URL_RE = re.compile(r"""(?<![\w])(?:https?|ftp)://[^\s"'<>)]+""")


@pytest.mark.parametrize("path", ["yukkuri/model_downloader.py", "setup_models.sh", "setup_models.py",
                                  "yukkuri/gui/model_manager.py"])
def test_qr_sec_04_all_download_urls_https(path):
    """产品配置的下载地址全部为 https（文档链接也一并检查）"""
    text = open(os.path.join(REPO, path), encoding="utf-8").read()
    urls = URL_RE.findall(text)
    if path.endswith("model_downloader.py"):
        import yukkuri.model_downloader as md
        urls += [md.VAD_URL, md.SENSEVOICE_URL, *md.VOSK_URLS.values()]
    plain = [u for u in urls if not u.startswith("https://")]
    assert urls and plain == [], plain


def test_obs_402_no_hash_verification():
    """QR-SEC-04：是否做哈希校验只作为观察项记录（OBS-402）"""
    srcs = [open(os.path.join(REPO, p), encoding="utf-8").read() for p in ("yukkuri/model_downloader.py", "setup_models.sh")]
    assert not any(re.search(r"hashlib|sha256|md5|sha1sum|sha256sum", s) for s in srcs), "已有哈希校验，更新 OBS-402"
