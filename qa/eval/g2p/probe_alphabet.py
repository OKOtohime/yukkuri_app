"""
探测 AquesTalk 对假名区块（U+3040–U+30FF）和音标符号的接受情况，生成 L1 预判表。

    python qa/eval/g2p/probe_alphabet.py [--voice f1] [--out qa/data/aquestalk_alphabet.json]

需要 YUKKURI_DATA_DIR 指向已导入声线的数据目录。结果中：
- single:  单个字符的返回码；
- after_ka: 接在「か」后的返回码；
- pairs:    「可单独合成的假名 + 小写假名」的返回码（判定拗音/外来音是否合法）；
- contexts: ー 与停顿符在不同上下文中的返回码。
"""

import argparse
import json
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aqtk_client import AqtkClient  # noqa: E402

KANA = [chr(c) for c in range(0x3040, 0x3100)]
SMALL = list("ぁぃぅぇぉゃゅょゎゕゖァィゥェォャュョヮヵヶ")
PAUSES = ["、", "。", "？", "?", "/", "'"]
CONTEXTS = [
    "ー", "あー", "あーー", "ーあ", "、ー", "。ー", "？ー", "/ー", "あ/ー", "っー", "んー", "ゃー", "きゃー",
    "、", "。", "？", "?", "/", "'", "、あ", "あ、", "あ、、", "あ、。", "あ。、", "あ？？", "あ/、/あ",
    "っ", "っあ", "あっ", "ん", "んっ", "あ'", "'あ",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="f1")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "data", "aquestalk_alphabet.json"))
    args = ap.parse_args()

    from yukkuri.config import AppConfig
    lib = AppConfig().find_aquestalk_library(args.voice)
    if not lib:
        sys.exit("未找到声线库")

    with AqtkClient(lib) as c:
        single = {k: c.synth(k)[0] for k in KANA}
        after_ka = {k: c.synth("か" + k)[0] for k in KANA}
        bases = [k for k in KANA if single[k] == 0 and k not in SMALL]
        pairs = {b + s: c.synth(b + s)[0] for b in bases for s in SMALL}
        contexts = {k: c.synth(k)[0] for k in CONTEXTS}
        enc = c.encoding

    out = {
        "generated_by": "qa/eval/g2p/probe_alphabet.py",
        "platform": platform.platform(),
        "voice": args.voice,
        "encoding": enc,
        "single": single,
        "after_ka": after_ka,
        "accepted_pairs": sorted(k for k, v in pairs.items() if v == 0),
        "contexts": contexts,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    rej = sorted(k for k, v in single.items() if v != 0)
    print(f"encoding={enc} 单字被拒 {len(rej)}：{''.join(rej)}；合法拗音/外来音 {len(out['accepted_pairs'])} 个")


if __name__ == "__main__":
    main()
