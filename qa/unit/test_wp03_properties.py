"""
WP-03 L1：G2P 属性测试与模糊测试（hypothesis）。

已登记缺陷 DEF-301 的输入（AquesTalk 不接受的假名、开头或停顿后的 ー）由 test_wp03_g2p 的
固定用例覆盖；这里的"可合成"属性排除这些字符，用于发现其他类型的问题并防止回归。
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pypinyin import lazy_pinyin

from yukkuri.g2p import PolyglotG2P, text_to_yukkuri_polyglot as g2p
from yukkuri.g2p.normalizer import clean_koe_string

from wp03_util import Alphabet, is_kana_only

pytestmark = pytest.mark.l1

NO_PINYIN = {chr(c) for c in range(0x4E00, 0x9FA6) if lazy_pinyin(chr(c)) == [chr(c)]}

ALPHABET = Alphabet()
LANGS = st.sampled_from(["zh", "en", "ja"])

# 偏向 G2P 实际会遇到的字符：CJK、假名、ASCII、全角、标点；再混入任意 Unicode
_DEF301_CHARS = "".join(sorted(ALPHABET.rejected))
CJK = st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF)
KANA = st.characters(min_codepoint=0x3040, max_codepoint=0x30FF, blacklist_characters=_DEF301_CHARS)
ASCII = st.characters(min_codepoint=0x20, max_codepoint=0x7E)
PUNCT = st.sampled_from(list("，。！？、；：…—“”（）「」『』《》,.!?;:'\"()-~/ \n\t"))
FULLWIDTH = st.characters(min_codepoint=0xFF01, max_codepoint=0xFF5E)
ANY = st.characters(blacklist_categories=("Cs",), blacklist_characters=_DEF301_CHARS)
MIXED_TEXT = st.text(st.one_of(CJK, KANA, ASCII, PUNCT, FULLWIDTH, ANY), max_size=60)

FUZZ = settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@FUZZ
@given(text=st.text(max_size=80), lang=LANGS)
def test_fr_g2p_11_any_unicode_no_exception(text, lang):
    """任意 Unicode（含代理项以外的全部类别）：不抛异常，返回 str"""
    assert isinstance(g2p(text, lang), str)


@FUZZ
@given(text=MIXED_TEXT, lang=LANGS)
def test_fr_g2p_11_output_alphabet(text, lang):
    """输出只含 AquesTalk 实测可接受的字符与组合（排除 DEF-301 的已知输入）"""
    out = g2p(text, lang)
    assert is_kana_only(out)
    if "ー" not in text:
        assert ALPHABET.ok(out), (out, ALPHABET.problems(out))


@FUZZ
@given(text=MIXED_TEXT, lang=LANGS)
def test_fr_g2p_11_output_well_formed(text, lang):
    """结构：不以分隔符或顿号开头/结尾，没有空的音节段（"//"）"""
    out = g2p(text, lang)
    if out:
        assert out[0] not in "/、" and out[-1] not in "/、"
        assert "//" not in out


@FUZZ
@given(text=MIXED_TEXT, lang=LANGS)
def test_fr_g2p_deterministic(text, lang):
    """同一输入两次转换结果相同；新实例与默认单例结果相同"""
    assert g2p(text, lang) == g2p(text, lang) == PolyglotG2P().convert(text, lang=lang)


@settings(max_examples=1000, deadline=None)
@given(raw=st.text(max_size=80))
def test_impl_12_clean_koe_idempotent(raw):
    """测试计划 4.2：clean_koe_string 幂等"""
    once = clean_koe_string(raw)
    assert clean_koe_string(once) == once


@settings(max_examples=500, deadline=None)
@given(word=st.text(st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")), min_size=1, max_size=20))
def test_fr_g2p_06_any_ascii_word_read(word):
    """任意小写字母串：有读音，只含假名，可合成（FR-G2P-06：不在词表中的词也能读出）"""
    for w in (word, word.upper(), word.capitalize()):
        out = g2p(w, "en")
        assert out and is_kana_only(out) and ALPHABET.ok(out), (w, out)


@settings(max_examples=500, deadline=None)
@given(text=st.text(CJK, min_size=1, max_size=30))
def test_fr_g2p_03_hanzi_runs_keep_syllable_count(text):
    """lang=zh：基本区汉字串（不含「油库里」）每个字一个音节。

    pypinyin 没有读音的 10 个字（日本国字式的单位字）由 test_obs_303 单独记录。
    """
    if "油库里" in text or any(c > "龥" or c in NO_PINYIN for c in text):
        return
    out = g2p(text, "zh")
    assert len([t for t in out.split("/") if t]) == len(text), out


def test_obs_303_hanzi_without_pinyin_dropped():
    """U+4E00–U+9FA5 中 pypinyin 没有读音的字被静默丢弃（不是普通话音节，FR-G2P-03 不适用；记 OBS-303）"""
    assert sorted(NO_PINYIN) == sorted("兙兡嗧桛烪瓧瓰瓱瓼甅")
    assert all(g2p(c, "zh") == "" for c in NO_PINYIN)
