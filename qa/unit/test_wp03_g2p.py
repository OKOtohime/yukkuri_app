"""
WP-03 L1：FR-G2P 功能用例（docs/test-design/WP-03.md 3.1 节）。

判定以读法为准：期望与实际都经 wp03_util.normalize_koe 归一化后比较。
期望读法按通行读法编写；中文期望用拼音写，经映射表展开（映射表即"油库里风格"，不在此重判）。
"""

import re
from unittest import mock

import numpy as np
import pytest
from pypinyin import lazy_pinyin
from pypinyin.phrases_dict import phrases_dict
from pypinyin.pinyin_dict import pinyin_dict
from pypinyin.style import convert as py_convert
from pypinyin import Style

from yukkuri.config import AppConfig
from yukkuri.g2p import PolyglotG2P, text_to_yukkuri_polyglot as g2p
from yukkuri.g2p.dicts.pinyin import PINYIN_MAP
from yukkuri.pipeline import YukkuriPipeline

from wp03_util import PAUSES, Alphabet, expand_expected, is_kana_only, normalize_koe

pytestmark = pytest.mark.l1


@pytest.fixture(scope="module")
def alphabet():
    return Alphabet()

LATIN = re.compile(r"[A-Za-z]")
HANZI = re.compile(r"[一-龥]")


def defect(def_id, *values):
    """标注已登记缺陷：缺陷存在时 xfail，修复后 XPASS 使套件失败"""
    return pytest.param(*values, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason=def_id))


def same_reading(actual, *expected_specs):
    a = normalize_koe(actual)
    return any(a == normalize_koe(expand_expected(e)) for e in expected_specs)


# ---------------------------------------------------------------- FR-G2P-01/02/04/05：README 示例

@pytest.mark.parametrize("text, lang, expected", [
    ("你好", "zh", "ni hao"),
    ("我是油库里", "zh", "wo shi ゆっくり"),
    ("Hello world", "zh", "へろー わーるど"),
    ("hello", "en", "へろー"),
    ("CPU", "zh", "しー ぴー ゆー"),
])
def test_fr_g2p_01_readme_examples(text, lang, expected):
    assert same_reading(g2p(text, lang), expected)


def test_fr_g2p_01_lang_en_reads_chinese():
    assert same_reading(g2p("你好世界", "en"), "ni hao shi jie")


@pytest.mark.parametrize("text, expected", [
    ("油库里", "ゆっくり"),
    ("油库里你好", "ゆっくり ni hao"),
    ("大家好我是油库里", "da jia hao wo shi ゆっくり"),
    ("油库里油库里", "ゆっくり ゆっくり"),
    ("油库里，你好。", "ゆっくり 、 ni hao 。"),
])
def test_fr_g2p_02_yukkuri_in_sentence(text, expected):
    assert same_reading(g2p(text, "zh"), expected)


# ---------------------------------------------------------------- FR-G2P-03：音节覆盖

def _dictionary_syllables():
    """G2P 处理范围（U+4E00–U+9FA5）内汉字在 pypinyin 字典中的全部读音"""
    out = {}
    for cp, readings in pinyin_dict.items():
        if not 0x4E00 <= cp <= 0x9FA5:
            continue
        for p in readings.split(","):
            s = py_convert(p, Style.NORMAL, strict=True)
            out.setdefault(s, chr(cp))
    return out


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-307")
def test_fr_g2p_03_all_dictionary_syllables():
    """pypinyin 字典中出现的全部音节（含多音字的非默认读音）都能映射"""
    syl = _dictionary_syllables()
    # pypinyin 的 NORMAL 风格把 ü 写成 v（lazy_pinyin 默认），字典音节中的 ü 同样按 v 处理
    missing = {s: c for s, c in syl.items() if s.replace("ü", "v") not in PINYIN_MAP}
    assert not missing, f"{len(missing)} 个音节没有读音（音节: 例字）: {missing}"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-307")
def test_fr_g2p_03_phrase_context_syllables():
    """pypinyin 词组库逐条转换：词组语境下产生的音节都能读出，不被静默丢弃"""
    dropped = {}
    for ph in phrases_dict:
        if not re.fullmatch(r"[一-龥]+", ph):
            continue
        for s in lazy_pinyin(ph):
            if s not in PINYIN_MAP:
                dropped.setdefault(s, ph)
    assert not dropped, f"词组语境中被丢弃的音节: {dropped}"


