#!/usr/bin/env python3
"""
评测语料抽样与标准化（WP-01 / WP-07 数据准备）

- 原始下载位于 RAW_DIR（默认 ~/.cache/yukkuri-qa/raw）与 SRC_DIR（~/.cache/yukkuri-qa/corpora）
- 输出 16kHz 单声道 PCM16 wav 到 qa/data/corpora/<set>/（不入库）
- 清单写到 qa/data/manifests/<set>.tsv（入库）：id, wav（相对 qa/data/corpora）, duration, text
- 重采样使用 ffmpeg，刻意不复用被测代码的重采样实现，保证测试预言独立

用法:
  prepare_corpora.py aishell | librispeech | cv_ja | musan | inject | all
"""

import argparse
import csv
import glob
import os
import random
import subprocess
import sys
import tarfile

import soundfile as sf

SEED = 20261007
N_PER_LANG = 500

QA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(QA_DIR, "data", "corpora")
MANIFEST_DIR = os.path.join(QA_DIR, "data", "manifests")
CACHE = os.path.expanduser(os.environ.get("YUKKURI_QA_CACHE", "~/.cache/yukkuri-qa"))
RAW_DIR = os.path.join(CACHE, "raw")
SRC_DIR = os.path.join(CACHE, "corpora")


def to_wav16k(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        return
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src,
         "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", dst],
        check=True,
    )


