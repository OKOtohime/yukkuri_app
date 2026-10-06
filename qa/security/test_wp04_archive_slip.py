"""
WP-04 QR-SEC-03：归档导入安全（zip-slip）。设计见 docs/test-design/WP-04.md 2.3 节。

数据目录放在沙箱深处（<sbx>/s1/s2/s3/s4/data），任何越界写入仍落在沙箱内；执行前后对沙箱做快照，
判定"所有新增或修改的文件都在 data/libs/aquestalk/ 下"。导入经真实入口 `setup_models.py --aquestalk <归档>` 执行。
"""

import io
import json
import os
import sys
import tarfile
import zipfile

import pytest

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(QA_DIR, "unit"))

from wp04_util import IS_WIN, LIBNAME, REPO, changed_paths, ensure_report_dir, isolated_env, run_setup, snapshot  # noqa: E402

pytestmark = pytest.mark.l1

DEPTH = "s1/s2/s3/s4"
ALLOWED = f"{DEPTH}/data/libs/aquestalk/".replace("/", os.sep)
PAYLOAD = b"\x7fELF-not-really-a-library"
RESULTS = {}

# 成员名中的 {SBX} 在运行时替换为沙箱绝对路径（正斜杠）
ZIP_SAMPLES = {
    "S-01_dotdot": "../../evil.so",
    "S-02_absolute": "/abs/evil.so",
    "S-03_lib64_dotdot": "pkg/lib64/../../../../evil.so",
    "S-04_lib64_voice_dotdot": "pkg/lib64/f1/../../../../../evil.so",
    "S-05_subdir_dotdot": "pkg/a/../../evil.so",
    "S-06a_backslash": "pkg\\lib64\\..\\..\\..\\..\\evil.dll",
    "S-06b_lib64_backslash": "pkg/lib64/..\\..\\..\\..\\evil.dll",
}
WIN_ONLY = {
    "S-07_lib64_drive_letter": "pkg/lib64/{SBX}/s1/evil.dll",
}
TAR_SAMPLES = {f"S-08_tar_{k[5:]}": v for k, v in ZIP_SAMPLES.items() if k[:4] in ("S-01", "S-02", "S-03", "S-04", "S-05")}

# 已知结果（2026-10-07 实测登记；W1 另有 Windows 路径语义下的样本）
XFAIL = {
    "S-03_lib64_dotdot": "DEF-401", "S-04_lib64_voice_dotdot": "DEF-401",
    "S-08_tar_lib64_dotdot": "DEF-401", "S-08_tar_lib64_voice_dotdot": "DEF-401",
}
if IS_WIN:
    XFAIL.update({"S-06b_lib64_backslash": "DEF-401", "S-07_lib64_drive_letter": "DEF-401"})


@pytest.fixture(scope="module", autouse=True)
def dump_results():
    yield
    if RESULTS:
        path = os.path.join(ensure_report_dir(), f"qr_sec_03_{'W1' if IS_WIN else 'R1'}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, ensure_ascii=False, indent=1, sort_keys=True)


@pytest.fixture
def sbx(tmp_path):
    root = tmp_path / "sbx"
    data = root / DEPTH / "data"
    data.mkdir(parents=True)
    (root / "outside").mkdir()
    (root / "outside" / "secret.txt").write_bytes(b"outside-original")
    return root


def run_import(sbx, archive, key):
    data = str(sbx / DEPTH / "data")
    before = snapshot(str(sbx))
    env = isolated_env(str(sbx.parent / "home"), data)
    r = run_setup(["--aquestalk", str(archive)], env, urls=None)
    after = snapshot(str(sbx))
    changed = changed_paths(before, after)
    outside = [p for p in changed if not p.startswith(ALLOWED)]
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    RESULTS[key] = {"returncode": r.returncode, "written": changed, "outside": outside, "tail": tail,
                    "traceback": "Traceback" in r.stderr}
    return outside, r


def check(request, key, outside):
    if XFAIL.get(key):
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason=XFAIL[key]))
    assert outside == [], f"{key}: 写到了 libs/aquestalk/ 之外: {outside}"


def _name(template, sbx):
    return template.replace("{SBX}", str(sbx).replace("\\", "/"))


@pytest.mark.parametrize("key", list(ZIP_SAMPLES) + list(WIN_ONLY))
def test_qr_sec_03_zip_member_paths(sbx, tmp_path, request, key):
    if key in WIN_ONLY and not IS_WIN:
        pytest.skip("盘符路径只在 Windows 上有意义")
    template = ZIP_SAMPLES.get(key) or WIN_ONLY[key]
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(_name(template, sbx), PAYLOAD)
    outside, _ = run_import(sbx, archive, key)
    check(request, key, outside)


def _tar_with(archive, members):
    with tarfile.open(archive, "w:gz") as tf:
        for m in members:
            tf.addfile(m, io.BytesIO(PAYLOAD) if m.isfile() else None)


