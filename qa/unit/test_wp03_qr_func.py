"""
WP-03 L1：QR-FUNC-06（3500 字覆盖）、QR-FUNC-08（黄金集）。

QR-FUNC-08 的明细写入 qa/reports/wp03/golden_result.json，供执行记录和缺陷报告引用。
"""

import collections
import json
import os
import re

import pytest
from pypinyin import lazy_pinyin

from yukkuri.g2p import text_to_yukkuri_polyglot as g2p
from yukkuri.g2p.dicts.pinyin import PINYIN_MAP

from wp03_util import LEVEL1_TXT, ensure_report_dir, golden_match, load_golden, normalize_koe

pytestmark = pytest.mark.l1

LATIN = re.compile(r"[A-Za-z]")
GOLDEN_THRESHOLD = 0.95


@pytest.fixture(scope="module")
def level1():
    with open(LEVEL1_TXT, encoding="utf-8") as f:
        chars = [line.strip() for line in f if line.strip()]
    assert len(chars) == 3500 and len(set(chars)) == 3500
    return chars


def test_qr_func_06_level1_syllables_mapped(level1):
    """3500 字逐字取 pypinyin 默认读音：未命中映射表的音节数 = 0"""
    missing = [(c, s) for c in level1 for s in lazy_pinyin(c) if s not in PINYIN_MAP]
    assert missing == []


def test_qr_func_06_level1_no_drop_no_latin(level1):
    """3500 字逐字转换：没有空输出（静默丢弃），没有拉丁残留"""
    empty = [c for c in level1 if not g2p(c, "zh")]
    latin = [c for c in level1 if LATIN.search(g2p(c, "zh"))]
    assert empty == [] and latin == []


def test_qr_func_06_level1_in_sentence(level1):
    """3500 字按 50 字一组成句转换：音节数与字数相同（词组语境下也不丢音节）"""
    bad = []
    for i in range(0, len(level1), 50):
        chunk = "".join(level1[i:i + 50])
        n = len([t for t in g2p(chunk, "zh").split("/") if t])
        if n != len(chunk):
            bad.append((i, n, len(chunk)))
    assert bad == []


def test_qr_func_08_golden_set():
    """黄金集一致率 ≥ 95%（QR-FUNC-08，S 级）；按类别统计"""
    rows = load_golden()
    assert len(rows) >= 200
    per_cat = collections.defaultdict(lambda: [0, 0])
    detail = []
    for r in rows:
        actual = g2p(r["text"], r["lang"])
        ok = golden_match(r, actual)
        per_cat[r["category"]][0] += ok
        per_cat[r["category"]][1] += 1
        detail.append({
            "id": r["id"], "category": r["category"], "lang": r["lang"], "text": r["text"],
            "accepted": r["accepted"], "actual": actual, "match": ok,
        })
    hits = sum(d["match"] for d in detail)
    rate = hits / len(rows)
    summary = {
        "total": len(rows), "match": hits, "rate": round(rate, 4), "threshold": GOLDEN_THRESHOLD,
        "per_category": {k: {"match": v[0], "total": v[1], "rate": round(v[0] / v[1], 4)} for k, v in per_cat.items()},
        "mismatches": [d for d in detail if not d["match"]],
    }
    out = os.path.join(ensure_report_dir(), "golden_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    assert rate >= GOLDEN_THRESHOLD, f"一致率 {rate:.1%}（{hits}/{len(rows)}），明细见 {out}"


def test_qr_func_08_golden_oracle_self_check():
    """比较口径自检：期望读法本身只含假名和停顿符（拼音全部可展开，除了刻意保留的未映射音节）"""
    for r in load_golden():
        for e in r["accepted"]:
            n = normalize_koe(e)
            assert not LATIN.search(n.replace("⟨dia⟩", "")), (r["id"], e)