def write_manifest(name: str, rows):
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    path = os.path.join(MANIFEST_DIR, f"{name}.tsv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["id", "wav", "duration", "text"])
        for r in rows:
            w.writerow([r["id"], r["wav"], f"{r['duration']:.3f}", r["text"]])
    print(f"[manifest] {path}: {len(rows)} 条，总时长 {sum(r['duration'] for r in rows) / 60:.1f} 分钟")


def finalize(set_name: str, picked):
    """picked: list of (id, src_path, text) -> 转换并写清单"""
    rows = []
    for uid, src, text in picked:
        rel = os.path.join(set_name, f"{uid}.wav")
        dst = os.path.join(OUT_DIR, rel)
        to_wav16k(src, dst)
        rows.append({"id": uid, "wav": rel, "duration": sf.info(dst).duration, "text": text})
    write_manifest(set_name, rows)


def prep_aishell():
    """从官方 data_aishell.tgz 流式解出 test 集说话人（不展开全部 15GB）"""
    tgz = os.path.join(RAW_DIR, "data_aishell.tgz")
    src = os.path.join(SRC_DIR, "aishell1")
    test_dir = os.path.join(src, "wav", "test")
    trans_path = os.path.join(src, "aishell_transcript_v0.8.txt")
    if not (os.path.isdir(test_dir) and os.path.exists(trans_path)):
        os.makedirs(src, exist_ok=True)
        with tarfile.open(tgz, "r|gz") as outer:
            for m in outer:
                if m.name.endswith("aishell_transcript_v0.8.txt"):
                    with open(trans_path, "wb") as f:
                        f.write(outer.extractfile(m).read())
                elif m.name.endswith(".tar.gz") and "/wav/" in m.name:
                    with tarfile.open(fileobj=outer.extractfile(m), mode="r|gz") as inner:
                        for im in inner:
                            if not im.name.startswith("test/") or not im.isfile():
                                if not im.name.startswith("test/"):
                                    break  # 每个说话人包只属于一个 split
                                continue
                            dst = os.path.join(src, "wav", im.name)
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            with open(dst, "wb") as f:
                                f.write(inner.extractfile(im).read())
    trans = {}
    with open(trans_path, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                trans[parts[0]] = parts[1].replace(" ", "")
    wavs = sorted(glob.glob(os.path.join(test_dir, "*", "*.wav")))
    cands = [(os.path.basename(w)[:-4], w) for w in wavs if os.path.basename(w)[:-4] in trans]
    print(f"[aishell] test 集可用 {len(cands)} 句")
    picked = random.Random(SEED).sample(cands, N_PER_LANG)
    finalize("aishell1_test", [(u, w, trans[u]) for u, w in sorted(picked)])


def prep_librispeech():
    root = os.path.join(SRC_DIR, "librispeech", "LibriSpeech", "test-clean")
    cands = []
    for tf in sorted(glob.glob(os.path.join(root, "*", "*", "*.trans.txt"))):
        d = os.path.dirname(tf)
        with open(tf, encoding="utf-8") as f:
            for line in f:
                uid, text = line.strip().split(" ", 1)
                cands.append((uid, os.path.join(d, uid + ".flac"), text.lower()))
    print(f"[librispeech] test-clean 可用 {len(cands)} 句")
    picked = random.Random(SEED).sample(cands, N_PER_LANG)
    finalize("librispeech_test_clean", sorted(picked))


def prep_cv_ja():
    root = os.path.join(SRC_DIR, "cv21_ja_test")
    cands = []
    with open(os.path.join(root, "test.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            p = os.path.join(root, r["path"])
            if os.path.exists(p) and int(r["down_votes"] or 0) == 0:
                cands.append((r["path"][:-4], p, r["sentence"].strip()))
    print(f"[cv_ja] test 可用 {len(cands)} 句（已排除 down_votes > 0）")
    picked = random.Random(SEED).sample(cands, N_PER_LANG)
    finalize("cv21_ja_test", sorted(picked))


def prep_musan():
    root = os.path.join(SRC_DIR, "musan")
    wavs = sorted(glob.glob(os.path.join(root, "**", "*.wav"), recursive=True))
    print(f"[musan] noise 共 {len(wavs)} 条，全部转换")
    finalize("musan_noise", [(os.path.basename(w)[:-4], w, "") for w in wavs])


def prep_inject():
    """系统测试注入集：从三语清单中选取（只引用已有 wav，不复制）"""
    rng = random.Random(SEED + 1)

    def load(name):
        with open(os.path.join(MANIFEST_DIR, f"{name}.tsv"), encoding="utf-8") as f:
            return [dict(r, lang=lang) for r in csv.DictReader(f, delimiter="\t")
                    for lang in [name.split("_")[0]]]

    pools = {"zh": load("aishell1_test"), "en": load("librispeech_test_clean"), "ja": load("cv21_ja_test")}
    rows = []
    for lang, n in (("zh", 40), ("en", 30), ("ja", 30)):
        normal = [r for r in pools[lang] if 1.0 <= float(r["duration"]) <= 6.0]
        rows += [dict(r, category="normal", lang=lang) for r in rng.sample(normal, n)]
    longs = [dict(r, category="long", lang=k) for k, v in pools.items() for r in v if float(r["duration"]) > 8.0]
    rows += rng.sample(longs, min(10, len(longs)))
    with open(os.path.join(MANIFEST_DIR, "musan_noise.tsv"), encoding="utf-8") as f:
        noise = list(csv.DictReader(f, delimiter="\t"))
    rows += [dict(r, category="noise_only", lang="-", text="") for r in rng.sample(noise, 5)]
    path = os.path.join(MANIFEST_DIR, "system_inject.tsv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["id", "wav", "duration", "lang", "category", "text"])
        for r in rows:
            w.writerow([r["id"], r["wav"], r["duration"], r["lang"], r["category"], r["text"]])
    print(f"[manifest] {path}: {len(rows)} 条（静音片段由测试工具运行时生成）")


STEPS = {
    "aishell": prep_aishell,
    "librispeech": prep_librispeech,
    "cv_ja": prep_cv_ja,
    "musan": prep_musan,
    "inject": prep_inject,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="+", choices=list(STEPS) + ["all"])
    args = ap.parse_args()
    steps = list(STEPS) if "all" in args.steps else args.steps
    for s in steps:
        STEPS[s]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
