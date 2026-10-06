"""
WP-02 L2：FR-VAD-06 极短非语音声响不触发合成（评审结论 #2：误触发率 ≤ 5%）

样本（固定种子 20261007，共 200 条）：
- click：5ms 衰减脉冲，不同幅度（40 条）
- keyboard：1~4 次 30~80ms 噪声突发，间隔 80~200ms（60 条）
- musan：MUSAN noise 中随机截取 0.1~0.5s（100 条）
每条前后各补静音（-60 dBFS 白噪声底），按 CLI 默认参数经 SileroVAD（800 样本块）→
流水线最短时长过滤（与 process_segment 相同的条件）→ SenseVoice → G2P。
"误触发" = 产生可发声的音标（去掉标点后非空）。只有标点的音标（如 "." → "。"）在 R1 上实测合成为
32ms 全零 WAV，听不到，单独计数（交 WP-03 按 FR-G2P-13 评估），不算误触发。
"""

import json
import os
import re

import numpy as np
import pytest

from wp02_util import REPORT_DIR, VAD_DEFAULTS, load_manifest, read_wav, require_audio, stream_vad

pytestmark = [pytest.mark.l2, pytest.mark.requires_models, pytest.mark.requires_corpora]

SR = 16000
SEED = 20261007
THRESHOLD = 0.05
_SILENT_KOE = re.compile(r"^[\s。、？！?!,.・/'\"]*$")


def audible(koe):
    return bool(koe) and not _SILENT_KOE.match(koe)


def _embed(event, rng):
    pre = int(SR * 0.5)
    post = int(SR * 1.0)
    floor = rng.normal(0, 10 ** (-60 / 20), pre + len(event) + post).astype(np.float32)
    floor[pre:pre + len(event)] += event
    return floor


def _click(rng):
    n = int(SR * 0.005)
    amp = rng.uniform(0.1, 0.9)
    return (amp * np.exp(-np.arange(n) / (n / 5)) * rng.choice([-1, 1], n)).astype(np.float32)


def _keyboard(rng):
    parts = []
    for k in range(rng.integers(1, 5)):
        n = int(SR * rng.uniform(0.03, 0.08))
        burst = rng.normal(0, 1, n) * np.exp(-np.arange(n) / (n / 4))
        burst = np.convolve(burst, np.ones(3) / 3, mode="same")  # 稍作低通
        parts.append((rng.uniform(0.1, 0.5) * burst / np.max(np.abs(burst))).astype(np.float32))
        parts.append(np.zeros(int(SR * rng.uniform(0.08, 0.2)), dtype=np.float32))
    return np.concatenate(parts)


def _musan(rng, rows):
    while True:
        r = rows[rng.integers(len(rows))]
        x = read_wav(r["wav"])
        n = int(SR * rng.uniform(0.1, 0.5))
        if len(x) <= n:
            continue
        start = rng.integers(len(x) - n)
        seg = x[start:start + n].astype(np.float64)
        rms = np.sqrt(np.mean(seg ** 2))
        if rms < 1e-4:
            continue
        seg = seg * (10 ** (-20 / 20) / rms)  # -20 dBFS RMS
        fade = np.minimum(1, np.minimum(np.arange(n), np.arange(n)[::-1]) / (SR * 0.005))
        return np.clip(seg * fade, -1, 1).astype(np.float32), f"{r['id']}@{start / SR:.2f}s"


def make_samples():
    rng = np.random.default_rng(SEED)
    musan_rows = load_manifest("musan_noise.tsv")
    samples = [("click", _click(rng), "synthetic") for _ in range(40)]
    samples += [("keyboard", _keyboard(rng), "synthetic") for _ in range(60)]
    samples += [("musan", *_musan(rng, musan_rows)) for _ in range(100)]
    return [(kind, i, src, _embed(ev, rng), len(ev) / SR) for i, (kind, ev, src) in enumerate(samples)]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-208")
def test_fr_vad_06_transients_no_output():
    from yukkuri.asr.sensevoice import SenseVoiceASR
    from yukkuri.audio.vad import SileroVAD
    from yukkuri.config import AppConfig
    from yukkuri.g2p.polyglot import PolyglotG2P

    require_audio([r["wav"] for r in load_manifest("musan_noise.tsv")])
    cfg = AppConfig()
    asr = SenseVoiceASR(cfg.find_sensevoice_dir(), num_threads=4)
    g2p = PolyglotG2P()
    min_len = cfg.sample_rate * cfg.vad_min_sample_duration

    rows = []
    for kind, idx, src, audio, ev_dur in make_samples():
        vad = SileroVAD(cfg.find_vad_model(), max_speech_duration=cfg.vad_max_speech, **VAD_DEFAULTS)
        segs = stream_vad(vad, audio)
        passed = [s for s in segs if len(s) >= min_len]
        texts = [asr.decode(s, SR) for s in passed]
        koes = [g2p.convert(t, lang=cfg.lang) for t in texts if t]
        rows.append({"kind": kind, "idx": idx, "src": src, "event_s": round(ev_dur, 3), "vad_segments": len(segs),
                     "after_min_len": len(passed), "texts": texts, "koe": [k for k in koes if k],
                     "punct_only": any(k and not audible(k) for k in koes),
                     "triggered": any(audible(k) for k in koes)})

    by_kind = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {"n": 0, "vad": 0, "after_min_len": 0, "punct_only": 0, "triggered": 0})
        k["n"] += 1
        k["vad"] += r["vad_segments"] > 0
        k["after_min_len"] += r["after_min_len"] > 0
        k["punct_only"] += r["punct_only"] and not r["triggered"]
        k["triggered"] += r["triggered"]
    rate = sum(r["triggered"] for r in rows) / len(rows)
    summary = {"n": len(rows), "false_trigger_rate": round(rate, 4), "threshold": THRESHOLD, "by_kind": by_kind}
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "vad06_transients.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "samples": rows}, f, ensure_ascii=False, indent=1)
    print(summary)
    assert rate <= THRESHOLD, summary