@pytest.mark.parametrize("text", ["女人", "绿色", "略微", "虐待", "嗯", "哪儿", "呣", "噷", "哟", "欸", "诶", "嘚瑟", "这么", "什么"])
def test_fr_g2p_03_special_syllables(text):
    """ü 音节、儿化、叹词：每个汉字都有读音，没有拉丁残留"""
    out = g2p(text, "zh")
    assert out and not LATIN.search(out)
    assert len([t for t in out.split("/") if t]) == len(text)


# ---------------------------------------------------------------- FR-G2P-04/05/06：英文

@pytest.mark.parametrize("text, expected", [
    ("hello", "へろー"), ("HELLO", "へろー"), ("Hello", "へろー"),
    ("thanks", "さんくす"), ("game", "げーむ"), ("youtube", "ゆーちゅーぶ"),
    ("discord", "でぃすこーど"), ("windows", "うぃんどーず"), ("sorry", "そりー"),
])
def test_fr_g2p_04_high_freq_words(text, expected):
    assert same_reading(g2p(text, "en"), expected)


# 日语中通行的字母读法（测试方编写，备选用 | 分隔）
LETTER_NAMES = {
    "A": "えー", "B": "びー", "C": "しー", "D": "でぃー|でー", "E": "いー", "F": "えふ", "G": "じー",
    "H": "えいち|えっち", "I": "あい", "J": "じぇー|じぇい", "K": "けー|けい", "L": "える", "M": "えむ",
    "N": "えぬ", "O": "おー", "P": "ぴー", "Q": "きゅー", "R": "あーる", "S": "えす", "T": "てぃー",
    "U": "ゆー", "V": "ぶい|ゔぃー", "W": "だぶりゅー|だぶる", "X": "えっくす", "Y": "わい", "Z": "ぜっと|ずぃー",
}


def spelled(acronym):
    """逐字母读的全部可接受组合"""
    combos = [""]
    for ch in acronym:
        combos = [c + " " + n for c in combos for n in LETTER_NAMES[ch].split("|")]
    return [c.strip() for c in combos]


@pytest.mark.parametrize("acronym", [
    "PC", "CPU", "GPU", "KFC", "FPS", "BGM", "NPC", "MVP",
    defect("DEF-304", "USB"), defect("DEF-304", "API"), defect("DEF-304", "OBS"),
    defect("DEF-304", "DIY"), defect("DEF-304", "HTTP"), defect("DEF-304", "HTML"),
])
def test_fr_g2p_05_acronyms(acronym):
    """全大写缩写逐字母读（Q4：读作单词的缩写如 NASA 不在此列）"""
    assert same_reading(g2p(acronym, "zh"), *spelled(acronym))


def test_fr_g2p_06_word_forms_readable():
    for w in ["don't", "I'm", "iPhone", "café", "rhythm", "strength", "xyz", "q"]:
        out = g2p(w, "en")
        assert out and is_kana_only(out), (w, out)


# ---------------------------------------------------------------- FR-G2P-07/08：日文

@pytest.mark.parametrize("text, expected", [
    ("東京", "とうきょう"),
    ("日本語", "にほんご"),
    ("ひらがな", "ひらがな"),
    ("カタカナ", "カタカナ"),
    ("ゆっくりしていってね", "ゆっくりしていってね"),
])
def test_fr_g2p_07_kanji_to_kana_kana_kept(text, expected):
    assert same_reading(g2p(text, "ja"), expected)


def test_fr_g2p_07_katakana_kept_verbatim():
    """假名保持原样：片假名不被改写成平假名或其他写法"""
    assert "コンピューター" in g2p("新しいコンピューター", "ja")


@pytest.mark.parametrize("text, expected", [
    defect("DEF-303", "行く", "いく"),
    defect("DEF-303", "食べる", "たべる"),
    defect("DEF-303", "書きます", "かきます"),
    defect("DEF-303", "話します", "はなします"),
    defect("DEF-303", "近づく", "ちかづく"),
    defect("DEF-303", "人々", "ひとびと"),
    defect("DEF-303", "時々", "ときどき"),
])
def test_fr_g2p_07_okurigana_and_iteration_mark(text, expected):
    """汉字读音要符合送假名语境；踊り字 々 要读出"""
    assert same_reading(g2p(text, "ja"), expected)


@pytest.mark.parametrize("text, expected", [
    defect("DEF-302", "私は", "わたしわ"),
    defect("DEF-302", "これは本です", "これわほんです"),
    defect("DEF-302", "東京へ行きます", "とうきょうえいきます"),
    defect("DEF-302", "明日は雨", "あしたわあめ"),
    defect("DEF-302", "こんにちは", "こんにちわ"),
])
def test_fr_g2p_08_particles(text, expected):
    """BL-08：作为助词的 は/へ 读作 わ/え，包括汉字后紧跟的假名助词"""
    assert same_reading(g2p(text, "ja"), expected)


