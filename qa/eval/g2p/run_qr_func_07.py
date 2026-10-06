"""
QR-FUNC-07：G2P 输出可合成性评测。

    python qa/eval/g2p/run_qr_func_07.py [--voice f1] [--out qa/reports/wp03/qr_func_07_<env>.json] [--limit N]

流程：fuzz_corpus.generate() → G2P（主进程）→ AquesTalk 合成（aqtk_worker 子进程，崩溃可定位）。
判定（需求 5.1）：非空音标中返回 105/107 的比例 ≤ 0.1%，且无崩溃。102/204 等其他错误码单独统计（评审 Q7）。
同时记录 L1 预判表（Alphabet）与真实库结论的一致率，用于确认 L1 预判可信。

平台差异：Windows 评价版 DLL 遇到未定义记号时，写回的不是 105，而是该记号的 1 起算字节位置
（"づ"→1，"あづ"→4），与真正的错误码取值重叠。因此"105/107 类失败"按原因判定：
返回非 0，且返回码为 105/107 或音标中含预判表认定的不可接受记号（symbol 类）；其余非 0 为 other 类（超长等）。
"""

import argparse
import collections
import json
import os
import platform
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
QA_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(QA_DIR, "unit"))

from aqtk_client import CRASH, AqtkClient  # noqa: E402
from fuzz_corpus import generate  # noqa: E402

JUDGED = {105, 107}


def enc_of(name):
    return "shift-jis" if name == "shift-jis" else "utf-8"
THRESHOLD = 0.001


def evaluate(voice="f1", limit=None, items=None):
    from yukkuri.config import AppConfig
    from yukkuri.g2p import text_to_yukkuri_polyglot as g2p
    from wp03_util import Alphabet

    lib = AppConfig().find_aquestalk_library(voice)
    if not lib:
        raise RuntimeError(f"未找到声线 {voice}")
    items = items if items is not None else generate()
    if limit:
        items = items[:limit]
    alphabet = Alphabet()

    g2p_errors = []
    for it in items:
        try:
            it["koe"] = g2p(it["text"], it["lang"])
        except Exception as e:  # G2P 自身抛异常也算"崩溃"类问题
            it["koe"] = ""
            g2p_errors.append({"id": it["id"], "error": repr(e)})

    t0 = time.time()
    with AqtkClient(lib) as c:
        for it in items:
            if it["koe"]:
                it["code"], it["wav_bytes"] = c.synth(it["koe"])
            else:
                it["code"], it["wav_bytes"] = None, 0
        crashes, encoding = c.crashes, c.encoding
    elapsed = time.time() - t0

    for x in items:
        p = alphabet.problems(x["koe"]) if x["koe"] else []
        x["symbol_failure"] = bool(x["koe"]) and x["code"] != 0 and (x["code"] in JUDGED or bool(p))
        x["position_code"] = bool(p) and x["code"] == len(x["koe"][:p[0][0]].encode(enc_of(encoding))) + 1

    def stats(sub):
        nonempty = [x for x in sub if x["koe"]]
        codes = collections.Counter(x["code"] for x in nonempty)
        judged = sum(x["symbol_failure"] for x in nonempty)
        return {
            "items": len(sub),
            "nonempty_koe": len(nonempty),
            "codes": {str(k): v for k, v in sorted(codes.items(), key=lambda kv: str(kv[0]))},
            "judged_failures": judged,
            "judged_rate": round(judged / len(nonempty), 6) if nonempty else 0.0,
            "other_failures": sum(1 for x in nonempty if x["code"] not in (0, CRASH) and not x["symbol_failure"]),
            "symbol_failures_reported_as_position": sum(x["position_code"] for x in nonempty if x["symbol_failure"]),
        }

    per_subset = {s: stats([x for x in items if x["subset"] == s]) for s in sorted({x["subset"] for x in items})}
    overall = stats(items)
    nonempty = [x for x in items if x["koe"]]
    agree = sum((x["code"] == 0) == alphabet.ok(x["koe"]) for x in nonempty)
    disagreements = [
        {"id": x["id"], "koe": x["koe"], "code": x["code"], "predicted_ok": alphabet.ok(x["koe"])}
        for x in nonempty if (x["code"] == 0) != alphabet.ok(x["koe"])
    ]

    # 失败原因归类：按 L1 预判给出的第一个问题字符
    cause = collections.Counter()
    for x in nonempty:
        if x["symbol_failure"]:
            p = alphabet.problems(x["koe"])
            cause[p[0][1] if p else "（预判未发现）"] += 1

    # 失败明细：子集 A 全部列出；B/C 各列前 100 条（数量见 per_subset，全部可由固定种子重新生成）
    failures, shown = [], collections.Counter()
    for x in nonempty:
        if x["code"] == 0:
            continue
        if x["subset"] != "A" and shown[x["subset"]] >= 100:
            continue
        shown[x["subset"]] += 1
        failures.append({k: x[k] for k in ("id", "subset", "lang", "text", "koe", "code", "symbol_failure")})
    return {
        "requirement": "QR-FUNC-07",
        "platform": platform.platform(),
        "voice": voice,
        "library_encoding": encoding,
        "threshold": THRESHOLD,
        "overall": overall,
        "per_subset": per_subset,
        "passed": overall["judged_rate"] <= THRESHOLD and not crashes and not g2p_errors,
        "crashes": crashes,
        "g2p_exceptions": g2p_errors,
        "failure_cause_first_char": dict(cause.most_common()),
        "l1_predictor_agreement": round(agree / len(nonempty), 6) if nonempty else None,
        "l1_predictor_disagreements": disagreements[:200],
        "l1_predictor_disagreement_count": len(disagreements),
        "synth_seconds": round(elapsed, 1),
        "failures": failures,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="f1")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default=os.path.join(QA_DIR, "reports", "wp03", "qr_func_07.json"))
    args = ap.parse_args()
    res = evaluate(args.voice, args.limit)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    o = res["overall"]
    print(f"{res['platform']} voice={res['voice']} enc={res['library_encoding']}")
    print(f"非空音标 {o['nonempty_koe']}/{o['items']}；记号类失败 {o['judged_failures']}（{o['judged_rate']:.3%}，其中以位置值返回 {o['symbol_failures_reported_as_position']}）；其他失败 {o['other_failures']}")
    for s, v in res["per_subset"].items():
        print(f"  子集 {s}: 非空 {v['nonempty_koe']}，记号类失败 {v['judged_failures']}（{v['judged_rate']:.3%}），其他失败 {v['other_failures']}")
    print(f"崩溃 {len(res['crashes'])}；G2P 异常 {len(res['g2p_exceptions'])}；L1 预判一致率 {res['l1_predictor_agreement']}")
    print("失败首因:", res["failure_cause_first_char"])
    print("判定:", "通过" if res["passed"] else "不通过", "→", args.out)


if __name__ == "__main__":
    main()
