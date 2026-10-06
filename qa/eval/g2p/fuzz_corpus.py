"""
QR-FUNC-07 模糊测试语料生成（固定种子，可复现）。

子集（docs/test-design/WP-03.md 第 5 节）：
  A 真实转写文本：评测语料清单中的参考文本（AISHELL-1、LibriSpeech、CV-ja）与混说脚本，模拟 ASR 输出
  B 结构化随机：按 G2P 输入分类树的叶子拼接片段（汉字、词组、日文词、假名、英文词、缩写、数字、标点、符号）
  C 字符级模糊：按 Unicode 区块加权随机取字符

    python qa/eval/g2p/fuzz_corpus.py            # 打印各子集条数
    generate() -> [{"id", "subset", "lang", "text"}]
"""

import csv
import os
import random

QA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(QA_DIR, "data")
SEED = 20261007
N_B, N_C = 6000, 2500

MANIFESTS = [
    ("aishell1_test.tsv", "zh"),
    ("librispeech_test_clean.tsv", "en"),
    ("cv21_ja_test.tsv", "ja"),
]

# 日文常用词（含送假名、助词、片假名外来语、踊り字），测试方编写
JA_WORDS = (
    "私 僕 あなた 今日 明日 昨日 学校 会社 電車 時間 友達 先生 日本 東京 大阪 天気 映画 音楽 料理 仕事 "
    "行く 来る 見る 食べる 飲む 話す 書く 読む 聞く 遊ぶ 待つ 帰る 続く 近づく 気づく 頑張る 始まる 終わる "
    "新しい 古い 美味しい 楽しい 難しい 早い 大きい 小さい 人々 時々 色々 様々 "
    "は へ を が に で と も の から まで より ね よ か "
    "ゲーム パソコン スマホ コンピューター ヴァイオリン ジョン・スミス 3ヶ月 ディスコード ストリーム "
    "ありがとう こんにちは こんばんは すみません おはよう ゆっくり ちょっと"
).split()
EMOJI = list("😀😂👍🎮🔥✨❤️🙏")
SYMBOLS = list("#@%&*+=<>|\\^_`$€¥£©®™°±×÷→←↑↓★☆♪")
PUNCT = list("，。！？、；：…—“”‘’（）「」『』《》【】,.!?;:'\"()[]-~/")


def _read_texts():
    rows = []
    for name, lang in MANIFESTS:
        with open(os.path.join(DATA, "manifests", name), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                rows.append((lang, r["text"]))
    with open(os.path.join(DATA, "mixed_zh_en_script.tsv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows.append(("zh", r["text"]))
    return rows


def _pools(texts):
    with open(os.path.join(DATA, "tgscc_level1.txt"), encoding="utf-8") as f:
        hanzi = [c.strip() for c in f if c.strip()]
    from pypinyin.phrases_dict import phrases_dict
    phrases = sorted(p for p in phrases_dict if 2 <= len(p) <= 4)
    en_words = sorted({w for lang, t in texts if lang == "en" for w in t.split()})
    kana = [chr(c) for c in range(0x3041, 0x3097)] + [chr(c) for c in range(0x30A1, 0x30FB)] + ["ー"]
    return hanzi, phrases, en_words, kana


def _fragment(rng, pools):
    hanzi, phrases, en_words, kana = pools
    kind = rng.choices(
        ["hanzi", "phrase", "ja", "kana", "en", "acronym", "mixedcase", "number", "punct", "symbol", "emoji", "fullwidth", "space"],
        weights=[12, 14, 14, 6, 14, 6, 3, 10, 10, 3, 2, 3, 3],
    )[0]
    if kind == "hanzi":
        return "".join(rng.choice(hanzi) for _ in range(rng.randint(1, 6)))
    if kind == "phrase":
        return rng.choice(phrases)
    if kind == "ja":
        return rng.choice(JA_WORDS)
    if kind == "kana":
        return "".join(rng.choice(kana) for _ in range(rng.randint(1, 5)))
    if kind == "en":
        return rng.choice(en_words)
    if kind == "acronym":
        return "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(rng.randint(2, 5)))
    if kind == "mixedcase":
        w = rng.choice(en_words)
        return "".join(c.upper() if rng.random() < 0.3 else c for c in w)
    if kind == "number":
        n = rng.choice([
            str(rng.randint(0, 9)), str(rng.randint(10, 99999)), f"{rng.randint(0, 999)}.{rng.randint(0, 99)}",
            f"{rng.randint(1, 100)}%", f"-{rng.randint(1, 50)}", f"{rng.randint(1, 9)},{rng.randint(100, 999)}",
            f"{rng.randint(1990, 2030)}年", f"{rng.randint(1, 12)}月{rng.randint(1, 28)}日",
        ])
        return n
    if kind == "punct":
        return "".join(rng.choice(PUNCT) for _ in range(rng.randint(1, 3)))
    if kind == "symbol":
        return rng.choice(SYMBOLS)
    if kind == "emoji":
        return rng.choice(EMOJI)
    if kind == "fullwidth":
        return "".join(chr(rng.randint(0xFF10, 0xFF5A)) for _ in range(rng.randint(1, 4)))
    return " "


# 字符级：(区块下界, 上界, 权重)
BLOCKS = [
    (0x4E00, 0x9FFF, 30), (0x3040, 0x309F, 12), (0x30A0, 0x30FF, 12), (0x0020, 0x007E, 18),
    (0xFF01, 0xFF5E, 5), (0x3000, 0x303F, 8), (0x2000, 0x206F, 3), (0x00A0, 0x024F, 4),
    (0x0370, 0x04FF, 3), (0xAC00, 0xD7A3, 2), (0x1F300, 0x1F64F, 3),
]


def _char_fuzz(rng):
    out = []
    for _ in range(rng.randint(1, 60)):
        lo, hi, _w = rng.choices(BLOCKS, weights=[b[2] for b in BLOCKS])[0]
        out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def generate(seed=SEED):
    rng = random.Random(seed)
    texts = _read_texts()
    items = [{"id": f"A{i:05d}", "subset": "A", "lang": lang, "text": t} for i, (lang, t) in enumerate(texts)]
    pools = _pools(texts)
    for i in range(N_B):
        sep = rng.choice(["", " "])
        text = sep.join(_fragment(rng, pools) for _ in range(rng.randint(1, 40)))
        items.append({"id": f"B{i:05d}", "subset": "B", "lang": rng.choice(["zh", "en", "ja"]), "text": text})
    for i in range(N_C):
        items.append({"id": f"C{i:05d}", "subset": "C", "lang": rng.choice(["zh", "en", "ja"]), "text": _char_fuzz(rng)})
    return items


if __name__ == "__main__":
    import collections
    items = generate()
    print(len(items), collections.Counter(x["subset"] for x in items))