def _file(name):
    ti = tarfile.TarInfo(name)
    ti.size = len(PAYLOAD)
    return ti


def _link(name, target, kind=tarfile.SYMTYPE):
    ti = tarfile.TarInfo(name)
    ti.type = kind
    ti.linkname = target
    return ti


@pytest.mark.parametrize("key", list(TAR_SAMPLES))
def test_qr_sec_03_tar_member_paths(sbx, tmp_path, request, key):
    archive = tmp_path / "evil.tar.gz"
    _tar_with(archive, [_file(TAR_SAMPLES[key])])
    outside, _ = run_import(sbx, archive, key)
    check(request, key, outside)


@pytest.mark.parametrize("variant", ["absolute", "relative_outside"])
def test_qr_sec_03_tar_symlink_member(sbx, tmp_path, request, variant):
    """S-09：库文件位置上是指向包外的符号链接"""
    key = f"S-09_tar_symlink_{variant}"
    target = str(sbx / "outside" / "secret.txt") if variant == "absolute" else "../../../../../../outside/secret.txt"
    archive = tmp_path / "evil.tar.gz"
    _tar_with(archive, [_link(f"pkg/lib64/f1/{LIBNAME}", target)])
    outside, _ = run_import(sbx, archive, key)
    check(request, key, outside)
    assert (sbx / "outside" / "secret.txt").read_bytes() == b"outside-original"


def test_qr_sec_03_tar_symlink_dir_then_file(sbx, tmp_path, request):
    """S-10：先放指向沙箱外目录的符号链接目录，再放其下的库文件"""
    key = "S-10_tar_symlink_dir"
    archive = tmp_path / "evil.tar.gz"
    _tar_with(archive, [_link("pkg/lib64/f9", str(sbx / "outside")), _file(f"pkg/lib64/f9/{LIBNAME}")])
    outside, _ = run_import(sbx, archive, key)
    check(request, key, outside)


def test_qr_sec_03_tar_hardlink_member(sbx, tmp_path, request):
    """S-11：硬链接成员指向包外文件"""
    key = "S-11_tar_hardlink"
    archive = tmp_path / "evil.tar.gz"
    _tar_with(archive, [_link(f"pkg/lib64/f1/{LIBNAME}", str(sbx / "outside" / "secret.txt"), tarfile.LNKTYPE)])
    outside, _ = run_import(sbx, archive, key)
    check(request, key, outside)


@pytest.mark.linux_only
def test_qr_sec_03_dir_import_with_symlinks(sbx, tmp_path, request):
    """S-12：目录导入时源目录里的库文件/声线目录是指向外部的符号链接（只要求写入落在 libs/aquestalk/ 内）"""
    key = "S-12_dir_symlinks"
    src = tmp_path / "src" / "lib64"
    (src / "f1").mkdir(parents=True)
    os.symlink(sbx / "outside" / "secret.txt", src / "f1" / LIBNAME)
    os.symlink(sbx / "outside", src / "f2")
    (sbx / "outside" / LIBNAME).write_bytes(PAYLOAD)
    outside, _ = run_import(sbx, tmp_path / "src", key)
    check(request, key, outside)


def test_qr_sec_03_zip_symlink_entry(sbx, tmp_path, request):
    """zip 中带 Unix 符号链接属性的条目：不得在目标处创建指向外部的链接"""
    key = "S-13_zip_symlink_entry"
    archive = tmp_path / "evil.zip"
    zi = zipfile.ZipInfo(f"pkg/lib64/f1/{LIBNAME}")
    zi.create_system = 3
    zi.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(zi, str(sbx / "outside" / "secret.txt"))
    outside, _ = run_import(sbx, archive, key)
    check(request, key, outside)
    lib = sbx / DEPTH / "data" / "libs" / "aquestalk" / "lib64" / "f1" / LIBNAME
    assert not lib.is_symlink()


def test_qr_sec_03_gui_import_same_function():
    """GUI 模型管理的导入调用同一组函数，本文件的结论适用于 GUI 入口"""
    src = open(os.path.join(REPO, "yukkuri", "gui", "model_manager.py"), encoding="utf-8").read()
    assert "install_aquestalk_from_archive(" in src and "install_aquestalk_from_dir(" in src
    assert "extractall" not in src and "zipfile" not in src and "tarfile" not in src


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-413")
@pytest.mark.parametrize("key", ["S-09_tar_symlink_absolute", "S-11_tar_hardlink"])
def test_qr_use_02_malformed_archive_reported(key):
    """评审 Q8：导入异常归档时应给出可理解的提示，而不是 traceback（依赖上面用例写入的 RESULTS）"""
    res = RESULTS.get(key)
    if res is None:
        pytest.skip("前置用例未执行")
    assert not res["traceback"], res["tail"]
