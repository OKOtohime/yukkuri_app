"""
WP-04 L1：语音合成（FR-TTS-01~07、BL-01 语速、IMPL-14/15），使用 AquesTalk 桩库（qa/stubs/）。

桩库被 ctypes 真实加载，按库所在目录名报告声线、按收到的参数报告语速和编码接口，
并把 SetDevKey/SetUsrKey/Synthe/FreeWave 调用记录到 QA_STUB_LOG。设计见 docs/test-design/WP-04.md。
"""

import ast
import os
import re
import shutil
import sys
import threading
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yukkuri.cli as cli  # noqa: E402
from yukkuri.config import AppConfig  # noqa: E402
from yukkuri.pipeline import YukkuriPipeline  # noqa: E402
from yukkuri.tts.aquestalk1 import AquesTalk1Engine  # noqa: E402

from wp04_util import (  # noqa: E402
    IS_WIN, LIBNAME, REPO, VOICES, isolate_home, isolated_env, make_stub_data, read_log, run_cli, stub_lib, wav_tag,
)

pytestmark = pytest.mark.l1

APP_PY = os.path.join(REPO, "yukkuri", "gui", "app.py")
README = os.path.join(REPO, "README.md")


# ---------------------------------------------------------------- 夹具

@pytest.fixture
def stub_env(tmp_path, monkeypatch):
    """进程内：HOME 隔离，数据目录放 9 种声线的桩库，QA_STUB_LOG 指向临时文件"""
    isolate_home(monkeypatch, tmp_path)
    data = str(tmp_path / "data")
    make_stub_data(data)
    log = str(tmp_path / "stub.log")
    monkeypatch.setenv("YUKKURI_DATA_DIR", data)
    monkeypatch.setenv("QA_STUB_LOG", log)
    return SimpleNamespace(data=data, log=log, tmp=tmp_path)


def engine_for(data, voice="f1", **kw):
    cfg = AppConfig(project_root=data)
    return AquesTalk1Engine(voice=voice, voice_resolver=cfg.find_aquestalk_library, **kw)


class FakeG2P:
    """按顺序返回预设音标（让某一句故意合成失败）"""

    def __init__(self, koes):
        self.koes = list(koes)

    def convert(self, text, lang="zh"):
        return self.koes.pop(0)


def make_pipeline(engine, koes=None, config=None):
    p = YukkuriPipeline(config or AppConfig(), asr=mock.Mock(), tts=engine,
                        g2p=FakeG2P(koes) if koes is not None else None, loopback_manager=mock.Mock())
    p.player = mock.Mock()
    p.asr.decode.return_value = "测试"
    return p


def played_tags(pipeline):
    return [wav_tag(c.args[0]) for c in pipeline.player.play.call_args_list]


class FakeMenu:
    def __init__(self, value=""):
        self.values, self.value = [], value

    def configure(self, values=None, **kw):
        if values is not None:
            self.values = list(values)

    def get(self):
        return self.value

    def set(self, v):
        self.value = v


def gui_cls():
    pytest.importorskip("customtkinter")
    from yukkuri.gui.app import YukkuriApp
    return YukkuriApp


# ---------------------------------------------------------------- FR-TTS-01

@pytest.mark.parametrize("voice", VOICES)
def test_fr_tts_01_cli_accepts_nine_voices(tmp_path, voice):
    """真实 CLI 子进程：--voice 被接受，引擎以该声线初始化，之后因缺 ASR 模型退出 1。
    初始化时库只在设置密钥时被调用，所以带一个有效 DevKey，由桩库记录是哪个声线的库收到调用"""
    data = make_stub_data(str(tmp_path / "data"))
    log = str(tmp_path / "stub.log")
    env = isolated_env(str(tmp_path / "home"), data, QA_STUB_LOG=log)
    r = run_cli(["--voice", voice, "--dev-key", "VALID-DEV"], env, cwd=str(tmp_path))
    assert r.returncode == 1, r.stderr[-1500:]
    assert "SenseVoice" in r.stderr  # 越过了 TTS 初始化，停在 ASR 模型检查
    loads = [e[1] for e in read_log(log) if e[0] == "LOAD"]
    assert loads == [voice]


