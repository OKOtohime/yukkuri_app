"""
WP-04 QR-REL-07：下载鲁棒性故障注入矩阵（设计见 docs/test-design/WP-04.md 2.4 节）。

每格判定三条：
    ① 给出提示：输出中有该项的错误提示，且没有宣称该项"完成"
    ② 不留下被误判为就绪的半成品：--check 或加载侧认为就绪时，落盘内容必须与发布物逐字节一致
    ③ 可以重试：故障消除后再次执行同一命令即成功（不需要手工删除文件）
停滞（F-5）按评审 Q2：60 s 内应给出超时提示并结束该项。
"""

import json
import os
import signal
import shutil
import subprocess
import sys
import time

import pytest

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(QA_DIR, "unit"))

from wp04_util import IS_WIN, QA_DIR as _QA, ensure_report_dir, isolated_env, loader_view, model_status, run_setup  # noqa: E402
from model_server import ModelServer, build_artifacts, closed_port_url  # noqa: E402
from test_wp04_mdl import STATUS_KEY, content_ok  # noqa: E402

pytestmark = pytest.mark.l1

ITEM_ARGS = {"vad": ["--vad"], "sensevoice": ["--sensevoice"], "vosk-en": ["--vosk", "en"]}
ITEM_LABEL = {"vad": "Silero-VAD", "sensevoice": "SenseVoice", "vosk-en": "Vosk [en]"}
PROGRESS_LABEL = {"vad": "Silero-VAD", "sensevoice": "SenseVoice", "vosk-en": "Vosk-en"}  # 进度条前缀
ITEM_FILE = {"vad": "silero_vad.onnx", "sensevoice": "sensevoice.tar.bz2", "vosk-en": "vosk-en.zip"}
STALL_LIMIT = 60
RESULTS = {}


@pytest.fixture(scope="module")
def server():
    with ModelServer(build_artifacts()) as s:
        yield s


@pytest.fixture(scope="module", autouse=True)
def dump_results():
    yield
    if RESULTS:
        path = os.path.join(ensure_report_dir(), f"qr_rel_07_{'W1' if IS_WIN else 'R1'}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, ensure_ascii=False, indent=1, sort_keys=True)


def judge(data, item, arts, out, *, check_prompt=True):
    """返回 (①, ②, 证据)"""
    label = ITEM_LABEL[item]
    lines = out.splitlines()
    err = [ln for ln in lines if label in ln and ("[错误]" in ln or "失败" in ln)]
    done = [ln for ln in lines if label in ln and "[完成]" in ln]
    prompt_ok = bool(err) and not done
    st = model_status(data)[STATUS_KEY[item]]
    lv = bool(loader_view(data)[STATUS_KEY[item]])
    intact = content_ok(data, item, arts)
    no_misjudge = intact or not (st or lv)
    leftovers = sorted(os.listdir(data))
    return prompt_ok, no_misjudge, {"error_lines": err, "done_lines": done, "check_ready": st, "loader_ready": lv,
                                    "content_intact": intact, "files": leftovers}


def retry_ok(data, env, server, item):
    r = run_setup(ITEM_ARGS[item], env, server.url_map())
    return content_ok(data, item, server.artifacts) and model_status(data)[STATUS_KEY[item]], r.stdout[-600:]


def record_and_assert(key, prompt_ok, no_misjudge, retried, evidence, *, check_prompt=True):
    RESULTS[key] = {"prompt": prompt_ok, "no_misjudge": no_misjudge, "retry": retried, **evidence}
    problems = []
    if check_prompt and not prompt_ok:
        problems.append("① 没有错误提示（或宣称完成）")
    if not no_misjudge:
        problems.append("② 半成品被 --check/加载侧判为就绪")
    if not retried:
        problems.append("③ 故障消除后重试仍不成功")
    assert not problems, f"{key}: {problems}; 证据: {json.dumps(evidence, ensure_ascii=False)[:600]}"


# 已知结果（2026-10-07 R1 实测登记）：键为 (故障, 对象)
XFAIL = {
    ("F2_trunc_cl", "vad"): "DEF-408", ("F2_trunc_cl", "sensevoice"): "DEF-408",
    ("F3_trunc_nocl", "vad"): "DEF-408", ("F3_trunc_nocl", "sensevoice"): "DEF-408",
    ("F6_sigint", "vad"): "DEF-408", ("F6_sigint", "sensevoice"): "DEF-408",
    ("F7_enospc_download", "vad"): "DEF-408", ("F7_enospc_download", "sensevoice"): "DEF-408",
    ("F7_enospc_extract", "sensevoice"): "DEF-408",
    ("F5_stall", "vad"): "DEF-409",
}


def maybe_xfail(request, fault, item):
    reason = XFAIL.get((fault, item))
    if reason:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason))


