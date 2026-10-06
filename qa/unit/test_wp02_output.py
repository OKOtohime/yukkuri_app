"""
WP-02 L1：输出增益与播放顺序（FR-OUT-07、FR-OUT-09；IMPL-17 作为线索）

Linux 播放器在 subprocess.Popen 边界上替换为桩；Windows 播放器在输出流边界上替换为桩。
两种实现在任一平台上都可以实例化（只在播放时访问设备）。
"""

import io
import threading
import time
import wave

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import yukkuri.audio.player as player_mod
from yukkuri.audio.player import LinuxPipeWirePlayer, WindowsWASAPIPlayer, apply_wav_gain

pytestmark = pytest.mark.l1


def make_wav(samples, sr=8000, sampwidth=2, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sr)
        w.writeframes(np.asarray(samples).tobytes())
    return buf.getvalue()


def read_wav(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getparams()


def rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


SPEECH_LIKE = (np.random.default_rng(7).normal(0, 6000, 8000)).clip(-32768, 32767).astype(np.int16)


# ---------------------------------------------------------------- FR-OUT-07

def test_fr_out_07_gain_monotonic():
    levels = [rms(read_wav(apply_wav_gain(make_wav(SPEECH_LIKE), g))[0]) for g in (0.1, 0.5, 1.0, 2.0, 3.0)]
    assert levels == sorted(levels) and levels[0] < levels[-1], levels


@pytest.mark.parametrize("gain", [0.1, 0.5, 2.0])
def test_fr_out_07_gain_scales_level_unsaturated(gain):
    x = (SPEECH_LIKE // 8).astype(np.int16)  # 3.0 倍以内不饱和
    y = read_wav(apply_wav_gain(make_wav(x), gain))[0]
    assert rms(y) / rms(x) == pytest.approx(gain, rel=0.01)


def test_fr_out_07_gain_one_byte_identical():
    wav = make_wav(SPEECH_LIKE)
    assert apply_wav_gain(wav, 1.0) == wav


@settings(max_examples=300, deadline=None)
@given(samples=st.lists(st.integers(-32768, 32767), min_size=1, max_size=2000), gain=st.floats(0.1, 3.0))
def test_fr_out_07_no_wraparound(samples, gain):
    """满幅时削波，但不能回绕（不变号、不跳到另一端）"""
    x = np.array(samples, dtype=np.int16)
    y = read_wav(apply_wav_gain(make_wav(x), gain))[0].astype(np.int64)
    ideal = np.clip(x.astype(np.float64) * gain, -32768, 32767)
    assert np.max(np.abs(y - ideal)) <= 1
    assert np.all(np.sign(y) * np.sign(x) >= 0)


@settings(max_examples=200, deadline=None)
@given(a=st.floats(0.1, 1.7), b=st.floats(0.1, 1.7))
def test_fr_out_07_gain_composition(a, b):
    """蜕变：非饱和区 gain(a) 再 gain(b) ≈ gain(a·b)。两次各截断一次，误差 ≤ 2 LSB"""
    x = (SPEECH_LIKE // 12).astype(np.int16)
    two = read_wav(apply_wav_gain(apply_wav_gain(make_wav(x), a), b))[0].astype(int)
    one = read_wav(apply_wav_gain(make_wav(x), a * b))[0].astype(int)
    assert np.max(np.abs(two - one)) <= 2


@pytest.mark.parametrize("sr, ch", [(8000, 1), (16000, 1), (48000, 2)])
def test_fr_out_07_wav_header_preserved(sr, ch):
    wav = make_wav(np.tile(SPEECH_LIKE[:1000, None], (1, ch)), sr=sr, channels=ch)
    _, p_in = read_wav(wav)
    _, p_out = read_wav(apply_wav_gain(wav, 2.0))
    assert p_out == p_in


def test_impl_17_non_16bit_unchanged():
    wav8 = make_wav(np.array([10, 128, 250], dtype=np.uint8), sampwidth=1)
    assert apply_wav_gain(wav8, 2.0) == wav8


class _RecordingPopen:
    """pw-play/paplay 的桩：记录命令、标准输入和起止时间，每次播放耗时 delay 秒"""
    log = []
    lock = threading.Lock()
    delay = 0.0

    def __init__(self, cmd, stdin=None, stdout=None, stderr=None):
        self.cmd = cmd
        self.returncode = 0

    def communicate(self, input=None):
        start = time.monotonic()
        time.sleep(self.delay)
        with self.lock:
            self.log.append({"cmd": self.cmd, "data": input, "start": start, "end": time.monotonic()})
        return b"", b""


@pytest.fixture
def linux_player(monkeypatch):
    _RecordingPopen.log = []
    _RecordingPopen.delay = 0.0
    monkeypatch.setattr(player_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(player_mod.subprocess, "Popen", _RecordingPopen)
    monkeypatch.setattr(LinuxPipeWirePlayer, "_is_sink_available", lambda self: True)
    players = []

    def make(**kw):
        p = LinuxPipeWirePlayer(**kw)
        players.append(p)
        return p

    yield make
    for p in players:
        p.stop()


def test_fr_out_07_linux_player_applies_gain(linux_player):
    p = linux_player(target_sink="yk_sink", gain=2.0)
    wav = make_wav((SPEECH_LIKE // 8).astype(np.int16))
    p.play(wav)
    p.queue.join()
    (call,) = _RecordingPopen.log
    assert call["cmd"][:3] == ["pw-play", "--target", "yk_sink"]
    assert call["data"] == apply_wav_gain(wav, 2.0)


def test_fr_out_07_linux_player_runtime_gain(linux_player):
    """FR-OUT-08 的前提：set_gain 后下一句使用新增益"""
    p = linux_player(target_sink="yk_sink", gain=1.0)
    wav = make_wav((SPEECH_LIKE // 8).astype(np.int16))
    p.play(wav)
    p.set_gain(0.5)
    p.play(wav)
    p.queue.join()
    assert [c["data"] for c in _RecordingPopen.log] == [wav, apply_wav_gain(wav, 0.5)]


def test_fr_out_07_windows_player_applies_gain(monkeypatch):
    played = []
    monkeypatch.setattr(WindowsWASAPIPlayer, "_play_streams", lambda self, data: played.append(data))
    p = WindowsWASAPIPlayer(output_device=1, gain=3.0)
    try:
        wav = make_wav(SPEECH_LIKE)
        p.play(wav)
        p.queue.join()
        assert played == [apply_wav_gain(wav, 3.0)]
    finally:
        p.stop()


# ---------------------------------------------------------------- FR-OUT-09

def _tagged_wav(tag: int, n=400):
    return make_wav(np.full(n, tag, dtype=np.int16))


def _tag_of(data: bytes) -> int:
    return int(read_wav(data)[0][0])


def test_fr_out_09_linux_sequential_in_order(linux_player):
    _RecordingPopen.delay = 0.05
    p = linux_player(target_sink="yk_sink")
    for i in range(1, 11):
        p.play(_tagged_wav(i))
    p.queue.join()
    log = _RecordingPopen.log
    assert [_tag_of(c["data"]) for c in log] == list(range(1, 11))
    for prev, nxt in zip(log, log[1:]):
        assert nxt["start"] >= prev["end"], "两句播放时间重叠"


def test_fr_out_09_concurrent_producers(linux_player):
    """识别线程与文字播报同时投递：每句完整播放一次，不重叠，各自的先后顺序保持"""
    _RecordingPopen.delay = 0.01
    p = linux_player(target_sink="yk_sink")

    def producer(base):
        for i in range(20):
            p.play(_tagged_wav(base + i))
            time.sleep(0.002)

    threads = [threading.Thread(target=producer, args=(b,)) for b in (100, 200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    p.queue.join()
    tags = [_tag_of(c["data"]) for c in _RecordingPopen.log]
    assert sorted(tags) == list(range(100, 120)) + list(range(200, 220))
    assert [t for t in tags if t < 200] == list(range(100, 120))
    assert [t for t in tags if t >= 200] == list(range(200, 220))
    log = sorted(_RecordingPopen.log, key=lambda c: c["start"])
    for prev, nxt in zip(log, log[1:]):
        assert nxt["start"] >= prev["end"]


class _FakeOutStream:
    def __init__(self):
        self.writes = []
        self.active = True

    def write(self, chunk):
        self.writes.append(np.array(chunk, copy=True))

    def stop(self):
        pass

    def close(self):
        pass


def test_fr_out_09_windows_sequential_in_order(monkeypatch):
    stream = _FakeOutStream()
    monkeypatch.setattr(WindowsWASAPIPlayer, "_get_or_create_stream", lambda self, *a: stream)
    monkeypatch.setattr(WindowsWASAPIPlayer, "_prepare_audio_for_device",
                        staticmethod(lambda mono, sr, dev: (mono, sr, 1)))
    p = WindowsWASAPIPlayer(output_device=1)
    try:
        for i in range(1, 11):
            p.play(_tagged_wav(i, n=1500))  # 每句跨 3 个写入块
        p.queue.join()
    finally:
        p.stop()
    order = [int(w[0]) for w in stream.writes]
    # 每句的块连续出现，句与句之间按投递顺序
    collapsed = [t for i, t in enumerate(order) if i == 0 or order[i - 1] != t]
    assert collapsed == list(range(1, 11))
    assert sum(len(w) for w in stream.writes) == 10 * 1500