@pytest.mark.parametrize("value", ["f4", "F1", "m3", "", "f1 ", "aquestalk"])
def test_fr_tts_01_cli_rejects_other_voices(monkeypatch, capsys, value):
    monkeypatch.setattr(sys, "argv", ["yukkuri", "--voice", value])
    with pytest.raises(SystemExit) as ei:
        cli.parse_args()
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert all(v in err for v in VOICES), "提示中应列出可选声线"


# ---------------------------------------------------------------- FR-TTS-02

@pytest.mark.parametrize("installed", [VOICES, ["f1", "m1"], ["dvd"]], ids=["all9", "f1+m1", "dvd_only"])
def test_fr_tts_02_gui_lists_installed_only(tmp_path, monkeypatch, installed):
    isolate_home(monkeypatch, tmp_path)
    data = make_stub_data(str(tmp_path / "data"), installed)
    monkeypatch.setenv("YUKKURI_DATA_DIR", data)
    fake = SimpleNamespace(voice_menu=FakeMenu())
    gui_cls()._refresh_voices(fake)
    listed = [o.split(":")[0] for o in fake.voice_menu.values]
    assert sorted(listed) == sorted(installed)
    assert fake.voice_menu.get().split(":")[0] in installed


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-205")
def test_fr_tts_02_gui_single_library_import(tmp_path, monkeypatch):
    """FR-MDL-05 的单文件导入方式之后，列表只应出现一种声线"""
    isolate_home(monkeypatch, tmp_path)
    data = str(tmp_path / "data")
    monkeypatch.setenv("YUKKURI_DATA_DIR", data)
    from yukkuri.model_downloader import install_aquestalk_from_archive
    src = tmp_path / LIBNAME
    shutil.copy(stub_lib(), src)
    install_aquestalk_from_archive(str(src), root=data)
    fake = SimpleNamespace(voice_menu=FakeMenu())
    gui_cls()._refresh_voices(fake)
    assert len(fake.voice_menu.values) == 1


def _gui_start_fake(msg_queue):
    """_start_pipeline 需要读取的控件替身"""
    def widget(value):
        w = mock.Mock()
        w.get.return_value = value
        return w
    return SimpleNamespace(
        engine_seg=widget("SenseVoice (高精度)"), lang_seg=widget("中文 (zh)"),
        device_menu=widget("default"), device_map={}, speed_slider=widget(100), vad_slider=widget(350),
        max_speech_slider=widget(6.0), gain_slider=widget(1.0), output_gain_slider=widget(1.0),
        switch_dyn_mic=widget(0), switch_loopback=widget(0), switch_max_speech=widget(1),
        voice_menu=FakeMenu("未检测到声线 (请点击上方模型管理导入)"),
        status_indicator=mock.Mock(), btn_toggle=mock.Mock(), msg_queue=msg_queue,
        _append_log=mock.Mock(), pipeline_thread=None,
    )


def test_fr_tts_02_gui_none_prompts_import(tmp_path, monkeypatch):
    """一个声线都没有：列表显示导入提示；点击启动时提示并打开模型管理（error_model 消息）"""
    import queue
    import time
    isolate_home(monkeypatch, tmp_path)
    data = str(tmp_path / "data")
    os.makedirs(data)
    monkeypatch.setenv("YUKKURI_DATA_DIR", data)
    App = gui_cls()
    fake = SimpleNamespace(voice_menu=FakeMenu())
    App._refresh_voices(fake)
    assert len(fake.voice_menu.values) == 1 and "导入" in fake.voice_menu.values[0]

    q = queue.Queue()
    App._start_pipeline(_gui_start_fake(q))
    deadline = time.time() + 30
    msg = None
    while time.time() < deadline and msg is None:
        try:
            msg = q.get(timeout=0.5)
        except queue.Empty:
            pass
    assert msg is not None and msg[0] == "error_model" and "模型管理" in msg[1]


