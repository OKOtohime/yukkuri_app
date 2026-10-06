#!/usr/bin/env python3
"""
中英混说语料 TTS 合成子集生成（需求 5.1.1 / QR-FUNC-05）

- 运行于独立工具环境（~/.cache/yukkuri-qa/ttsenv：edge-tts, sherpa-onnx, soundfile, numpy），不污染被测 venv
- 2 种引擎（edge-tts 在线、sherpa-onnx Kokoro 本地），合计 8 个音色，按句轮换
- 每句叠加合成房间混响（RT60 0.3~0.6s）与 MUSAN 噪声（SNR 15~25dB），参数写入清单
- 输出: qa/data/corpora/mixed_tts/<id>.wav（16k 单声道），清单 qa/data/manifests/mixed_tts.tsv

用法: ~/.cache/yukkuri-qa/ttsenv/bin/python qa/eval/asr/gen_mixed_tts.py
"""

import asyncio
import csv
import os
import random
import subprocess
import tempfile

import numpy as np
import soundfile as sf

SEED = 20261007
QA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(QA_DIR, "data", "corpora", "mixed_tts")
MANIFEST = os.path.join(QA_DIR, "data", "manifests", "mixed_tts.tsv")
SCRIPT = os.path.join(QA_DIR, "data", "mixed_zh_en_script.tsv")
NOISE_MANIFEST = os.path.join(QA_DIR, "data", "manifests", "musan_noise.tsv")
KOKORO_DIR = os.path.expanduser("~/.cache/yukkuri-qa/tts/kokoro-multi-lang-v1_0")

EDGE_VOICES = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-XiaoyiNeural"]
# 与 edge-tts 音色名错开，增加多样性；ID 取自 model.onnx 元数据 speaker2id
KOKORO_VOICES = {"zf_xiaobei": 45, "zf_xiaoni": 46, "zm_yunxia": 51, "zm_yunyang": 52}
SR = 16000


def load_kokoro():
    import sherpa_onnx
    d = KOKORO_DIR
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=f"{d}/model.onnx", voices=f"{d}/voices.bin", tokens=f"{d}/tokens.txt",
                data_dir=f"{d}/espeak-ng-data", dict_dir=f"{d}/dict",
                lexicon=f"{d}/lexicon-us-en.txt,{d}/lexicon-zh.txt",
            ),
            num_threads=4,
        ),
    )
    return sherpa_onnx.OfflineTts(cfg)


def resample_ffmpeg(src: str, dst: str):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src,
                    "-ac", "1", "-ar", str(SR), dst], check=True)


async def edge_synth(text: str, voice: str, dst_wav: str):
    import edge_tts
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        mp3 = tmp.name
    await edge_tts.Communicate(text, voice).save(mp3)
    resample_ffmpeg(mp3, dst_wav)
    os.remove(mp3)


def kokoro_synth(tts, sid: int, text: str, dst_wav: str):
    audio = tts.generate(text, sid=sid, speed=1.0)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        raw = tmp.name
    sf.write(raw, np.asarray(audio.samples, dtype=np.float32), audio.sample_rate)
    resample_ffmpeg(raw, dst_wav)
    os.remove(raw)


def augment(x: np.ndarray, rng: random.Random, noise_pool):
    rt60 = rng.uniform(0.3, 0.6)
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    nrng = np.random.default_rng(rng.randrange(1 << 30))
    ir = nrng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    ir[0] = 1.0
    ir /= np.sqrt(np.sum(ir ** 2))
    y = np.convolve(x, ir)[: len(x)]
    noise_path, _ = rng.choice(noise_pool)
    noise, _ = sf.read(noise_path, dtype="float32")
    if len(noise) < len(y):
        noise = np.tile(noise, int(np.ceil(len(y) / len(noise))))
    off = rng.randrange(0, len(noise) - len(y) + 1)
    noise = noise[off: off + len(y)]
    snr = rng.uniform(15, 25)
    ps, pn = np.mean(y ** 2) + 1e-12, np.mean(noise ** 2) + 1e-12
    y = y + noise * np.sqrt(ps / (pn * 10 ** (snr / 10)))
    peak = np.max(np.abs(y))
    if peak > 0.99:
        y = y * (0.99 / peak)
    return y.astype(np.float32), rt60, os.path.basename(noise_path), snr


def main():
    with open(SCRIPT, encoding="utf-8") as f:
        items = [r for r in csv.DictReader(f, delimiter="\t") if r["use"] == "tts"]
    with open(NOISE_MANIFEST, encoding="utf-8") as f:
        corpora = os.path.join(QA_DIR, "data", "corpora")
        noise_pool = [(os.path.join(corpora, r["wav"]), r["id"]) for r in csv.DictReader(f, delimiter="\t")]
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(SEED)
    tts = load_kokoro()
    kokoro_names = list(KOKORO_VOICES)
    rows = []
    for i, it in enumerate(items):
        if i % 2 == 0:
            engine, voice = "edge-tts", EDGE_VOICES[(i // 2) % len(EDGE_VOICES)]
        else:
            engine, voice = "kokoro-v1.0", kokoro_names[(i // 2) % len(kokoro_names)]
        clean = os.path.join(OUT_DIR, f"{it['id']}.clean.wav")
        if not os.path.exists(clean):
            if engine == "edge-tts":
                asyncio.run(edge_synth(it["text"], voice, clean))
            else:
                kokoro_synth(tts, KOKORO_VOICES[voice], it["text"], clean)
        x, _ = sf.read(clean, dtype="float32")
        y, rt60, noise_id, snr = augment(x, rng, noise_pool)
        dst = os.path.join(OUT_DIR, f"{it['id']}.wav")
        sf.write(dst, y, SR, subtype="PCM_16")
        rows.append([it["id"], os.path.join("mixed_tts", f"{it['id']}.wav"), f"{len(y) / SR:.3f}",
                     it["text"], "tts", engine, voice, f"{rt60:.2f}", noise_id, f"{snr:.1f}"])
        print(f"{it['id']} {engine}/{voice} rt60={rt60:.2f} snr={snr:.1f}")
    with open(MANIFEST, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["id", "wav", "duration", "text", "source", "engine", "voice", "rt60", "noise", "snr_db"])
        w.writerows(rows)
    print(f"[manifest] {MANIFEST}: {len(rows)} 条")


if __name__ == "__main__":
    main()
