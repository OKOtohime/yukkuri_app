"""
WP-02 L1：麦克风采集（FR-AUD-03、FR-AUD-05）

可观察结果：与设备协商出的参数、打开输入流的参数、回调投递给 VAD/ASR 的样本。
sounddevice 在模块边界上 mock；回调直接用合成音频驱动。
"""

import queue
from unittest import mock

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import yukkuri.audio.capture as capture_mod
from yukkuri.audio.capture import MicrophoneStream

pytestmark = pytest.mark.l1

TARGET_SR = 16000


class FakeDevice:
    """只接受 supported 中的 (采样率, 声道) 组合"""

    def __init__(self, supported, default_sr, max_ch):
        self.supported = set(supported)
        self.default_sr = default_sr
        self.max_ch = max_ch
        self.checked = []

    def check_input_settings(self, device=None, samplerate=None, channels=None, **_):
        self.checked.append((samplerate, channels))
        if (samplerate, channels) not in self.supported:
            raise ValueError("Invalid sample rate / channels")

    def query_devices(self, device=None, kind=None):
        return {"default_samplerate": float(self.default_sr), "max_input_channels": self.max_ch}


DEVICES = {
    "native_16k_mono": FakeDevice({(16000, 1), (48000, 1)}, 48000, 1),
    "usb_48k_stereo_only": FakeDevice({(48000, 2)}, 48000, 2),
    "onboard_44k1": FakeDevice({(44100, 1), (44100, 2)}, 44100, 2),
    "default_22k05": FakeDevice({(22050, 1)}, 22050, 1),
    "array_4ch_48k": FakeDevice({(48000, 4)}, 48000, 4),
    "studio_96k_stereo": FakeDevice({(96000, 2)}, 96000, 2),
    "bt_hfp_8k_mono": FakeDevice({(8000, 1)}, 8000, 1),
}


@pytest.fixture
def mic(monkeypatch):
    def make(device: FakeDevice, gain=1.0):
        monkeypatch.setattr(capture_mod.sd, "check_input_settings", device.check_input_settings)
        monkeypatch.setattr(capture_mod.sd, "query_devices", device.query_devices)
        return MicrophoneStream(sample_rate=TARGET_SR, channels=1, gain=gain)

    return make


def _drain(q: queue.Queue):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _feed(stream: MicrophoneStream, audio_2d: np.ndarray, sr: int, block: int):
    stream.actual_sample_rate = sr
    stream.actual_channels = audio_2d.shape[1]
    for i in range(0, len(audio_2d) - block + 1, block):
        stream._audio_callback(audio_2d[i:i + block], block, None, None)
    return _drain(stream.queue)


# ---------------------------------------------------------------- FR-AUD-03 协商

@pytest.mark.parametrize("name", list(DEVICES))
def test_fr_aud_03_negotiation_decision_table(mic, name):
    dev = DEVICES[name]
    sr, ch = mic(dev)._resolve_input_parameters()
    assert (sr, ch) in dev.supported, f"{name}: 选了设备不支持的 {(sr, ch)}"


@pytest.mark.parametrize("name", list(DEVICES))
def test_fr_aud_03_stream_opened_with_negotiated_params(mic, monkeypatch, name):
    dev = DEVICES[name]
    opened = {}

    class FakeStream:
        def __init__(self, **kw):
            opened.update(kw)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(capture_mod.sd, "InputStream", FakeStream)
    s = mic(dev)
    s.start()
    assert (opened["samplerate"], opened["channels"]) in dev.supported
    # 块长约 50ms（16k 时为 800）
    assert abs(opened["blocksize"] / opened["samplerate"] - 0.05) < 0.001
    s.stop()


# ---------------------------------------------------------------- FR-AUD-03 回调输出

FORMATS = [(16000, 1), (48000, 2), (44100, 2), (44100, 1), (32000, 1), (48000, 4), (96000, 2), (22050, 1), (8000, 1)]


