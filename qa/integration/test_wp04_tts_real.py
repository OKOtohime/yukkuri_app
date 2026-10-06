"""
WP-04 L2：真实 AquesTalk 评价版库上的合成行为（FR-TTS-01/03/04/06/07）。
数据目录取 YUKKURI_DATA_DIR（R1/W1 的 appdata，WP-01 导入了 9 种声线）。
评价版会把 na/ma 行替换为 nu（测试计划 1.2），本文件只按返回值、输出是否相同、时长判定，不做听感判断。
"""

import json
import os
import random
import subprocess
import sys
import threading

import pytest

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(QA_DIR, "unit"))

from yukkuri.config import AppConfig  # noqa: E402
from yukkuri.tts.aquestalk1 import AquesTalk1Engine  # noqa: E402

from wp04_util import IS_WIN, VOICES, ensure_report_dir  # noqa: E402

pytestmark = [pytest.mark.l2, pytest.mark.requires_aquestalk]
KOE = "ゆっくりしていってね"
RESULTS = {}


@pytest.fixture(scope="module", autouse=True)
def dump_results():
    yield
    if RESULTS:
        path = os.path.join(ensure_report_dir(), f"tts_real_{'W1' if IS_WIN else 'R1'}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, ensure_ascii=False, indent=1, sort_keys=True)


@pytest.fixture(scope="module")
def cfg():
    c = AppConfig()
    assert sorted(c.get_available_voices()) == sorted(VOICES), "需要 9 种声线全部就绪（WP-01）"
    return c


@pytest.fixture(scope="module")
def refs(cfg):
    """每种声线对 KOE 的参考输出（每个声线单独的引擎实例）"""
    out = {}
    for v in VOICES:
        out[v] = AquesTalk1Engine(voice=v, voice_resolver=cfg.find_aquestalk_library).synthesize(KOE)
    return out


def test_fr_tts_01_real_nine_voices_distinct(refs):
    assert all(refs[v] for v in VOICES)
    assert len({refs[v] for v in VOICES}) == 9, "9 种声线的输出应两两不同"


def test_fr_tts_03_real_switch_under_load(cfg, refs):
    """一个线程连续合成 200 句，另一个线程随机切换声线 50 次：无异常、无空输出，
    每一句都与某一种声线的参考输出逐字节相同（转换不中断、不出现混合或损坏的输出）"""
    eng = AquesTalk1Engine(voice="f1", voice_resolver=cfg.find_aquestalk_library)
    outputs, errors = [], []
    stop = threading.Event()

    def synth():
        try:
            for _ in range(200):
                outputs.append(eng.synthesize(KOE))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
        finally:
            stop.set()

    def switch():
        rnd = random.Random(20261007)
        n = 0
        while not stop.is_set() and n < 50:
            assert eng.set_voice(rnd.choice(VOICES))
            n += 1
            stop.wait(0.004)  # 让切换分散在整个合成过程中

    ts = [threading.Thread(target=synth), threading.Thread(target=switch)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    by_wav = {w: v for v, w in refs.items()}
    seen = [by_wav.get(w) for w in outputs]
    RESULTS["switch_under_load"] = {"n": len(outputs), "voices_seen": sorted({s for s in seen if s}), "unknown": seen.count(None)}
    assert errors == [] and len(outputs) == 200
    assert None not in seen
    assert len(set(seen)) >= 5, f"切换与合成交错不足，只出现了 {sorted(set(seen))}（工具自检）"


def test_fr_tts_03_real_next_sentence_after_switch(cfg, refs):
    eng = AquesTalk1Engine(voice="f1", voice_resolver=cfg.find_aquestalk_library)
    for v in ["m1", "dvd", "jgr", "f1", "r1"]:
        assert eng.set_voice(v)
        assert eng.synthesize(KOE) == refs[v]


def test_fr_tts_04_real_speed_monotonic(cfg):
    eng = AquesTalk1Engine(voice="f1", voice_resolver=cfg.find_aquestalk_library)
    lens = {s: len(eng.synthesize(KOE, speed=s)) for s in (50, 100, 200, 300)}
    RESULTS["speed_lengths"] = lens
    assert lens[50] > lens[100] > lens[200] > lens[300]


def test_fr_tts_06_real_invalid_key(cfg, capfd):
    eng = AquesTalk1Engine(voice="f1", voice_resolver=cfg.find_aquestalk_library, dev_key="WRONG-KEY", usr_key="WRONG-KEY")
    err = capfd.readouterr().err
    RESULTS["invalid_key_stderr"] = err.strip().splitlines()
    assert "警告" in err
    assert eng.synthesize(KOE)


def _overlong_probe(eng, voice):
    """逐步加长音标直到失败，返回 (失败时的音标字节数, 提示文本)"""
    import io
    from contextlib import redirect_stderr
    unit = "ゆっくりしていってね、"
    for n in range(10, 400, 10):
        koe = unit * n
        buf = io.StringIO()
        with redirect_stderr(buf):
            w = eng.synthesize(koe)
        if not w:
            return len(koe.encode("utf-8")), buf.getvalue().strip()
    return None, ""


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-403")
def test_fr_tts_07_real_overlong_koe(cfg, refs):
    """超长音标（WP-03 线索）：每种声线逐步加长直到失败。失败后 ①有提示且说明原因（评审 Q3：
    "未知错误"不算）；②同一引擎下一句正常"""
    rows, bad = {}, []
    for v in VOICES:
        eng = AquesTalk1Engine(voice=v, voice_resolver=cfg.find_aquestalk_library)
        size, msg = _overlong_probe(eng, v)
        nxt = eng.synthesize(KOE) == refs[v]
        rows[v] = {"fail_at_bytes": size, "message": msg[:160], "next_ok": nxt}
        assert nxt, f"{v}: 失败后下一句不正常"
        if size is None:
            continue
        if not msg or "未知错误" in msg:
            bad.append(v)
    RESULTS["overlong"] = rows
    assert bad == [], f"失败原因不明的声线: {bad}；例: {rows[bad[0]]['message'] if bad else ''}"


_CONCURRENT = r"""
import json, sys, threading
from yukkuri.config import AppConfig
from yukkuri.tts.aquestalk1 import AquesTalk1Engine
cfg = AppConfig()
eng = AquesTalk1Engine(voice="f1", voice_resolver=cfg.find_aquestalk_library)
koes = ["ゆっくりしていってね", "こんにちは", "てすと、です"]
refs = {k: eng.synthesize(k) for k in koes}
bad = [0]; done = [0]
def w(i):
    for j in range(300):
        k = koes[(i + j) % 3]
        if eng.synthesize(k) != refs[k]:
            bad[0] += 1
        done[0] += 1
ts = [threading.Thread(target=w, args=(i,)) for i in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
print(json.dumps({"done": done[0], "mismatch": bad[0]}))
"""


def test_fr_tts_xx_concurrent_synthesis_real():
    """文字播报（GUI 线程）与语音转换（流水线线程）可能同时合成：4 线程 × 300 次，子进程执行以隔离崩溃"""
    r = subprocess.run([sys.executable, "-c", _CONCURRENT], capture_output=True, text=True, timeout=300)
    RESULTS["concurrent"] = {"returncode": r.returncode, "stdout": r.stdout.strip()[-200:], "stderr": r.stderr.strip()[-300:]}
    assert r.returncode == 0, f"子进程异常退出 {r.returncode}: {r.stderr[-500:]}"
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res == {"done": 1200, "mismatch": 0}
