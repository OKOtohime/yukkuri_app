"""
QR-MNT-02：按需求口径统计上游单测的语句覆盖率

范围：g2p/、config.py、audio/{resample,capture,player,vad}.py、pipeline.py、tts/aquestalk1.py。
"不含设备 I/O"：下面 DEVICE_IO 中列出的函数整体排除（主要职责是打开声卡流、调用 pw-play/pactl
或驱动主循环），其余语句全部计入。

用法：
    python -m pytest tests --cov=yukkuri --cov-report=json:<cov.json>
    python qa/eval/mnt/coverage_scope.py <cov.json> [--repo <源码根目录>] [--out <结果.json>]
"""

import argparse
import ast
import json
import os

SCOPE = [
    "yukkuri/g2p/__init__.py", "yukkuri/g2p/normalizer.py", "yukkuri/g2p/polyglot.py",
    "yukkuri/g2p/dicts/__init__.py", "yukkuri/g2p/dicts/english.py", "yukkuri/g2p/dicts/pinyin.py",
    "yukkuri/config.py",
    "yukkuri/audio/resample.py", "yukkuri/audio/capture.py", "yukkuri/audio/player.py", "yukkuri/audio/vad.py",
    "yukkuri/pipeline.py",
    "yukkuri/tts/aquestalk1.py",
]

# (文件, 限定名) —— 设备 I/O
DEVICE_IO = {
    "yukkuri/audio/capture.py": {"MicrophoneStream.start", "MicrophoneStream.stop",
                                 "MicrophoneStream.__enter__", "MicrophoneStream.__exit__"},
    "yukkuri/audio/player.py": {"LinuxPipeWirePlayer._is_sink_available", "LinuxPipeWirePlayer._worker",
                                "WindowsWASAPIPlayer._get_or_create_stream", "WindowsWASAPIPlayer._play_streams",
                                "WindowsWASAPIPlayer._worker", "WindowsWASAPIPlayer.set_output_device",
                                "WindowsWASAPIPlayer.find_default_cable_input"},
    "yukkuri/pipeline.py": {"YukkuriPipeline.run"},
}


def _function_lines(path, names):
    tree = ast.parse(open(path, encoding="utf-8").read())
    lines = set()

    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef) and qual in names:
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    lines.update(range(start, child.end_lineno + 1))
                visit(child, qual + ".")

    visit(tree)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cov_json")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    ap.add_argument("--out")
    args = ap.parse_args()

    files = {k.replace("\\", "/"): v for k, v in json.load(open(args.cov_json))["files"].items()}
    rows, tot_s, tot_c = [], 0, 0
    for rel in SCOPE:
        data = next((v for k, v in files.items() if k.endswith(rel)), None)
        if data is None:
            rows.append({"file": rel, "statements": None, "covered": None, "pct": None, "note": "未被导入"})
            continue
        excl = _function_lines(os.path.join(args.repo, rel), DEVICE_IO.get(rel, set()))
        executed = set(data["executed_lines"])
        stmts = (executed | set(data["missing_lines"])) - excl
        cov = executed & stmts
        tot_s += len(stmts)
        tot_c += len(cov)
        rows.append({"file": rel, "statements": len(stmts), "covered": len(cov),
                     "pct": round(100 * len(cov) / len(stmts), 1) if stmts else 100.0,
                     "excluded_device_io": len(excl & (executed | set(data["missing_lines"])))})
    result = {"rows": rows, "total_statements": tot_s, "total_covered": tot_c,
              "total_pct": round(100 * tot_c / tot_s, 1) if tot_s else None, "threshold_pct": 80}
    for r in rows:
        print(f"{r['file']:<36} {r['statements']!s:>5} {r['covered']!s:>5} {r['pct']!s:>6}%")
    print(f"{'TOTAL':<36} {tot_s:>5} {tot_c:>5} {result['total_pct']:>6}%")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