@pytest.mark.parametrize("sr, ch", FORMATS, ids=[f"{sr}x{ch}" for sr, ch in FORMATS])
def test_fr_aud_03_callback_output_format(mic, sr, ch):
    s = mic(DEVICES["native_16k_mono"])
    block = int(round(sr * 0.05))
    t = np.arange(sr) / sr
    mono = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio = np.repeat(mono[:, None], ch, axis=1)
    chunks = _feed(s, audio, sr, block)
    assert chunks, "回调没有投递任何样本"
    for c in chunks:
        assert c.dtype == np.float32 and c.ndim == 1
        assert abs(len(c) - block * TARGET_SR / sr) <= 1
    total = sum(len(c) for c in chunks)
    assert abs(total - (len(audio) // block) * block * TARGET_SR / sr) <= len(chunks)
    # 声道一致时下混后电平不变（IMPL-02）
    rms = float(np.sqrt(np.mean(np.concatenate(chunks) ** 2)))
    assert rms == pytest.approx(0.3 / np.sqrt(2), rel=0.05)


@pytest.mark.xfail(strict=True, raises=ValueError, reason="DEF-203")
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(n=st.integers(1, 4096), sr=st.sampled_from([48000, 44100, 32000, 22050, 96000, 8000]), ch=st.sampled_from([1, 2]))
def test_fr_aud_03_callback_any_block_size(mic, n, sr, ch):
    """PortAudio 在未指定块长时（start() 的回退分支）块长可变；任何块长都不能让回调抛异常"""
    s = mic(DEVICES["native_16k_mono"])
    s.actual_sample_rate = sr
    s._audio_callback(np.zeros((n, ch), dtype=np.float32), n, None, None)
    for c in _drain(s.queue):
        assert np.all(np.isfinite(c))


# ---------------------------------------------------------------- FR-AUD-05 麦克风增益

def _sine16k(amp, seconds=1.0, f=440):
    t = np.arange(int(TARGET_SR * seconds)) / TARGET_SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)[:, None]


@pytest.mark.parametrize("gain", [0.5, 1.0, 2.0, 3.0])
def test_fr_aud_05_gain_scales_level(mic, gain):
    s = mic(DEVICES["native_16k_mono"], gain=gain)
    x = _sine16k(0.2)
    y = np.concatenate(_feed(s, x, TARGET_SR, 800))
    ratio = np.sqrt(np.mean(y ** 2)) / np.sqrt(np.mean(x ** 2))
    assert ratio == pytest.approx(gain, rel=0.01)


def test_fr_aud_05_gain_one_is_identity(mic):
    s = mic(DEVICES["native_16k_mono"], gain=1.0)
    x = np.random.default_rng(0).uniform(-1, 1, (8000, 1)).astype(np.float32)
    y = np.concatenate(_feed(s, x, TARGET_SR, 800))
    np.testing.assert_array_equal(y, x[:, 0])


def test_fr_aud_05_level_monotonic_in_gain(mic):
    x = _sine16k(0.6)  # 高增益下会饱和
    levels = []
    for g in (0.5, 1.0, 1.5, 2.0, 3.0):
        y = np.concatenate(_feed(mic(DEVICES["native_16k_mono"], gain=g), x, TARGET_SR, 800))
        levels.append(float(np.sqrt(np.mean(y ** 2))))
    assert levels == sorted(levels), levels


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(samples=st.lists(st.one_of(st.floats(-1, 1, width=32), st.just(float("nan"))), min_size=1, max_size=800),
       gain=st.floats(0.5, 3.0))
def test_fr_aud_05_high_gain_no_crash_bounded(mic, samples, gain):
    s = mic(DEVICES["native_16k_mono"], gain=gain)
    x = np.array(samples, dtype=np.float32)[:, None]
    s._audio_callback(x, len(x), None, None)
    for c in _drain(s.queue):
        assert np.all(np.isfinite(c))
        if gain != 1.0:
            assert np.all(np.abs(c) <= 1.0)
    assert 0.0 <= s.current_level <= 1.0


def test_impl_05_non_finite_input_becomes_finite(mic):
    s = mic(DEVICES["native_16k_mono"], gain=2.0)
    x = np.array([np.nan, np.inf, -np.inf, 0.5], dtype=np.float32)[:, None]
    s._audio_callback(x, 4, None, None)
    (c,) = _drain(s.queue)
    assert np.all(np.isfinite(c))


def test_fr_aud_05_runtime_gain_change(mic, monkeypatch):
    """FR-AUD-06：运行中经流水线调整增益，下一块即生效"""
    from yukkuri.config import AppConfig
    from yukkuri.pipeline import YukkuriPipeline

    monkeypatch.setattr(capture_mod.sd, "check_input_settings", lambda **kw: None)
    p = YukkuriPipeline(AppConfig(mic_gain=1.0), asr=mock.Mock(), tts=mock.Mock(), loopback_manager=mock.Mock())
    try:
        x = _sine16k(0.1, seconds=0.05)
        before = np.concatenate(_feed(p.mic_stream, x, TARGET_SR, 800))
        p.set_mic_gain(2.5)
        after = np.concatenate(_feed(p.mic_stream, x, TARGET_SR, 800))
        assert np.sqrt(np.mean(after ** 2)) / np.sqrt(np.mean(before ** 2)) == pytest.approx(2.5, rel=0.01)
    finally:
        p.player.stop()