# ---------------------------------------------------------------- FR-TTS-03

def test_fr_tts_03_switch_next_sentence(stub_env):
    """流水线运行中依次切换 f1→m1→dvd→f1：每次切换后的下一句由新声线合成；已加载的声线不重复加载"""
    eng = engine_for(stub_env.data)
    p = make_pipeline(eng)
    seg = np.zeros(16000, dtype=np.float32)
    p.process_segment(seg)
    for v in ["m1", "dvd", "f1"]:
        assert p.set_voice(v) is True
        p.process_segment(seg)
    assert [t["VOICE"] for t in played_tags(p)] == ["f1", "m1", "dvd", "f1"]
    assert p.config.voice == "f1"
    loads = [e[1] for e in read_log(stub_env.log) if e[0] == "LOAD"]
    assert loads == ["f1", "m1", "dvd"]


def test_fr_tts_03_switch_to_missing_keeps_original(tmp_path, monkeypatch, capfd):
    isolate_home(monkeypatch, tmp_path)
    data = make_stub_data(str(tmp_path / "data"), ["f1", "m1"])
    eng = engine_for(data)
    p = make_pipeline(eng)
    assert p.set_voice("dvd") is False
    err = capfd.readouterr().err
    assert "dvd" in err
    assert eng.current_voice == "f1" and p.config.voice == "f1"
    p.process_segment(np.zeros(16000, dtype=np.float32))
    assert played_tags(p)[0]["VOICE"] == "f1"


@pytest.mark.parametrize("kind", ["empty_file", "text_file", "no_symbols"])
def test_fr_tts_03_switch_to_broken_library_keeps_original(tmp_path, monkeypatch, capfd, kind):
    isolate_home(monkeypatch, tmp_path)
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    bad_dir = os.path.join(data, "libs", "aquestalk", "lib64", "m2")
    os.makedirs(bad_dir)
    bad = os.path.join(bad_dir, LIBNAME)
    if kind == "empty_file":
        open(bad, "wb").close()
    elif kind == "text_file":
        with open(bad, "w") as f:
            f.write("not a library\n")
    else:
        shutil.copy(stub_lib("empty"), bad)
    eng = engine_for(data)
    assert eng.set_voice("m2") is False
    assert "m2" in capfd.readouterr().err
    assert eng.current_voice == "f1"
    assert wav_tag(eng.synthesize("ゆっくり"))["VOICE"] == "f1"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-410")
def test_fr_tts_03_gui_failed_switch_feedback(tmp_path, monkeypatch):
    """GUI 回调切换到坏掉的声线：日志提示失败；下拉框显示的声线应与实际合成的声线一致"""
    isolate_home(monkeypatch, tmp_path)
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    os.makedirs(os.path.join(data, "libs", "aquestalk", "lib64", "m2"))
    open(os.path.join(data, "libs", "aquestalk", "lib64", "m2", LIBNAME), "wb").close()
    p = make_pipeline(engine_for(data))
    logs = []
    menu = FakeMenu("m2: 男声2")  # CTkOptionMenu 在回调之前已经显示用户选中的项
    fake = SimpleNamespace(pipeline=p, is_running=True, voice_menu=menu, _append_log=lambda who, m: logs.append(m))
    gui_cls()._on_voice_changed(fake, "m2: 男声2")
    assert any("失败" in m for m in logs)
    assert p.tts.current_voice == "f1"
    assert menu.get().split(":")[0] == p.tts.current_voice


# ---------------------------------------------------------------- FR-TTS-04 / BL-01

