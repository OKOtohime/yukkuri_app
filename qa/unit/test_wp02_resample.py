"""
WP-02 L1：重采样（FR-AUD-03；IMPL-04 作为线索）

默认安装不带 scipy（不在 pyproject 依赖中），用户实际走的是纯 NumPy 分支。
本文件同时覆盖当前环境实际可用的分支；scipy 分支只在装了 scipy 时额外参数化。
"""

import json
import os

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import yukkuri.audio.resample as rs

pytestmark = pytest.mark.l1

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "wp02")

BRANCHES = ["numpy"] + (["scipy"] if rs._HAS_SCIPY else [])


@pytest.fixture(params=BRANCHES)
def branch(request, monkeypatch):
    monkeypatch.setattr(rs, "_HAS_SCIPY", request.param == "scipy")
    return request.param


def _sine(f, sr, seconds=1.0, amp=0.5):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def sinad_db(x, sr, f0):
    """去掉首尾各 1/8 后加 Hann 窗，信号能量取 f0 附近 ±3 个频点"""
    x = x[len(x) // 8: -len(x) // 8].astype(np.float64)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    k = int(np.argmin(np.abs(np.fft.rfftfreq(len(x), 1 / sr) - f0)))
    sig = spec[k - 3:k + 4].sum()
    return float(10 * np.log10(sig / (spec[1:].sum() - sig)))


@pytest.mark.xfail(strict=True, raises=(ValueError, AssertionError), reason="DEF-203")
@settings(max_examples=300, deadline=None, report_multiple_bugs=False)
@given(n=st.integers(0, 3000),
       rates=st.sampled_from([(48000, 16000), (44100, 16000), (32000, 16000), (22050, 16000), (96000, 16000),
                              (8000, 16000), (8000, 48000), (8000, 44100), (16000, 48000)]),
       stereo=st.booleans())
def test_fr_aud_03_resample_any_length(n, rates, stereo):
    orig, target = rates
    x = np.zeros((n, 2) if stereo else n, dtype=np.float32)
    y = rs.resample_audio(x, orig, target)
    assert y.shape[1:] == x.shape[1:]
    assert abs(len(y) - n * target / orig) <= 1


@pytest.mark.parametrize("dtype", [np.float32, np.int16])
def test_fr_aud_03_resample_preserves_dtype(branch, dtype):
    x = (_sine(440, 48000) * (20000 if dtype is np.int16 else 1)).astype(dtype)
    assert rs.resample_audio(x, 48000, 16000).dtype == dtype


@pytest.mark.parametrize("f", [
    200, 1000,
    pytest.param(3000, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason="OBS-201 升采样通带衰减")),
    pytest.param(6000, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason="OBS-201 升采样通带衰减")),
])
def test_fr_aud_03_resample_roundtrip_energy(branch, f):
    """蜕变（测试计划 4.2）：16k→48k→16k 后带内信号能量保持（±0.5 dB）"""
    x = _sine(f, 16000)
    y = rs.resample_audio(rs.resample_audio(x, 16000, 48000), 48000, 16000)
    core = slice(len(x) // 8, -len(x) // 8)
    ratio_db = 10 * np.log10(np.mean(y[core] ** 2) / np.mean(x[core] ** 2))
    assert abs(ratio_db) <= 0.5


@pytest.mark.parametrize("orig", [48000, 44100, 32000])
@pytest.mark.parametrize("f", [300, 1000, 2000, 3000, 4000, 5000])
def test_fr_aud_03_downsample_passband_flat(branch, orig, f):
    """采集路径（设备采样率 → 16k）在语音主频带内平坦：≤ 1 dB"""
    y = rs.resample_audio(_sine(f, orig, amp=1.0), orig, 16000)
    core = y[len(y) // 8:-len(y) // 8]
    assert abs(20 * np.log10(np.sqrt(2 * np.mean(core.astype(float) ** 2)))) <= 1.0


def _response_db(orig, target, freqs):
    out = {}
    for f in freqs:
        y = rs.resample_audio(_sine(f, orig, amp=1.0), orig, target)
        core = y[len(y) // 8:-len(y) // 8]
        out[f] = round(float(20 * np.log10(np.sqrt(2 * np.mean(core.astype(float) ** 2)))), 1)
    return out


def test_obs_201_upsample_response_measurement(branch):
    """测量：AquesTalk 8k → Windows 输出设备常见采样率的幅频响应（Windows 播放器使用该路径）。
    只记录（OBS-201，交 WP-11 结合 FR-OUT-04 评估）"""
    freqs = [300, 1000, 2000, 3000, 3500]
    data = {f"8000->{t}": _response_db(8000, t, freqs) for t in (48000, 44100)}
    data["16000->48000"] = _response_db(16000, 48000, [300, 1000, 2000, 3000, 4000, 6000, 7000])
    data["48000->16000"] = _response_db(48000, 16000, [300, 1000, 2000, 3000, 4000, 5000, 6000, 7000])
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, f"resample_response_{branch}.json"), "w") as fp:
        json.dump(data, fp, indent=1)
    assert data["8000->48000"][300] > -1.0


@pytest.mark.parametrize("orig", [48000, 44100, 32000])
def test_fr_aud_03_resample_anti_alias(branch, orig):
    """带外（> 8 kHz）成分降到 16k 后不应以可观能量折回带内：≥ 20 dB 衰减"""
    x_out = _sine(min(0.45 * orig, 12000), orig)
    x_in = _sine(1000, orig)
    y_out = rs.resample_audio(x_out, orig, 16000)
    y_in = rs.resample_audio(x_in, orig, 16000)
    assert 10 * np.log10(np.mean(y_in ** 2) / max(np.mean(y_out ** 2), 1e-20)) >= 20


def test_fr_aud_03_streaming_resample_fidelity(branch):
    """测量：50ms 块逐块重采样（采集回调的实际做法）与整段重采样的 SINAD。只记录不判定，
    结果写入 qa/reports/wp02/resample_fidelity_<branch>.json，供 recognition_parity 解释"""
    rows = []
    for orig in (48000, 44100, 32000):
        block = int(round(orig * 0.05))
        for f in (300, 1000, 3000):
            x = _sine(f, orig, seconds=2.0)
            whole = rs.resample_audio(x, orig, 16000)
            blocks = np.concatenate([rs.resample_audio(x[i:i + block], orig, 16000)
                                     for i in range(0, len(x) - block + 1, block)])
            rows.append({"orig_sr": orig, "freq": f,
                         "sinad_whole_db": round(sinad_db(whole, 16000, f), 1),
                         "sinad_blockwise_db": round(sinad_db(blocks, 16000, f), 1)})
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, f"resample_fidelity_{branch}.json"), "w") as fp:
        json.dump(rows, fp, indent=1)
    assert all(r["sinad_whole_db"] > 30 for r in rows)