@pytest.mark.parametrize("item", list(ITEM_ARGS))
@pytest.mark.parametrize("fault", ["F1_refused", "F2_trunc_cl", "F3_trunc_nocl", "F8_404"])
def test_qr_rel_07_download_faults(tmp_path, server, request, fault, item):
    maybe_xfail(request, fault, item)
    data = str(tmp_path / "data")
    os.makedirs(data)
    env = isolated_env(str(tmp_path / "home"), data)
    urls = server.url_map()
    key = {"vad": "vad", "sensevoice": "sensevoice", "vosk-en": "vosk-en"}[item]
    if fault == "F1_refused":
        urls[key] = closed_port_url(ITEM_FILE[item])
    else:
        urls[key] = server.url({"F2_trunc_cl": "trunc_cl", "F3_trunc_nocl": "trunc_nocl", "F8_404": "404"}[fault], ITEM_FILE[item])
    r = run_setup(ITEM_ARGS[item], env, urls)
    prompt_ok, no_misjudge, ev = judge(data, item, server.artifacts, r.stdout + r.stderr)
    ev["returncode"] = r.returncode
    retried, ev["retry_tail"] = retry_ok(data, env, server, item)
    record_and_assert(f"{fault}|{item}", prompt_ok, no_misjudge, retried, ev)


def test_qr_rel_07_slow_network_completes(tmp_path, server):
    """F-4：限速 200 KB/s 可以完成，过程中持续显示进度"""
    data = str(tmp_path / "data")
    os.makedirs(data)
    env = isolated_env(str(tmp_path / "home"), data)
    r = run_setup(["--vad"], env, server.url_map({"vad": "slow"}))
    assert r.returncode == 0 and content_ok(data, "vad", server.artifacts)
    assert r.stdout.count("%") >= 5


def test_qr_rel_07_stall_times_out(tmp_path, server, request):
    """F-5（评审 Q2）：发完响应头后停滞，60 s 内应给出超时提示并结束该项"""
    maybe_xfail(request, "F5_stall", "vad")
    data = str(tmp_path / "data")
    os.makedirs(data)
    env = isolated_env(str(tmp_path / "home"), data)
    t0 = time.time()
    try:
        r = run_setup(["--vad"], env, server.url_map({"vad": "stall"}), timeout=STALL_LIMIT + 15)
        out, hung = r.stdout + r.stderr, False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        hung = True
    elapsed = time.time() - t0
    prompt_ok, no_misjudge, ev = judge(data, "vad", server.artifacts, out)
    ev.update(hung=hung, elapsed_s=round(elapsed, 1))
    retried, ev["retry_tail"] = retry_ok(data, env, server, "vad")
    RESULTS["F5_stall|vad"] = {"prompt": prompt_ok and not hung, "no_misjudge": no_misjudge, "retry": retried, **ev}
    assert not hung, f"停滞 {STALL_LIMIT + 15}s 后进程仍未结束，没有任何超时提示；证据: {ev}"
    record_and_assert("F5_stall|vad", prompt_ok, no_misjudge, retried, ev)


