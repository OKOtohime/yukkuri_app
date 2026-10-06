"""
WP-04 L2：AquesTalk 声线库导入（FR-MDL-05/06），使用真实的评价版官方包（Linux：aqtk1_lnx_200.zip；
Windows：aqtk1_win_200.zip，不入库，见 wp04_util.official_package）。

判定："每种声线都可用，彼此不覆盖"
    1) 导入后 libs/aquestalk/lib64/<声线>/<库> 与包内 lib64/<声线>/<库> 逐字节相同（9 种）；
    2) 在独立子进程中用产品引擎逐个加载 9 种声线并合成成功。
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(QA_DIR, "unit"))

from wp04_util import (  # noqa: E402
    IS_WIN, LIBNAME, VOICES, ensure_report_dir, installed_hashes, isolated_env, model_status, official_package,
    package_lib64_hashes, run_setup,
)

pytestmark = pytest.mark.l2
RESULTS = {}

_LOAD_ALL = r"""
import json, sys
from yukkuri.config import AppConfig
from yukkuri.tts.aquestalk1 import AquesTalk1Engine
cfg = AppConfig(project_root=sys.argv[1])
out = {}
for v in %r:
    try:
        e = AquesTalk1Engine(voice=v, voice_resolver=cfg.find_aquestalk_library)
        w = e.synthesize("ゆっくりしていってね")
        out[v] = bool(w) and len(w)
    except Exception as ex:
        out[v] = f"{type(ex).__name__}: {ex}"[:200]
print(json.dumps(out))
""" % (VOICES,)


@pytest.fixture(scope="module", autouse=True)
def dump_results():
    yield
    if RESULTS:
        path = os.path.join(ensure_report_dir(), f"fr_mdl_05_{'W1' if IS_WIN else 'R1'}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, ensure_ascii=False, indent=1, sort_keys=True)


@pytest.fixture(scope="module")
def pkg():
    return official_package()


@pytest.fixture(scope="module")
def expected(pkg):
    h = package_lib64_hashes(pkg)
    assert sorted(h) == sorted(VOICES)
    return h


def load_all(data, home):
    env = isolated_env(home, data)
    r = subprocess.run([sys.executable, "-c", _LOAD_ALL, data], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def import_and_judge(tmp_path, source, expected, key):
    data = str(tmp_path / "data")
    os.makedirs(data, exist_ok=True)
    home = str(tmp_path / "home")
    r = run_setup(["--aquestalk", str(source)], isolated_env(home, data), urls=None)
    got = installed_hashes(data)
    wrong = sorted(v for v in VOICES if got.get(v) != expected[v])
    loads = load_all(data, home)
    failed = sorted(v for v, ok in loads.items() if not isinstance(ok, int) or ok <= 0)
    RESULTS[key] = {"returncode": r.returncode, "wrong_or_missing": wrong, "load_failed": {v: loads[v] for v in failed},
                    "tail": (r.stdout + r.stderr).strip().splitlines()[-2:]}
    assert wrong == [] and failed == [], f"{key}: 库不一致 {wrong}；加载失败 {RESULTS[key]['load_failed']}"
    return data


def _repack_tar(pkg, out, mode, names=None):
    with zipfile.ZipFile(pkg) as zf, tarfile.open(out, mode) as tf:
        for info in (names or zf.infolist()):
            ti = tarfile.TarInfo(info.filename.rstrip("/"))
            if info.is_dir():
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            else:
                data = zf.read(info)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))


def _extract(pkg, dest):
    with zipfile.ZipFile(pkg) as zf:
        zf.extractall(dest)
        return zf.namelist()[0].split("/")[0]


# ---------------------------------------------------------------- FR-MDL-05：格式

FORMATS = ["zip", "tar.gz", "tgz", "tar.bz2", "dir_top", "dir_parent", "dir_lib64"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_fr_mdl_05_import_formats(tmp_path, pkg, expected, fmt):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    if fmt == "zip":
        source = pkg
    elif fmt in ("tar.gz", "tgz", "tar.bz2"):
        source = src_dir / f"aqtk.{fmt}"
        _repack_tar(pkg, source, "w:bz2" if fmt == "tar.bz2" else "w:gz")
    else:
        top = _extract(pkg, src_dir)
        source = {"dir_top": src_dir / top, "dir_parent": src_dir, "dir_lib64": src_dir / top / "lib64"}[fmt]
    import_and_judge(tmp_path, source, expected, f"format:{fmt}")


# ---------------------------------------------------------------- FR-MDL-05：成员顺序

@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-402")
@pytest.mark.parametrize("fmt", ["zip", "tar.gz"])
def test_fr_mdl_05_member_order_independent(tmp_path, pkg, expected, fmt):
    """同一官方包内容，成员顺序改为 lib64 在前、32 位库（Linux：lib32；Windows：lib）在后"""
    with zipfile.ZipFile(pkg) as zf:
        infos = zf.infolist()
    reordered = [i for i in infos if "/lib64/" in i.filename] + [i for i in infos if "/lib64/" not in i.filename]
    source = tmp_path / f"reordered.{fmt}"
    if fmt == "zip":
        with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(source, "w") as dst:
            for i in reordered:
                dst.writestr(i, src.read(i))
    else:
        _repack_tar(pkg, source, "w:gz", reordered)
    import_and_judge(tmp_path, source, expected, f"order:{fmt}")


def _native_repack_cmds(src_dir, top, out_base):
    """用户最可能用的工具把解压出的官方目录重新打包"""
    cmds = {}
    if IS_WIN:
        cmds["tar.exe czf"] = (["tar", "czf", f"{out_base}.tar.gz", "-C", str(src_dir), top], f"{out_base}.tar.gz")
        ps = f"Compress-Archive -Path '{src_dir / top}' -DestinationPath '{out_base}-ps.zip'"
        cmds["Compress-Archive"] = (["powershell", "-NoProfile", "-Command", ps], f"{out_base}-ps.zip")
    else:
        if shutil.which("tar"):
            cmds["tar czf"] = (["tar", "czf", f"{out_base}.tar.gz", "-C", str(src_dir), top], f"{out_base}.tar.gz")
        if shutil.which("zip"):
            cmds["zip -r"] = (["sh", "-c", f"cd '{src_dir}' && zip -qr '{out_base}.zip' '{top}'"], f"{out_base}.zip")
    return cmds


@pytest.mark.xfail(not IS_WIN, strict=True, raises=AssertionError, reason="DEF-402（R1：tar 与 zip 都按目录读取顺序写入，lib64 在 lib32 之前）")
def test_fr_mdl_05_repacked_by_native_tools(tmp_path, pkg, expected):
    """把解压出的官方目录用本平台常用工具重新打包后导入（tar.gz 是 FR-MDL-05 列出的格式）。
    结果取决于工具写入成员的顺序，逐个工具记录；任何一个失败都判不通过"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    top = _extract(pkg, src_dir)
    failures = {}
    for tool, (cmd, out) in _native_repack_cmds(src_dir, top, str(tmp_path / "repack")).items():
        subprocess.run(cmd, check=True, capture_output=True)
        if out.endswith(".zip"):
            with zipfile.ZipFile(out) as zf:
                order = [n for n in zf.namelist() if n.endswith(LIBNAME) and "/f1/" in n.replace("\\", "/")]
        else:
            with tarfile.open(out) as tf:
                order = [n for n in tf.getnames() if n.endswith(LIBNAME) and "/f1/" in n]
        sub = tmp_path / tool.replace(" ", "_").replace(".", "_")
        sub.mkdir()
        try:
            import_and_judge(sub, out, expected, f"native:{tool}")
        except AssertionError as e:
            failures[tool] = str(e)[:300]
        RESULTS[f"native:{tool}"]["f1_member_order"] = order
    if not _native_repack_cmds(src_dir, top, "x"):
        pytest.skip("本机没有可用的打包工具")
    assert failures == {}, failures


