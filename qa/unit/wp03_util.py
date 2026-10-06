"""
WP-03（G2P）测试公共工具：黄金集读取与比较口径、AquesTalk 可接受符号表预判。

比较口径见 docs/test-design/WP-03.md 4.2 节（已评审）：
去掉 `/` 和空白；片假名视同平假名；ぢ/づ 视同 じ/ず；`?` 视同 `？`；保留停顿位置。
"""

import csv
import json
import os
import re

from yukkuri.g2p.dicts.pinyin import PINYIN_MAP

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(QA_DIR, "data")
REPORT_DIR = os.path.join(QA_DIR, "reports", "wp03")
GOLDEN_CSV = os.path.join(DATA_DIR, "g2p_golden.csv")
ALPHABET_JSON = os.path.join(DATA_DIR, "aquestalk_alphabet.json")
LEVEL1_TXT = os.path.join(DATA_DIR, "tgscc_level1.txt")

PAUSES = set("、。？?")
SEPARATORS = set("/'")
_KATA = {chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)}
_HOMOPHONE = str.maketrans({"ぢ": "じ", "づ": "ず", "?": "？"})
_PINYIN_TOKEN = re.compile(r"^[a-zü]+$")


# ---------------------------------------------------------------- 比较口径

def kata_to_hira(s: str) -> str:
    return "".join(_KATA.get(c, c) for c in s)


def normalize_koe(s: str) -> str:
    """比较前的归一化（期望与实际同样处理）"""
    s = re.sub(r"[\s/]+", "", s)
    return kata_to_hira(s).translate(_HOMOPHONE)


def expand_expected(spec: str) -> str:
    """黄金集期望写法 → 音标：拼音 token 经映射表转为假名，其余原样。

    映射表中没有的拼音音节展开为 `⟨音节⟩`，保证与任何实际输出都不相等。
    """
    out = []
    for tok in spec.split():
        if _PINYIN_TOKEN.match(tok):
            out.append(PINYIN_MAP.get(tok, f"⟨{tok}⟩"))
        else:
            out.append(tok)
    return "/".join(out)


def load_golden(path: str = GOLDEN_CSV):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["accepted"] = [expand_expected(r["expected"])] + [
            expand_expected(a) for a in r["alternatives"].split("|") if a.strip()
        ]
    return rows


def golden_match(row, actual: str) -> bool:
    a = normalize_koe(actual)
    return any(normalize_koe(e) == a for e in row["accepted"])


# ---------------------------------------------------------------- 可合成性预判

class Alphabet:
    """由 probe_alphabet.py 实测得到的 AquesTalk 接受规则"""

    def __init__(self, path: str = ALPHABET_JSON):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.meta = {k: d[k] for k in ("platform", "voice", "encoding")}
        self.single_ok = {k for k, v in d["single"].items() if v == 0}
        self.rejected = {k for k, v in d["single"].items() if v != 0}
        self.pairs = set(d["accepted_pairs"])
        self.small = {p[1] for p in self.pairs} | {k for k in self.rejected if k in "ぁぃぅぇぉゃゅょゎゕゖァィゥェォャュョヮヵヶ"}

    def problems(self, koe: str):
        """返回音标中预计会被拒绝的位置列表 [(下标, 字符, 原因)]；空列表表示预计可合成"""
        out = []
        prev = None          # 上一个非分隔符字符
        extendable = False   # 上一个音节能否接 ー（可合成的假名、合法拗音或已合法的 ー，且不是 っ）
        for i, ch in enumerate(koe):
            if ch in SEPARATORS:
                continue
            if ch in PAUSES:
                prev, extendable = ch, False
                continue
            if ch == "ー":
                good = extendable
                if not good:
                    out.append((i, ch, "ー 前面不是可延长的假名（102）"))
            elif ch in self.small and ch not in self.single_ok:
                good = prev is not None and (prev + ch) in self.pairs and koe[i - 1] not in SEPARATORS
                if not good:
                    out.append((i, ch, "小写假名不构成合法拗音/外来音（105）"))
            else:
                good = ch in self.single_ok
                if not good:
                    out.append((i, ch, "AquesTalk 不接受的字符（105）"))
            extendable = good and ch not in "っッ"
            prev = ch
        return out

    def ok(self, koe: str) -> bool:
        return not self.problems(koe)


# ---------------------------------------------------------------- 其他

def is_kana_only(koe: str, alphabet_chars=None) -> bool:
    """只含假名区块字符与音标符号"""
    return all("぀" <= c <= "ヿ" or c in PAUSES or c in SEPARATORS for c in koe)


def ensure_report_dir():
    os.makedirs(REPORT_DIR, exist_ok=True)
    return REPORT_DIR
