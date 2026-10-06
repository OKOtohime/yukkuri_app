"""
WP-02 L1：CLI 参数到运行配置的映射（BL-02、BL-03、FR-VAD-03/04）与短切片过滤（FR-VAD-06）

cli.main() 真实执行参数解析和配置构造；识别、合成、VAD、虚拟声卡和流水线在导入边界上替换为桩，
桩流水线的 run() 只记录收到的配置，不访问设备。
"""

import sys
from unittest import mock

import numpy as np
import pytest

import yukkuri.cli as cli
from yukkuri.config import AppConfig
from yukkuri.pipeline import YukkuriPipeline

pytestmark = pytest.mark.l1


@pytest.fixture
def run_cli(monkeypatch, capsys):
    captured = {}

    class FakePipeline:
        def __init__(self, config, **kw):
            captured["config"] = config

        def run(self):
            captured["ran"] = True

        def stop(self):
            pass

    def fake_vad(**kw):
        captured["vad_kwargs"] = kw
        return mock.Mock()

    monkeypatch.setattr(cli, "YukkuriPipeline", FakePipeline)
    monkeypatch.setattr(cli, "AquesTalk1Engine", mock.Mock())
    monkeypatch.setattr(cli, "SenseVoiceASR", mock.Mock())
    monkeypatch.setattr(cli, "VoskASR", mock.Mock())
    monkeypatch.setattr(cli, "SileroVAD", fake_vad)
    monkeypatch.setattr(cli, "VirtualMicManager", mock.Mock())
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    monkeypatch.setattr(AppConfig, "find_aquestalk_library", lambda self, v=None: "/fake/libAquesTalk.so")
    monkeypatch.setattr(AppConfig, "find_sensevoice_dir", lambda self: "/fake/sensevoice")
    monkeypatch.setattr(AppConfig, "find_vad_model", lambda self: "/fake/silero_vad.onnx")

    def run(*argv):
        captured.clear()
        monkeypatch.setattr(sys, "argv", ["yukkuri", *argv])
        try:
            rc = cli.main()
        except SystemExit as e:
            rc = e.code
        out = capsys.readouterr()
        return rc, captured, out.out + out.err

    return run


# ---------------------------------------------------------------- BL-02 / BL-03

def _rejected_or_warned(rc, cap, output, option):
    rejected = rc not in (0, None) and not cap.get("ran")
    warned = option in output or "范围" in output
    return rejected or warned


@pytest.mark.parametrize("value", ["0.5", "1.0", "3.0"])
def test_bl_02_cli_accepts_in_range_mic_gain(run_cli, value):
    rc, cap, _ = run_cli("--mic-gain", value)
    assert rc == 0 and cap["ran"] and cap["config"].mic_gain == float(value)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-207")
@pytest.mark.parametrize("value", ["0.49", "3.01", "0", "-1", "10"])
def test_bl_02_cli_rejects_out_of_range_mic_gain(run_cli, value):
    rc, cap, output = run_cli("--mic-gain", value)
    assert _rejected_or_warned(rc, cap, output, "--mic-gain"), f"--mic-gain {value} 被静默接受"


@pytest.mark.parametrize("value", ["0.1", "1.0", "3.0"])
def test_bl_03_cli_accepts_in_range_output_gain(run_cli, value):
    rc, cap, _ = run_cli("--output-gain", value)
    assert rc == 0 and cap["ran"] and cap["config"].output_gain == float(value)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-207")
@pytest.mark.parametrize("value", ["0.09", "3.01", "0", "-1", "10"])
def test_bl_03_cli_rejects_out_of_range_output_gain(run_cli, value):
    rc, cap, output = run_cli("--output-gain", value)
    assert _rejected_or_warned(rc, cap, output, "--output-gain"), f"--output-gain {value} 被静默接受"


# ---------------------------------------------------------------- FR-VAD-03 / 04 决策表

# (参数, 期望的有效上限；0 表示关闭截断)
MAX_SPEECH_TABLE = [
    ([], 6.0),
    (["--max-speech-duration", "2"], 2.0),
    (["--max-speech-duration", "15"], 15.0),
    (["--max-speech-duration", "0"], 0.0),
    (["--max-speech-duration", "-1"], 0.0),
    (["--no-max-speech"], 0.0),
    (["--disable-cutoff"], 0.0),
    (["--no-max-speech", "--max-speech-duration", "8"], 0.0),
]


@pytest.mark.parametrize("argv, expected", MAX_SPEECH_TABLE, ids=[" ".join(a) or "default" for a, _ in MAX_SPEECH_TABLE])
def test_fr_vad_03_effective_max_decision_table(run_cli, argv, expected):
    rc, cap, _ = run_cli(*argv)
    assert rc == 0
    assert cap["config"].get_effective_max_speech_duration() == expected
    assert cap["vad_kwargs"]["max_speech_duration"] == expected


def test_fr_vad_03_gui_config_mapping():
    """GUI 用同样的 AppConfig 字段构造 VAD：开关关闭时无论滑块取值都应为 0"""
    assert AppConfig(vad_max_speech=6.0, vad_enable_max_speech=True).get_effective_max_speech_duration() == 6.0
    assert AppConfig(vad_max_speech=15.0, vad_enable_max_speech=False).get_effective_max_speech_duration() == 0.0
    assert AppConfig(vad_max_speech=2.0, vad_enable_max_speech=True).get_effective_max_speech_duration() == 2.0


# ---------------------------------------------------------------- FR-VAD-06 短切片

@pytest.fixture
def pipeline():
    asr = mock.Mock()
    asr.decode.return_value = ""
    p = YukkuriPipeline(AppConfig(), asr=asr, tts=mock.Mock(), loopback_manager=mock.Mock())
    yield p
    p.player.stop()


@pytest.mark.parametrize("seconds, reaches_asr", [(0.05, False), (0.2, False), (0.299, False), (0.3, True), (0.31, True), (1.0, True)])
def test_fr_vad_06_short_segments_dropped(pipeline, seconds, reaches_asr):
    pipeline.process_segment(np.zeros(int(round(16000 * seconds)), dtype=np.float32))
    assert pipeline.asr.decode.called == reaches_asr


def test_fr_asr_07_empty_text_no_synthesis(pipeline):
    """识别结果为空时不合成（FR-VAD-06 的后半段判定依赖这一点）"""
    pipeline.player = mock.Mock()
    pipeline.process_segment(np.zeros(16000, dtype=np.float32))
    pipeline.tts.synthesize.assert_not_called()
    pipeline.player.play.assert_not_called()