@pytest.fixture
def cli_speed(tmp_path, monkeypatch, capsys):
    """真实执行 cli.main() 的参数解析与引擎构造（桩库）；ASR/VAD/虚拟声卡替换为桩，
    替身流水线在 run() 中播报一句，记录实际送到库的语速"""
    isolate_home(monkeypatch, tmp_path)
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    log = str(tmp_path / "stub.log")
    monkeypatch.setenv("YUKKURI_DATA_DIR", data)
    monkeypatch.setenv("QA_STUB_LOG", log)
    monkeypatch.setattr(AppConfig, "find_sensevoice_dir", lambda self: "/fake/sensevoice")
    monkeypatch.setattr(AppConfig, "find_vad_model", lambda self: "/fake/vad.onnx")
    monkeypatch.setattr(cli, "SenseVoiceASR", mock.Mock())
    monkeypatch.setattr(cli, "SileroVAD", mock.Mock())
    monkeypatch.setattr(cli, "VirtualMicManager", mock.Mock())

    class SpeakOnce(YukkuriPipeline):
        def run(self):
            self.player = mock.Mock()
            self.speak_text("ゆっくり")

    monkeypatch.setattr(cli, "YukkuriPipeline", SpeakOnce)

    def run(value):
        monkeypatch.setattr(sys, "argv", ["yukkuri", "--speed", str(value)])
        try:
            rc = cli.main()
        except SystemExit as e:
            rc = e.code
        out = capsys.readouterr()
        speeds = [int(e[3]) for e in read_log(log) if e[0] == "Synthe"]
        if os.path.exists(log):
            os.remove(log)
        return rc, out, speeds

    return run


@pytest.mark.parametrize("value", [50, 51, 100, 250, 251, 299, 300])
def test_fr_tts_04_cli_speed_in_range(cli_speed, value):
    rc, out, speeds = cli_speed(value)
    assert rc in (0, None)
    assert speeds == [value], "有效范围内的语速应原样送到合成"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-405")
@pytest.mark.parametrize("value", [49, 301, 0, -1, 1000])
def test_fr_tts_04_cli_speed_out_of_range(cli_speed, value):
    """BL-01：越界值应拒绝或给出提示，不能静默钳位"""
    rc, out, speeds = cli_speed(value)
    rejected = rc not in (0, None)
    warned = bool(re.search(r"语速|speed", out.err + out.out, re.I) and re.search(r"50|300|范围|range", out.err + out.out))
    assert rejected or warned, f"rc={rc} 送到库的语速={speeds}"


def test_fr_tts_04_runtime_change_next_sentence(stub_env):
    """GUI 语速滑块回调：下一句按新语速合成"""
    p = make_pipeline(engine_for(stub_env.data))
    p.speak_text("ゆっくり")
    fake = SimpleNamespace(pipeline=p, speed_label=mock.Mock())
    gui_cls()._on_speed_changed(fake, 200.0)
    p.speak_text("ゆっくり")
    gui_cls()._on_speed_changed(fake, 50.0)
    p.speak_text("ゆっくり")
    assert [int(t["SPEED"]) for t in played_tags(p)] == [100, 200, 50]