@pytest.mark.parametrize("text, expected", [
    defect("DEF-302", "葉", "は"),
    defect("DEF-302", "歯", "は"),
    defect("DEF-302", "屁", "へ"),
    ("母", "はは"),
    ("はい", "はい"),
    ("へや", "へや"),
])
def test_fr_g2p_08_non_particles_unchanged(text, expected):
    """BL-08 反例：不是助词的 は/へ 不能被改读"""
    assert same_reading(g2p(text, "ja"), expected)


# ---------------------------------------------------------------- FR-G2P-09：数字

# 评审 Q2：按当前语种读出即通过；逐位读和按值读都接受。期望读法：
#   zh 用汉字数字的拼音（经映射表展开）；ja/en 用通行读法
NUM_READINGS = {
    "zh": {"0": "ling", "1": "yi", "3": "san", "4": "si", "5": "wu", "6": "liu", "7": "qi", "8": "ba", "9": "jiu"},
    "ja": {"0": "ぜろ|れい", "1": "いち", "3": "さん", "4": "よん|し", "5": "ご", "6": "ろく", "7": "なな|しち", "8": "はち", "9": "きゅう|く"},
    "en": {"0": "ぜろ", "1": "わん", "3": "すりー", "4": "ふぉー", "5": "ふぁいぶ", "6": "しっくす", "7": "せぶん", "8": "えいと", "9": "ないん"},
}


def digitwise(digits, lang):
    combos = [""]
    for d in digits:
        combos = [c + " " + r for c in combos for r in NUM_READINGS[lang][d].split("|")]
    return [c.strip() for c in combos]


@pytest.mark.parametrize("lang", ["zh", "ja", "en"])
@pytest.mark.parametrize("digits", ["7", "13", "4568", "90", "31415"])
def test_fr_g2p_09_digits_per_lang(digits, lang):
    """同一数字串按当前语种读出（数字 2 的读法单独判定，见下一条）"""
    assert same_reading(g2p(digits, lang), *digitwise(digits, lang))


@pytest.mark.parametrize("lang, hanzi_text", [("zh", "二"), ("ja", "二")])
def test_fr_g2p_09_digit_two_matches_numeral(lang, hanzi_text):
    """阿拉伯数字 2 与汉字「二」在同一语种下读法一致"""
    assert normalize_koe(g2p("2", lang)) == normalize_koe(g2p(hanzi_text, lang))


@pytest.mark.parametrize("text, lang, min_syllables", [
    defect("DEF-305", "１２３", "zh", 3),
    defect("DEF-305", "１２３", "ja", 3),
    defect("DEF-305", "二〇二六", "zh", 4),
    ("3.14", "zh", 3),
    ("1,000", "en", 4),
])
def test_fr_g2p_09_number_forms_not_dropped(text, lang, min_syllables):
    """全角数字、汉字数字〇、小数、千分位：每个数字都要读出"""
    out = g2p(text, lang)
    tokens = [t for t in re.split(r"[/、。？?]", out) if t]
    assert len(tokens) >= min_syllables, out


@pytest.mark.parametrize("text, lang", [
    defect("DEF-305", "ＣＰＵ", "zh"),
    defect("DEF-305", "ｈｅｌｌｏ", "en"),
])
def test_fr_g2p_06_fullwidth_latin_not_dropped(text, lang):
    """全角拉丁字母（日文输入法常见）不被静默跳过"""
    assert g2p(text, lang)


# ---------------------------------------------------------------- FR-G2P-10：混排（蜕变）

@pytest.mark.parametrize("segments, lang, sep", [
    (["我用", "CPU", "打游戏"], "zh", ""),
    (["我用", "CPU", "打游戏"], "zh", " "),
    (["今天", "hello", "ゆっくり", "123"], "zh", ""),
    (["学校", "で", "game", "を", "する"], "ja", ""),
    (["PC", "が", "3", "台"], "ja", ""),
    (["see", "you", "明天"], "en", " "),
    (["hello", "你好", "456", "ありがとう"], "en", " "),
])
def test_fr_g2p_10_mixed_script(segments, lang, sep):
    """MT：混排整句一次转换的读法 = 各段分别转换后拼接（同一 lang，不切换语种）"""
    whole = g2p(sep.join(segments), lang)
    parts = "".join(g2p(s, lang) for s in segments)
    assert normalize_koe(whole) == normalize_koe(parts)
    assert all(g2p(s, lang) for s in segments)


