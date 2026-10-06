"""
WP-03 L2：G2P 输出交给真实 AquesTalk 库（QR-FUNC-06/07、FR-G2P-11）。

合成在子进程中进行（qa/eval/g2p/aqtk_worker.py），库崩溃时只影响工作进程并被计为"崩溃"。
QR-FUNC-07 全量评测结果写入 qa/reports/wp03/qr_func_07_<平台>.json。
"""

import collections
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
QA_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(QA_DIR, "unit"))
sys.path.insert(0, os.path.join(QA_DIR, "eval", "g2p"))

from yukkuri.g2p import text_to_yukkuri_polyglot as g2p  # noqa: E402
from yukkuri.g2p.dicts import ENG_CONSONANTS, ENG_SYLLABLES, ENG_VOWELS, ENGLISH_HIGH_FREQ, LETTER_MAP, PINYIN_MAP  # noqa: E402
from yukkuri.g2p.normalizer import DIGIT_CN, DIGIT_EN, DIGIT_JA  # noqa: E402

from aqtk_client import AqtkClient  # noqa: E402
from wp03_util import ALPHABET_JSON, LEVEL1_TXT, ensure_report_dir  # noqa: E402

pytestmark = [pytest.mark.l2, pytest.mark.requires_aquestalk]

VOICES = ["f1", "f2", "f3", "m1", "m2", "r1", "dvd", "imd1", "jgr"]
ENV = "W1" if sys.platform == "win32" else "R1"


@pytest.fixture(scope="module")
def client():
    from yukkuri.config import AppConfig
    with AqtkClient(AppConfig().find_aquestalk_library("f1")) as c:
        yield c


def test_qr_func_06_level1_synthesizable(client):
    """3500 字逐字的音标全部能被真实库合成（覆盖不只是表里有键）"""
    with open(LEVEL1_TXT, encoding="utf-8") as f:
        chars = [c.strip() for c in f if c.strip()]
    bad = [(c, k, code) for c in chars for k in [g2p(c, "zh")] for code, _ in [client.synth(k)] if code != 0]
    assert bad == []


def test_qr_func_07_dictionary_values_synthesizable(client):
    tables = {
        "PINYIN_MAP": PINYIN_MAP, "ENGLISH_HIGH_FREQ": ENGLISH_HIGH_FREQ, "LETTER_MAP": LETTER_MAP,
        "ENG_VOWELS": ENG_VOWELS, "ENG_CONSONANTS": ENG_CONSONANTS, "ENG_SYLLABLES": ENG_SYLLABLES,
        "DIGIT_CN": DIGIT_CN, "DIGIT_JA": DIGIT_JA, "DIGIT_EN": DIGIT_EN,
    }
    bad = {name: {k: v for k, v in t.items() if client.synth(v)[0] != 0} for name, t in tables.items()}
    assert not any(bad.values()), bad


def _xf(def_id, *v):
    return pytest.param(*v, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason=def_id))


@pytest.mark.parametrize("text, lang", [
    ("你好，世界！", "zh"), ("ゆっくりしていってね", "ja"), ("コンピューター", "ja"), ("hello world", "en"),
    ("きゃー", "ja"), ("カーー", "ja"), ("今日はいい天気ですね？", "ja"),
    _xf("DEF-301", "鼻血", "ja"),
    _xf("DEF-301", "私もそれに気づきます", "ja"),
    _xf("DEF-301", "3ヶ月", "ja"),
    _xf("DEF-301", "ヴァイオリン", "ja"),
    _xf("DEF-301", "ジョージ・ワシントン", "ja"),
    _xf("DEF-301", "あぁ、疲れている！", "ja"),
    _xf("DEF-301", "いすゞ", "ja"),
    _xf("DEF-301", "ーあ", "ja"),
])
def test_fr_g2p_11_real_library_accepts_output(client, text, lang):
    """FR-G2P-11：任何符号都不导致合成失败——真实库返回码为 0"""
    koe = g2p(text, lang)
    code, _ = client.synth(koe)
    assert code == 0, f"{text!r} -> {koe!r} 返回 {code}"