# ---------------------------------------------------------------- FR-MDL-05：其他

def _single_lib(pkg, dest):
    with zipfile.ZipFile(pkg) as zf:
        name = next(n for n in zf.namelist() if n.endswith(f"lib64/f1/{LIBNAME}"))
        dest.write_bytes(zf.read(name))
    return dest


def test_fr_mdl_05_single_library_usable(tmp_path, pkg, expected):
    lib = _single_lib(pkg, tmp_path / LIBNAME)
    data = str(tmp_path / "data")
    os.makedirs(data)
    run_setup(["--aquestalk", str(lib)], isolated_env(str(tmp_path / "home"), data), urls=None)
    assert load_all(data, str(tmp_path / "home"))["f1"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-205")
def test_fr_mdl_05_single_library_only_one_voice(tmp_path, pkg):
    lib = _single_lib(pkg, tmp_path / LIBNAME)
    data = str(tmp_path / "data")
    os.makedirs(data)
    run_setup(["--aquestalk", str(lib)], isolated_env(str(tmp_path / "home"), data), urls=None)
    assert model_status(data)["aquestalk_voices"] == ["f1"]


def test_fr_mdl_05_reimport_idempotent(tmp_path, pkg, expected):
    data = import_and_judge(tmp_path, pkg, expected, "reimport:1")
    before = installed_hashes(data)
    r = run_setup(["--aquestalk", pkg], isolated_env(str(tmp_path / "home"), data), urls=None)
    assert r.returncode == 0 and "[成功]" in r.stdout
    assert installed_hashes(data) == before


def _invalid_input(kind, tmp_path):
    p = tmp_path / "in"
    p.mkdir(exist_ok=True)
    if kind == "missing":
        return p / "nope.zip"
    if kind == "empty_zip":
        out = p / "empty.zip"
        zipfile.ZipFile(out, "w").close()
        return out
    if kind == "zip_without_libs":
        out = p / "nolib.zip"
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("aqtk1_lnx/readme.txt", "hello")
        return out
    if kind == "corrupt_zip":
        out = p / "corrupt.zip"
        out.write_bytes(os.urandom(4096))
        return out
    if kind == "corrupt_tgz":
        out = p / "corrupt.tar.gz"
        out.write_bytes(os.urandom(4096))
        return out
    if kind == "rar":
        out = p / "aqtk.rar"
        out.write_bytes(b"Rar!\x1a\x07\x00" + os.urandom(1000))
        return out
    if kind == "empty_dir":
        (p / "emptydir").mkdir()
        return p / "emptydir"
    raise ValueError(kind)


INVALID = ["missing", "empty_zip", "zip_without_libs", "corrupt_zip", "corrupt_tgz", "rar", "empty_dir"]


@pytest.mark.parametrize("kind", INVALID)
def test_fr_mdl_05_invalid_inputs_message(tmp_path, request, kind):
    """空数据目录导入无效输入：给出错误提示，不出现 traceback（损坏的归档见评审 Q8 → DEF-413）"""
    if kind in ("corrupt_zip", "corrupt_tgz"):
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-413"))
    data = str(tmp_path / "data")
    os.makedirs(data)
    r = run_setup(["--aquestalk", str(_invalid_input(kind, tmp_path))], isolated_env(str(tmp_path / "home"), data), urls=None)
    out = r.stdout + r.stderr
    RESULTS[f"invalid:{kind}"] = {"returncode": r.returncode, "tail": out.strip().splitlines()[-3:]}
    assert "Traceback" not in out, out[-600:]
    assert "[错误]" in out
    assert model_status(data)["aquestalk_count"] == 0


@pytest.mark.parametrize("kind", ["corrupt_zip", "rar", "empty_dir"])
def test_fr_mdl_05_invalid_input_keeps_existing(tmp_path, pkg, expected, kind):
    """已有声线时导入无效输入：已有声线不受影响"""
    data = import_and_judge(tmp_path, pkg, expected, f"existing:{kind}")
    before = installed_hashes(data)
    r = run_setup(["--aquestalk", str(_invalid_input(kind, tmp_path))], isolated_env(str(tmp_path / "home"), data), urls=None)
    RESULTS[f"existing:{kind}"]["second_tail"] = (r.stdout + r.stderr).strip().splitlines()[-2:]
    assert installed_hashes(data) == before


# ---------------------------------------------------------------- FR-MDL-06

def _home_with(tmp_path, rel, pkg, as_dir=False):
    home = tmp_path / "home"
    target = home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if as_dir:
        tmp = tmp_path / "x"
        top = _extract(pkg, tmp)
        shutil.move(str(tmp / top), str(target))
    else:
        shutil.copy(pkg, target)
    return str(home)


OFFICIAL_ZIP = os.path.basename("aqtk1_win_200.zip" if IS_WIN else "aqtk1_lnx_200.zip")
OFFICIAL_DIR = "aqtk1_win" if IS_WIN else "aqtk1_lnx"
JUDGED = {  # 评审 Q4：只判定官方原名压缩包放在 ~/Downloads（含 ~/下载）
    "Downloads_zip": (f"Downloads/{OFFICIAL_ZIP}", False),
    "下载_zip": (f"下载/{OFFICIAL_ZIP}", False),
}
RECORDED = {  # 只记录，不判定
    "Desktop_zip": (f"Desktop/{OFFICIAL_ZIP}", False),
    "Downloads_dir": (f"Downloads/{OFFICIAL_DIR}", True),
    "Desktop_dir": (f"Desktop/{OFFICIAL_DIR}", True),
}


@pytest.mark.parametrize("where", list(JUDGED))
def test_fr_mdl_06_autodetect_downloads(tmp_path, pkg, expected, request, where):
    if IS_WIN:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEF-411"))
    rel, as_dir = JUDGED[where]
    home = _home_with(tmp_path, rel, pkg, as_dir)
    data = str(tmp_path / "data")
    os.makedirs(data)
    r = run_setup(["--aquestalk"], isolated_env(home, data), urls=None)
    found = model_status(data)["aquestalk_count"]
    RESULTS[f"autodetect:{where}"] = {"found_voices": found, "tail": (r.stdout + r.stderr).strip().splitlines()[-2:]}
    assert found == 9, r.stdout[-600:]
    assert installed_hashes(data) == expected


@pytest.mark.parametrize("where", list(RECORDED))
def test_fr_mdl_06_autodetect_other_locations_recorded(tmp_path, pkg, where):
    rel, as_dir = RECORDED[where]
    home = _home_with(tmp_path, rel, pkg, as_dir)
    data = str(tmp_path / "data")
    os.makedirs(data)
    run_setup(["--aquestalk"], isolated_env(home, data), urls=None)
    RESULTS[f"autodetect:{where}"] = {"found_voices": model_status(data)["aquestalk_count"], "judged": False}


def test_fr_mdl_06_not_found_guidance(tmp_path):
    data = str(tmp_path / "data")
    os.makedirs(data)
    r = run_setup(["--aquestalk"], isolated_env(str(tmp_path / "home"), data), urls=None)
    assert "https://www.a-quest.com/download.html" in r.stdout
    assert "setup_models.py --aquestalk" in r.stdout
