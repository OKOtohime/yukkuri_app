"""
WP-02 L2：最长单句截断（FR-VAD-03、FR-VAD-04；IMPL-06 作为线索），真实 Silero VAD

语料：LibriSpeech 1221-135767-0011（16.5s 连续朗读，几乎没有 ≥ 0.3s 的停顿）。
运行中调整的判定口径（蜕变关系）：运行中把上限改为 X 后，切段结果应与"构造时就用 X"一致
——以最长段为比较量，容差 0.2s。这对应 GUI 运行中拖动滑块或拨动开关的行为（FR-GUI-03）。
"""

import sys

import numpy as np
import pytest

from wp02_util import VAD_DEFAULTS, read_wav, require_audio, stream_vad

pytestmark = [pytest.mark.l2, pytest.mark.requires_models, pytest.mark.requires_corpora]

LONG_UTT = "librispeech_test_clean/1221-135767-0011.wav"
TOL = 0.1


@pytest.fixture(scope="module")
def long_utt():
    require_audio([LONG_UTT])
    return read_wav(LONG_UTT)


@pytest.fixture(scope="module")
def vad_path():
    from yukkuri.config import AppConfig
    return AppConfig().find_vad_model()


def segment_lengths(vad_path, x, ctor_max, runtime_max="unset"):
    from yukkuri.audio.vad import SileroVAD
    vad = SileroVAD(vad_path, max_speech_duration=ctor_max, **VAD_DEFAULTS)
    if runtime_max != "unset":
        vad.set_max_speech_duration(runtime_max)
    segs = stream_vad(vad, x)
    if segs and not any(len(s) for s in segs):
        pytest.skip("VAD 输出的语音段全部为空，受 DEF-211 阻塞")
    return [len(s) / 16000 for s in segs]


@pytest.mark.xfail(sys.platform == "win32", strict=True, raises=AssertionError, reason="DEF-211")
def test_fr_vad_01_segments_carry_audio(vad_path, long_utt):
    """FR-VAD-01 的前提：切出的每一段都带有音频，且合计时长覆盖整句的语音"""
    from yukkuri.audio.vad import SileroVAD
    vad = SileroVAD(vad_path, max_speech_duration=6.0, **VAD_DEFAULTS)
    segs = stream_vad(vad, long_utt)
    assert segs, "没有切出任何语音段"
    assert all(len(s) > 0 for s in segs), f"{len(segs)} 段中有空段：{[len(s) for s in segs]}"
    assert sum(len(s) for s in segs) / len(long_utt) > 0.8
    assert all(np.max(np.abs(s)) > 0.01 for s in segs)


@pytest.mark.xfail(sys.platform == "win32", strict=True, raises=AssertionError, reason="DEF-211")
def test_fr_vad_01_pipeline_reaches_asr(vad_path, long_utt):
    """按 YukkuriPipeline.run() 主循环的做法（50ms 块 → vad.accept_waveform → process_segment），
    统计 ASR 实际收到的音频。用桩 ASR 记录调用，不依赖识别模型"""
    from unittest import mock

    from yukkuri.audio.vad import SileroVAD
    from yukkuri.config import AppConfig
    from yukkuri.pipeline import YukkuriPipeline

    asr = mock.Mock()
    asr.decode.return_value = ""
    vad = SileroVAD(vad_path, max_speech_duration=6.0, **VAD_DEFAULTS)
    p = YukkuriPipeline(AppConfig(), asr=asr, tts=mock.Mock(), vad=vad, loopback_manager=mock.Mock())
    try:
        x = np.concatenate([long_utt, np.zeros(16000, dtype=np.float32)])
        for i in range(0, len(x), 800):
            for seg in p.vad.accept_waveform(x[i:i + 800]):
                p.process_segment(seg)
    finally:
        p.player.stop()
    received = sum(len(c.args[0]) for c in asr.decode.call_args_list) / 16000
    assert asr.decode.call_count >= 2, f"ASR 只被调用 {asr.decode.call_count} 次"
    assert received > 10.0, f"ASR 共收到 {received:.2f}s 音频（整句 16.5s）"


def test_corpus_precondition(vad_path, long_utt):
    """前提：关闭截断时这句话是一整段 > 15s，否则下面的用例没有意义"""
    segs = segment_lengths(vad_path, long_utt, 0.0)
    assert len(segs) == 1 and segs[0] > 15.0


@pytest.mark.parametrize("limit", [2.0, 6.0, 15.0])
def test_fr_vad_03_truncates_at_limit(vad_path, long_utt, limit):
    segs = segment_lengths(vad_path, long_utt, limit)
    assert len(segs) >= 2, f"上限 {limit}s 时 16.5s 句子没有被切开"
    assert max(segs) <= limit + TOL, segs


def test_fr_vad_04_disabled_keeps_long_utterance(vad_path, long_utt):
    for off in (0.0, -1.0, None):
        assert len(segment_lengths(vad_path, long_utt, off)) == 1


RUNTIME_CASES = [
    # (构造时上限, 运行中改为, 缺陷编号)
    (6.0, 3.0, None),
    (15.0, 2.0, None),
    (0.0, 6.0, None),
    (6.0, 12.0, "DEF-202"),
    (2.0, 15.0, "DEF-202"),
    (6.0, 0.0, "DEF-202"),   # 运行中关闭截断（FR-VAD-04）
]


@pytest.mark.parametrize("ctor, runtime, defect", RUNTIME_CASES,
                         ids=[f"{c}->{r}" for c, r, _ in RUNTIME_CASES])
def test_fr_vad_03_runtime_change_takes_effect(vad_path, long_utt, request, ctor, runtime, defect):
    if defect:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason=defect))
    fresh = segment_lengths(vad_path, long_utt, runtime)
    changed = segment_lengths(vad_path, long_utt, ctor, runtime)
    assert abs(max(changed) - max(fresh)) <= 0.2, (
        f"运行中改为 {runtime}s 后最长段 {max(changed):.2f}s，构造时即为 {runtime}s 则最长段 {max(fresh):.2f}s；"
        f"切段 {[round(s, 2) for s in changed]}")
    if runtime > 0:
        assert max(changed) <= runtime + TOL


def test_impl_06_runtime_min_clamp(vad_path, long_utt):
    segs = segment_lengths(vad_path, long_utt, 6.0, 0.5)
    assert 0.9 <= max(segs) <= 1.0 + TOL, segs