def _slider_kwargs(attr):
    tree = ast.parse(open(APP_PY, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            t = node.targets[0]
            if isinstance(t, ast.Attribute) and t.attr == attr:
                return {k.arg: ast.literal_eval(k.value) for k in node.value.keywords if k.arg in ("from_", "to")}
    raise AssertionError(f"未找到 {attr}")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-406")
def test_fr_tts_04_gui_slider_range():
    """BL-01：有效范围 50~300；GUI 只到 250 记为一致性缺陷（低）"""
    assert _slider_kwargs("speed_slider") == {"from_": 50, "to": 300}


# ---------------------------------------------------------------- FR-TTS-05

KEY_COMBOS = [(c, e, d) for c in (True, False) for e in (True, False) for d in (True, False)]


def _expected_key(kind, c, e, d):
    return f"cli-{kind}" if c else f"env-{kind}" if e else f"dotenv-{kind}" if d else None


@pytest.mark.parametrize("cli_set,env_set,dotenv_set", KEY_COMBOS,
                         ids=[f"cli{int(c)}-env{int(e)}-dotenv{int(d)}" for c, e, d in KEY_COMBOS])
def test_fr_tts_05_key_precedence(tmp_path, cli_set, env_set, dotenv_set):
    """决策表（CLI 入口，真实子进程）：DevKey 与 UsrKey 同时按 CLI > 环境变量 > .env 取值；都没有时不调用"""
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    log = str(tmp_path / "stub.log")
    extra = {}
    if env_set:
        extra.update(AQUESTALK_DEV_KEY="env-dev", AQUESTALK_USR_KEY="env-usr")
    env = isolated_env(str(tmp_path / "home"), data, QA_STUB_LOG=log, **extra)
    cwd = tmp_path / "work"
    cwd.mkdir()
    if dotenv_set:
        (cwd / ".env").write_text("AQUESTALK_DEV_KEY=dotenv-dev\nAQUESTALK_USR_KEY=dotenv-usr\n", encoding="utf-8")
    args = ["--dev-key", "cli-dev", "--usr-key", "cli-usr"] if cli_set else []
    r = run_cli(args, env, cwd=str(cwd))
    assert r.returncode == 1 and "SenseVoice" in r.stderr, r.stderr[-1500:]
    calls = read_log(log)
    for kind, op in (("dev", "SetDevKey"), ("usr", "SetUsrKey")):
        got = [e[2] for e in calls if e[0] == op]
        exp = _expected_key(kind, cli_set, env_set, dotenv_set)
        assert got == ([exp] if exp else []), f"{op}: {got}"


def test_fr_tts_05_cli_empty_string_recorded(tmp_path):
    """--dev-key "" 且环境变量有值：只记录行为（空串视为"不使用密钥"，环境变量不再生效），不崩溃"""
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    log = str(tmp_path / "stub.log")
    env = isolated_env(str(tmp_path / "home"), data, QA_STUB_LOG=log, AQUESTALK_DEV_KEY="env-dev")
    r = run_cli(["--dev-key", ""], env, cwd=str(tmp_path))
    assert r.returncode == 1 and "SenseVoice" in r.stderr
    assert [e for e in read_log(log) if e[0] == "SetDevKey"] == []


@pytest.mark.parametrize("env_set,dotenv_set", [(True, True), (True, False), (False, True), (False, False)])
def test_fr_tts_05_gui_key_precedence(tmp_path, env_set, dotenv_set):
    """GUI 入口：导入 GUI 模块后按 GUI 启动代码的方式构造配置与引擎（GUI 没有密钥输入框，只有环境变量 > .env）"""
    import subprocess
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    log = str(tmp_path / "stub.log")
    extra = {"AQUESTALK_DEV_KEY": "env-dev"} if env_set else {}
    env = isolated_env(str(tmp_path / "home"), data, QA_STUB_LOG=log, **extra)
    cwd = tmp_path / "work"
    cwd.mkdir()
    if dotenv_set:
        (cwd / ".env").write_text("AQUESTALK_DEV_KEY=dotenv-dev\n", encoding="utf-8")
    code = ("import yukkuri.gui.app as g; from yukkuri.config import AppConfig; c = AppConfig(); "
            "g.AquesTalk1Engine(voice=c.voice, voice_resolver=c.find_aquestalk_library, dev_key=c.dev_key, usr_key=c.usr_key)")
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(cwd), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    exp = "env-dev" if env_set else "dotenv-dev" if dotenv_set else None
    assert [e[2] for e in read_log(log) if e[0] == "SetDevKey"] == ([exp] if exp else [])


def test_fr_tts_05_gui_start_passes_config_keys():
    """静态检查：GUI 启动代码把 config.dev_key / config.usr_key 传给引擎（上一个用例模拟的就是这段代码）"""
    src = open(APP_PY, encoding="utf-8").read()
    m = re.search(r"AquesTalk1Engine\((.*?)\)", src, re.S)
    assert m and "dev_key=config.dev_key" in m.group(1) and "usr_key=config.usr_key" in m.group(1)


def test_fr_tts_05_key_applied_to_hot_switched_voice(stub_env):
    eng = engine_for(stub_env.data, dev_key="VALID-DEV", usr_key="VALID-USR")
    assert eng.set_voice("m1")
    calls = read_log(stub_env.log)
    assert ("SetDevKey", "m1", "VALID-DEV") in [tuple(e[:3]) for e in calls]
    assert ("SetUsrKey", "m1", "VALID-USR") in [tuple(e[:3]) for e in calls]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-407")
def test_fr_tts_05_readme_precedence_statement():
    """README"密钥配置方式"中"按优先级由高到低"列出的顺序应为 CLI > 环境变量 > .env"""
    text = open(README, encoding="utf-8").read()
    sec = text[text.index("### 密钥配置方式"):]
    sec = sec[: sec.index("\n## ", 1) if "\n## " in sec[1:] else len(sec)]
    assert "由高到低" in sec
    heads = re.findall(r"####\s*方式[一二三][：:]\s*(.+)", sec)
    order = ["dotenv" if ".env" in h else "env" if "环境变量" in h else "cli" if ("命令行" in h or "参数" in h) else h
             for h in heads]
    assert order == ["cli", "env", "dotenv"], f"README 中的顺序: {heads}"


# ---------------------------------------------------------------- FR-TTS-06

@pytest.mark.parametrize("key", ["WRONG-KEY", "has space", "x" * 4096])
def test_fr_tts_06_invalid_key_warns_and_continues(stub_env, capfd, key):
    eng = engine_for(stub_env.data, dev_key=key, usr_key=key)
    err = capfd.readouterr().err
    assert "警告" in err and "SetDevKey" in err and "SetUsrKey" in err
    assert key not in err  # 提示中不回显密钥（QR-SEC-02 线索）
    assert wav_tag(eng.synthesize("ゆっくり"))["VOICE"] == "f1"


def test_fr_tts_06_valid_key_no_warning(stub_env, capfd):
    eng = engine_for(stub_env.data, dev_key="VALID-DEV", usr_key="VALID-USR")
    assert "警告" not in capfd.readouterr().err
    assert eng.synthesize("ゆっくり")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-404")
@pytest.mark.parametrize("key", ["ｋｅｙ１２３", "密钥", "key 123"], ids=["fullwidth", "cjk", "nbsp"])
def test_fr_tts_06_non_ascii_key_warns_and_continues(stub_env, capfd, key):
    """从网页复制密钥时混入全角字符或不换行空格：属于无效密钥，应警告并继续按评价版合成"""
    try:
        eng = engine_for(stub_env.data, dev_key=key)
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"引擎初始化抛出 {type(e).__name__}: {e}")
    assert "警告" in capfd.readouterr().err
    assert eng.synthesize("ゆっくり")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-404")
def test_fr_tts_06_cli_non_ascii_key_starts(tmp_path):
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    env = isolated_env(str(tmp_path / "home"), data)
    r = run_cli(["--dev-key", "ｋｅｙ１２３"], env, cwd=str(tmp_path))
    assert "初始化 AquesTalk 失败" not in r.stderr, r.stderr[-800:]
    assert "SenseVoice" in r.stderr


def test_fr_tts_06_cli_invalid_ascii_key_starts(tmp_path):
    data = make_stub_data(str(tmp_path / "data"), ["f1"])
    env = isolated_env(str(tmp_path / "home"), data)
    r = run_cli(["--dev-key", "WRONG-KEY"], env, cwd=str(tmp_path))
    assert "警告" in r.stderr and "SenseVoice" in r.stderr


# ---------------------------------------------------------------- FR-TTS-07

KNOWN_CODES = [100, 101, 102, 103, 104, 105, 106, 107]


@pytest.mark.parametrize("code", KNOWN_CODES)
def test_fr_tts_07_known_error_codes_reported(stub_env, capfd, code):
    eng = engine_for(stub_env.data)
    assert eng.synthesize(f"#E{code}") is None
    err = capfd.readouterr().err
    assert str(code) in err and "未知错误" not in err


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-403")
@pytest.mark.parametrize("code", [204, 1, 4, 28], ids=["204_overlong", "win_pos1", "win_pos4", "win_pos28"])
def test_fr_tts_07_unregistered_error_codes_reported(stub_env, capfd, code):
    """评审 Q3：库返回未登记的错误码（Linux 超长音标 204；Windows 评价版的字节位置码）时，
    "错误码 N: 未知错误" 不算说明了原因"""
    eng = engine_for(stub_env.data)
    assert eng.synthesize(f"#E{code}") is None
    err = capfd.readouterr().err
    assert str(code) in err
    assert "未知错误" not in err, err.strip()


def test_fr_tts_07_pipeline_skips_and_continues(stub_env, capfd):
    """三句中间一句合成失败：第 1、3 句照常播放，第 2 句不播放，有提示，流水线不抛异常"""
    p = make_pipeline(engine_for(stub_env.data), koes=["ゆ", "#E105", "く"])
    seg = np.zeros(16000, dtype=np.float32)
    for _ in range(3):
        p.process_segment(seg)
    assert p.player.play.call_count == 2
    assert "105" in capfd.readouterr().err


def test_fr_tts_07_text_broadcast_failure(stub_env, capfd):
    p = make_pipeline(engine_for(stub_env.data), koes=["#E102", "ゆっくり"])
    p.speak_text("很长的一句话")
    p.speak_text("下一句")
    assert p.player.play.call_count == 1
    assert "102" in capfd.readouterr().err


# ---------------------------------------------------------------- IMPL-14 / IMPL-15

def test_impl_14_shift_jis_branch(tmp_path, monkeypatch):
    isolate_home(monkeypatch, tmp_path)
    data = make_stub_data(str(tmp_path / "data"), ["f1"], variant="no_utf8")
    log = str(tmp_path / "stub.log")
    monkeypatch.setenv("QA_STUB_LOG", log)
    eng = engine_for(data)
    koe = "ゆっくりしていってね、ー"
    assert wav_tag(eng.synthesize(koe))["IF"] == "sjis"
    sent = [e for e in read_log(log) if e[0] == "Synthe"][-1]
    assert bytes.fromhex(sent[4]) == koe.encode("shift-jis")


def test_impl_14_utf8_branch_preferred(stub_env):
    eng = engine_for(stub_env.data)
    eng.synthesize("ゆっくり")
    sent = [e for e in read_log(stub_env.log) if e[0] == "Synthe"][-1]
    assert sent[2] == "utf8" and bytes.fromhex(sent[4]) == "ゆっくり".encode("utf-8")


def test_impl_15_free_wave_each_synthesis(stub_env):
    eng = engine_for(stub_env.data)
    for koe in ["ゆ", "#E105", "く", "#E204", "り"]:
        eng.synthesize(koe)
    ops = [e[0] for e in read_log(stub_env.log)]
    assert ops.count("Synthe") == 5 and ops.count("FreeWave") == 3


# ---------------------------------------------------------------- 观察项

def test_obs_401_synthesize_with_unloaded_voice_deadlocks(stub_env):
    """synthesize(koe, voice=<未加载的声线>) 持锁调用 set_voice()，再次获取同一把非重入锁 → 死锁。
    产品代码不传 voice 参数，没有可观察后果，记 OBS-401。线程留在后台（守护线程）。"""
    eng = engine_for(stub_env.data)
    t = threading.Thread(target=eng.synthesize, args=("ゆっくり",), kwargs={"voice": "m1"}, daemon=True)
    t.start()
    t.join(3)
    assert t.is_alive(), "OBS-401 不再复现：synthesize(voice=...) 已不死锁"
