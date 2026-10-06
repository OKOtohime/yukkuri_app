"""WP-02 L2 用例的公共工具（不被 pytest 收集）"""

import csv
import os
import re

import numpy as np
import soundfile as sf

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPORA_DIR = os.path.join(QA_DIR, "data", "corpora")
MANIFEST_DIR = os.path.join(QA_DIR, "data", "manifests")
REPORT_DIR = os.path.join(QA_DIR, "reports", "wp02")

# 默认 CLI 参数下的 VAD 配置（AppConfig 默认值）
VAD_DEFAULTS = dict(min_silence_duration=0.3, min_speech_duration=0.15, threshold=0.5)


def load_manifest(name):
    with open(os.path.join(MANIFEST_DIR, name), encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def require_audio(rels):
    """缺少本地语料文件时 skip（W1 只同步了部分语料）"""
    import pytest
    missing = [r for r in rels if not os.path.isfile(os.path.join(CORPORA_DIR, r))]
    if missing:
        pytest.skip(f"缺少评测语料 {len(missing)} 个文件，例如 {missing[0]}")


def read_wav(rel):
    x, sr = sf.read(os.path.join(CORPORA_DIR, rel), dtype="float32")
    assert sr == 16000 and x.ndim == 1
    return x


def fft_resample(x, orig_sr, target_sr):
    """测试侧参考重采样：频域补零/截断，带限且与被测实现无关"""
    n_out = int(round(len(x) * target_sr / orig_sr))
    spec = np.fft.rfft(x.astype(np.float64))
    out_bins = n_out // 2 + 1
    new = np.zeros(out_bins, dtype=complex)
    k = min(len(spec), out_bins)
    new[:k] = spec[:k]
    return (np.fft.irfft(new, n_out) * (n_out / len(x))).astype(np.float32)


def stream_vad(vad, x, block=800):
    segs = []
    for i in range(0, len(x), block):
        segs += vad.accept_waveform(x[i:i + block])
    segs += vad.flush()
    return segs


_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_zh(text):
    """去标点和空白；阿拉伯数字转汉字（与 5.1.1 节约定一致的最小实现）"""
    import cn2an
    text = re.sub(r"<\|[^|]*\|>", "", text)
    try:
        text = cn2an.transform(text, "an2cn")
    except Exception:
        pass
    return _PUNCT.sub("", text)


def cer(ref, hyp):
    import jiwer
    ref, hyp = normalize_zh(ref), normalize_zh(hyp)
    if not ref:
        return 0.0 if not hyp else 1.0
    return jiwer.cer(ref, hyp)


def edit_counts(ref, hyp):
    """返回 (编辑距离, 参考长度)，用于语料级 CER"""
    import jiwer
    ref, hyp = normalize_zh(ref), normalize_zh(hyp)
    out = jiwer.process_characters(ref, hyp)
    return out.substitutions + out.deletions + out.insertions, len(ref)