def test_wp03_alphabet_table_matches_this_library(client):
    """L1 预判表（R1 实测）与本环境的库一致；不一致时说明需要按平台重新生成"""
    with open(ALPHABET_JSON, encoding="utf-8") as f:
        table = json.load(f)
    diff = {k: (v, client.synth(k)[0]) for k, v in table["single"].items() if (client.synth(k)[0] == 0) != (v == 0)}
    diff.update({k: (v, client.synth(k)[0]) for k, v in table["contexts"].items() if (client.synth(k)[0] == 0) != (v == 0)})
    assert diff == {}


@pytest.fixture(scope="module")
def qr07_result():
    from run_qr_func_07 import evaluate
    res = evaluate("f1")
    out = os.path.join(ensure_report_dir(), f"qr_func_07_{ENV}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


def test_qr_func_07_no_crash(qr07_result):
    """≥ 10,000 条模糊输入：G2P 无异常，合成库无崩溃"""
    assert qr07_result["overall"]["items"] >= 10000
    assert qr07_result["crashes"] == [] and qr07_result["g2p_exceptions"] == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-301")
def test_qr_func_07_synthesizability(qr07_result):
    """非空音标中 105/107 的比例 ≤ 0.1%"""
    o = qr07_result["overall"]
    assert o["judged_rate"] <= 0.001, f"{o['judged_failures']}/{o['nonempty_koe']} = {o['judged_rate']:.3%}"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-301")
def test_qr_func_07_real_transcripts(qr07_result):
    """子集 A（真实转写文本，最接近 ASR 输出）单独也满足 ≤ 0.1%"""
    a = qr07_result["per_subset"]["A"]
    assert a["judged_rate"] <= 0.001, f"{a['judged_failures']}/{a['nonempty_koe']} = {a['judged_rate']:.3%}"


def test_qr_func_07_l1_predictor_reliable(qr07_result):
    """L1 预判表与真实库的结论一致率 ≥ 99%；预判从不把真实库能合成的音标判为失败（差异只允许是预判表覆盖不到的超长等情况）"""
    assert qr07_result["l1_predictor_agreement"] >= 0.99
    assert all(d["predicted_ok"] for d in qr07_result["l1_predictor_disagreements"])


def test_qr_func_07_voice_independent():
    """105/107 与声线无关：子集 A 抽样在 9 种声线上，"是否 105/107"的结论相同。

    204（超长）与声线有关（同一音标 f1 合成出约 19s 音频，f3 返回 204），推测上限按合成时长计；
    差异写入 qa/reports/wp03/voice_204.json，交 WP-04 在 FR-TTS-07 中判定。
    """
    from yukkuri.config import AppConfig
    from fuzz_corpus import generate
    cfg = AppConfig()
    voices = [v for v in VOICES if cfg.find_aquestalk_library(v)]
    items = [x for x in generate() if x["subset"] == "A"][::8]
    koes = [k for k in (g2p(x["text"], x["lang"]) for x in items) if k]
    results = collections.defaultdict(list)
    for v in voices:
        with AqtkClient(cfg.find_aquestalk_library(v)) as c:
            results[v] = [code for code, _ in c.run(koes)]
    judged = {v: [c in (105, 107) for c in results[v]] for v in voices}
    over = {v: [i for i, c in enumerate(results[v]) if c == 204] for v in voices}
    with open(os.path.join(ensure_report_dir(), f"voice_204_{ENV}.json"), "w", encoding="utf-8") as f:
        json.dump({"inputs": len(koes), "code_204_indices": over,
                   "koe_bytes": {i: len(koes[i].encode()) for v in voices for i in over[v]}}, f, ensure_ascii=False, indent=1)
    base = judged[voices[0]]
    assert all(judged[v] == base for v in voices), {v: sum(a != b for a, b in zip(judged[v], base)) for v in voices}