# ---------------------------------------------------------------- FR-G2P-11：标点与符号

# 评审 Q3：句读类必须产生停顿；引号、括号、破折号、~ 等可以忽略
@pytest.mark.parametrize("p", [
    "，", "。", "！", "？", "、", ",", ".", "!", "?",
    defect("DEF-306", "；"), defect("DEF-306", "："), defect("DEF-306", "……"), defect("DEF-306", "…"),
    defect("DEF-306", ";"), defect("DEF-306", ":"),
])
@pytest.mark.parametrize("lang, left, right", [("zh", "你好", "再见"), ("en", "hello", "world"), ("ja", "はい", "そうです")])
def test_fr_g2p_11_punct_to_pause(p, lang, left, right):
    out = g2p(f"{left}{p}{right}", lang)
    assert any(c in PAUSES for c in out), out


@pytest.mark.parametrize("sym", ["😀", "#", "@", "%", "&", "*", "~", "“", "”", "（", "）", "「", "」", "——", "α", "Привет", "안녕", "​", "\t", "\x00"])
def test_fr_g2p_11_symbols_ignored(sym):
    """不可合成的符号被忽略：不报错，读法与去掉符号时相同"""
    assert normalize_koe(g2p(f"你好{sym}再见", "zh")) == normalize_koe(g2p("你好再见", "zh"))


@pytest.mark.parametrize("text, lang", [
    defect("DEF-301", "鼻血", "ja"),
    defect("DEF-301", "近づく", "ja"),
    defect("DEF-301", "3ヶ月", "ja"),
    defect("DEF-301", "ヴァイオリン", "ja"),
    defect("DEF-301", "ジョン・スミス", "ja"),
    defect("DEF-301", "いすゞ", "ja"),
    defect("DEF-301", "ーあ", "ja"),
    defect("DEF-301", "你好。ーー", "zh"),
])
def test_fr_g2p_11_output_synthesizable_known_cases(alphabet, text, lang):
    """输出中不能含 AquesTalk 不接受的符号（预判；L2 用例用真实库复核同一批输入）"""
    out = g2p(text, lang)
    assert alphabet.ok(out), alphabet.problems(out)


# ---------------------------------------------------------------- FR-G2P-13：不可读文本

@pytest.mark.parametrize("text", ["", " ", "\n\t", "😀😀", "#@&*~", "αβγ", "Привет", "안녕하세요", "​", "“”（）"])
@pytest.mark.parametrize("lang", ["zh", "en", "ja"])
def test_fr_g2p_13_unreadable_only_empty(text, lang):
    assert g2p(text, lang) == ""


@pytest.mark.parametrize("text", ["。", "？？", "!!!", "...", "，。！"])
def test_obs_301_punct_only_yields_pause_only_koe(text):
    """评审 Q5：只含标点时产生只有停顿符的音标（合成为无声音频），按可发声口径不违反 FR-G2P-13，记 OBS-301"""
    out = g2p(text, "zh")
    assert out and all(c in PAUSES for c in out)


@pytest.fixture
def pipeline():
    asr = mock.Mock()
    p = YukkuriPipeline(AppConfig(), asr=asr, tts=mock.Mock(), loopback_manager=mock.Mock())
    p.player = mock.Mock()
    yield p


@pytest.mark.parametrize("text", ["😀", "#@&", "Привет", "αβγ"])
def test_fr_g2p_13_pipeline_skips_unreadable(pipeline, text):
    """流水线（语音识别路径和文字播报路径）收到不可读文本：不合成、不播放、不报错"""
    pipeline.asr.decode.return_value = text
    pipeline.process_segment(np.zeros(16000, dtype=np.float32))
    pipeline.speak_text(text)
    pipeline.tts.synthesize.assert_not_called()
    pipeline.player.play.assert_not_called()


# ---------------------------------------------------------------- 健壮性

@pytest.mark.parametrize("lang", ["ko", "", "ZH", None])
def test_fr_g2p_10_unknown_lang_does_not_raise(lang):
    out = PolyglotG2P().convert("你好hello123こんにちは今日", lang=lang)
    assert out and is_kana_only(out)


def test_obs_302_yukkuri_substring_replacement():
    """「油库里」做子串替换：石油库里面 中跨词的 油库里 也被读作 ゆっくり（记录行为，OBS-302）"""
    assert "ゆっくり" in g2p("石油库里面", "zh")