@pytest.mark.linux_only
@pytest.mark.parametrize("item", ["vad", "sensevoice"])
def test_qr_rel_07_user_interrupt(tmp_path, server, request, item):
    """F-6：下载中按 Ctrl+C（SIGINT）。用户主动中断，不判①；判②③"""
    maybe_xfail(request, "F6_sigint", item)
    data = str(tmp_path / "data")
    os.makedirs(data)
    env = isolated_env(str(tmp_path / "home"), data, QA_MODEL_URLS=json.dumps(server.url_map({item: "slow"})))
    p = subprocess.Popen([sys.executable, os.path.join(_QA, "harness", "run_setup_models.py"), *ITEM_ARGS[item]],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    target = os.path.join(data, "silero_vad.onnx" if item == "vad" else "sensevoice.tar.bz2")
    deadline = time.time() + 30
    while time.time() < deadline and not (os.path.exists(target) and os.path.getsize(target) > 100_000):
        time.sleep(0.05)
    p.send_signal(signal.SIGINT)
    out = p.communicate(timeout=30)[0].decode("utf-8", "replace")
    _, no_misjudge, ev = judge(data, item, server.artifacts, out)
    ev["returncode"] = p.returncode
    retried, ev["retry_tail"] = retry_ok(data, env, server, item)
    record_and_assert(f"F6_sigint|{item}", True, no_misjudge, retried, ev, check_prompt=False)


# ---------------------------------------------------------------- 磁盘空间不足（Linux，用户命名空间 + tmpfs）

# 用户命名空间里不能 remount 改容量，所以挂 256 MB tmpfs，再用占位文件只留下 {free_kb} KB 空间；
# "清理出空间后重试" = 删除占位文件（用户真实会做的事）。
_ENOSPC_SCRIPT = r"""
set -u
mount -t tmpfs -o size=256m tmpfs "$QA_MNT" || exit 97
mkdir -p "$QA_DATA"
"$QA_PY" "$QA_JUDGE_PY" "$QA_DATA" {key} prep || exit 96
avail_kb=$(df -k --output=avail "$QA_MNT" | tail -1)
fill_kb=$((avail_kb - {free_kb}))
dd if=/dev/zero of="$QA_MNT/filler" bs=1k count=$fill_kb status=none || exit 98
"$QA_PY" "$QA_RUNNER" {args} > "$QA_OUT1" 2>&1
echo "rc=$?" >> "$QA_OUT1"
"$QA_PY" "$QA_JUDGE_PY" "$QA_DATA" {key} > "$QA_JUDGE1"
rm -f "$QA_MNT/filler"
"$QA_PY" "$QA_RUNNER" {args} > "$QA_OUT2" 2>&1
"$QA_PY" "$QA_JUDGE_PY" "$QA_DATA" {key} > "$QA_JUDGE2"
"""

_JUDGE_PY = r"""
import json, os, sys
sys.path.insert(0, os.environ["QA_UNIT"])
sys.path.insert(0, os.path.join(os.path.dirname(os.environ["QA_UNIT"]), "harness"))
from wp04_util import model_status, loader_view
from test_wp04_mdl import content_ok, STATUS_KEY
from model_server import build_artifacts
d, key = sys.argv[1], sys.argv[2]
if sys.argv[3:] == ["prep"]:
    # --sensevoice 会先下载 VAD：预先放好完整的 VAD，让空间只消耗在 SenseVoice 上
    if key == "sensevoice":
        with open(os.path.join(d, "silero_vad.onnx"), "wb") as f:
            f.write(build_artifacts(scale=3)["silero_vad.onnx"])
    sys.exit(0)
k = STATUS_KEY[key]
print(json.dumps(dict(status=model_status(d)[k], loader=bool(loader_view(d)[k]),
                      intact=content_ok(d, key, build_artifacts(scale=3)), files=sorted(os.listdir(d)))))
"""


def _unshare_ok():
    if not sys.platform.startswith("linux") or not shutil.which("unshare"):
        return False
    return subprocess.run(["unshare", "-rm", "true"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def big_server():
    with ModelServer(build_artifacts(scale=3)) as s:
        yield s


@pytest.mark.linux_only
@pytest.mark.parametrize("item,free_kb,phase", [
    # 假模型（scale=3）体积：VAD 1.9 MB；SenseVoice 归档 2.7 MB、解压后约 2.0 MB（另有 0.9 MB 的 model.onnx）；
    # Vosk-en 归档 0.97 MB、解压后约 0.97 MB
    ("vad", 1024, "download"),
    ("sensevoice", 1024, "download"),
    ("sensevoice", 3400, "extract"),
    ("vosk-en", 512, "download"),
    ("vosk-en", 1400, "extract"),
])
def test_qr_rel_07_disk_full(tmp_path, big_server, request, item, free_kb, phase):
    """F-7：数据目录所在的文件系统只剩 free_kb（真实 ENOSPC）。download：下载过程中写满；extract：压缩包放得下、解压时写满"""
    maybe_xfail(request, f"F7_enospc_{phase}", item)
    if not _unshare_ok():
        pytest.skip("不支持非特权用户命名空间")
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    data = mnt / "data"
    judge_py = tmp_path / "judge.py"
    judge_py.write_text(_JUDGE_PY, encoding="utf-8")
    env = isolated_env(str(tmp_path / "home"), str(data), QA_MODEL_URLS=json.dumps(big_server.url_map()),
                       QA_MNT=str(mnt), QA_DATA=str(data), QA_PY=sys.executable,
                       QA_RUNNER=os.path.join(_QA, "harness", "run_setup_models.py"), QA_JUDGE_PY=str(judge_py),
                       QA_UNIT=os.path.join(_QA, "unit"), QA_OUT1=str(tmp_path / "out1"), QA_OUT2=str(tmp_path / "out2"),
                       QA_JUDGE1=str(tmp_path / "j1"), QA_JUDGE2=str(tmp_path / "j2"))
    script = _ENOSPC_SCRIPT.format(free_kb=free_kb, args=" ".join(ITEM_ARGS[item]), key=item)
    r = subprocess.run(["unshare", "-rm", "sh", "-c", script], env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode not in (96, 97, 98), r.stderr
    out1 = (tmp_path / "out1").read_text(encoding="utf-8", errors="replace")
    # 工具自检：确实发生了 ENOSPC，且发生在预定阶段（extract 阶段要求归档已完整下载）
    assert "Errno 28" in out1 or "No space" in out1, f"未触发 ENOSPC，检查 free_kb：{out1[-400:]}"
    progress = [ln for ln in out1.replace("\r", "\n").splitlines() if ln.strip().startswith(PROGRESS_LABEL[item] + ":")]
    downloaded_fully = any("100%" in ln for ln in progress)
    assert downloaded_fully == (phase == "extract"), f"ENOSPC 发生的阶段与预期（{phase}）不符：{out1[-400:]}"
    j1 = json.loads((tmp_path / "j1").read_text())
    j2 = json.loads((tmp_path / "j2").read_text())
    label = ITEM_LABEL[item]
    err = [ln for ln in out1.splitlines() if label in ln and ("[错误]" in ln or "失败" in ln)]
    done = [ln for ln in out1.splitlines() if label in ln and "[完成]" in ln]
    ev = {"error_lines": err, "done_lines": done, "after_fault": j1, "after_retry": j2, "tail": out1[-400:]}
    prompt_ok = bool(err) and not done
    no_misjudge = j1["intact"] or not (j1["status"] or j1["loader"])
    retried = j2["status"] and j2["intact"]
    record_and_assert(f"F7_enospc_{phase}|{item}", prompt_ok, no_misjudge, retried, ev)
