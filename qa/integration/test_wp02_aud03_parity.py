"""
WP-02 L2：FR-AUD-03 识别对等（评审结论 #1）

AISHELL-1 清单前 100 句。测试侧用频域方法（与被测实现无关）把 16k 原始音频升到设备采样率，
模拟 48k 立体声 / 44.1k 单声道麦克风；按 start() 计算的硬件块长（50ms）逐块送入
MicrophoneStream._audio_callback（被测的下混、增益、重采样路径），拼接输出后用 SenseVoice 整段解码。
基线：同一句 16k 原始音频直接解码。判定：语料级 CER 绝对增量 ≤ 2pp。
"""

import json
import os

import numpy as np
import pytest

from wp02_util import REPORT_DIR, edit_counts, fft_resample, load_manifest, read_wav, require_audio

pytestmark = [pytest.mark.l2, pytest.mark.requires_models, pytest.mark.requires_corpora]

N_UTT = 100
THRESHOLD_PP = 2.0


@pytest.fixture(scope="module")
def asr():
    from yukkuri.asr.sensevoice import SenseVoiceASR
    from yukkuri.config import AppConfig
    return SenseVoiceASR(AppConfig().find_sensevoice_dir(), num_threads=4)


@pytest.fixture(scope="module")
def utterances():
    rows = load_manifest("aishell1_test.tsv")[:N_UTT]
    require_audio([r["wav"] for r in rows])
    return [(r["id"], r["text"], read_wav(r["wav"])) for r in rows]


@pytest.fixture(scope="module")
def baseline(asr, utterances):
    return {uid: asr.decode(x, 16000) for uid, _, x in utterances}


def through_capture(x16k, device_sr, channels):
    from yukkuri.audio.capture import MicrophoneStream
    dev = fft_resample(x16k, 16000, device_sr)
    block = int(round(device_sr * 0.05))  # 与 MicrophoneStream.start() 相同
    dev = np.concatenate([dev, np.zeros(-len(dev) % block, dtype=np.float32)])
    frames = np.repeat(dev[:, None], channels, axis=1)
    mic = MicrophoneStream(sample_rate=16000, channels=1)
    mic.actual_sample_rate, mic.actual_channels = device_sr, channels
    for i in range(0, len(frames), block):
        mic._audio_callback(frames[i:i + block], block, None, None)
    out = []
    while not mic.queue.empty():
        out.append(mic.queue.get_nowait())
    return np.concatenate(out)


@pytest.mark.parametrize("device_sr, channels", [(48000, 2), (44100, 1)], ids=["48k-stereo", "44.1k-mono"])
def test_fr_aud_03_recognition_parity(asr, utterances, baseline, device_sr, channels):
    rows, e_base, e_dev, n_ref = [], 0, 0, 0
    for uid, ref, x in utterances:
        hyp_dev = asr.decode(through_capture(x, device_sr, channels), 16000)
        eb, n = edit_counts(ref, baseline[uid])
        ed, _ = edit_counts(ref, hyp_dev)
        e_base, e_dev, n_ref = e_base + eb, e_dev + ed, n_ref + n
        rows.append({"id": uid, "ref": ref, "hyp_16k": baseline[uid], "hyp_device": hyp_dev,
                     "err_16k": eb, "err_device": ed, "ref_len": n})
    cer_base, cer_dev = 100 * e_base / n_ref, 100 * e_dev / n_ref
    summary = {"device": f"{device_sr}Hz/{channels}ch", "n_utt": len(rows), "ref_chars": n_ref,
               "cer_16k_pct": round(cer_base, 2), "cer_device_pct": round(cer_dev, 2),
               "delta_pp": round(cer_dev - cer_base, 2), "threshold_pp": THRESHOLD_PP,
               "utt_worse": sum(r["err_device"] > r["err_16k"] for r in rows),
               "utt_better": sum(r["err_device"] < r["err_16k"] for r in rows)}
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, f"aud03_parity_{device_sr}_{channels}ch.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "utterances": rows}, f, ensure_ascii=False, indent=1)
    print(summary)
    assert cer_dev - cer_base <= THRESHOLD_PP, summary
