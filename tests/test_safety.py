from __future__ import annotations

import os

import pytest
from conftest import SESSION1

from rawpick import safety
from rawpick.safety import UnsafePathError


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "a\\b", ".hidden", "x\x00y"])
def test_session_dir_rejects_bad_names(photo_root, bad):
    with pytest.raises(UnsafePathError):
        safety.session_dir(photo_root, bad)


def test_session_dir_rejects_symlink_escape(photo_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (photo_root / "link").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        safety.session_dir(photo_root, "link")


@pytest.mark.parametrize("bad", ["../DSC00001.ARW", "../raw/DSC00001.ARW", "a/b", "..", ""])
def test_target_rejects_traversal(photo_root, bad):
    sess = photo_root / SESSION1
    safety.ensure_select_dir(sess)
    with pytest.raises(UnsafePathError):
        safety.target_path(sess, bad)


def test_select_symlink_refused(photo_root):
    sess = photo_root / SESSION1
    (sess / "select").symlink_to(sess / "raw")
    with pytest.raises(UnsafePathError):
        safety.ensure_select_dir(sess)
    with pytest.raises(UnsafePathError):
        safety.target_path(sess, "DSC00001.ARW")


def test_target_symlink_refused(photo_root):
    sess = photo_root / SESSION1
    d = safety.ensure_select_dir(sess)
    (d / "DSC00001.ARW").symlink_to(sess / "raw" / "DSC00001.ARW")
    with pytest.raises(UnsafePathError):
        safety.target_path(sess, "DSC00001.ARW")


def test_finalize_never_overwrites(photo_root):
    sess = photo_root / SESSION1
    d = safety.ensure_select_dir(sess)
    (d / "A.ARW").write_bytes(b"original")
    f, part = safety.open_part(sess, "A.ARW")
    with f:
        f.write(b"new")
    with pytest.raises(FileExistsError):
        safety.finalize_part(sess, "A.ARW", 0, 0)
    assert (d / "A.ARW").read_bytes() == b"original"
    safety.remove_part(sess, "A.ARW")
    assert not part.exists()


def test_finalize_sets_mtime(photo_root):
    sess = photo_root / SESSION1
    safety.ensure_select_dir(sess)
    f, _ = safety.open_part(sess, "B.ARW")
    with f:
        f.write(b"data")
    final = safety.finalize_part(sess, "B.ARW", 1_700_000_000 * 10**9, 1_600_000_000 * 10**9)
    assert final.read_bytes() == b"data"
    assert os.stat(final).st_mtime_ns == 1_600_000_000 * 10**9


def test_no_other_writes_into_photo_dirs():
    """除 safety.py 外，代码里不应出现对文件的写入/删除/改名调用。"""
    import re
    from pathlib import Path

    pkg = Path(safety.__file__).parent
    pattern = re.compile(
        r"""open\([^)]*["'](w|a|x)b?["']|\.unlink\(|os\.remove\(|os\.rename\(|os\.replace\(|"""
        r"""shutil\.(move|rmtree|copy)|\.write_bytes\(|\.write_text\(|\.rmdir\("""
    )
    # 以下文件只写 DATA_DIR：previews.py 写预览缓存；gallery_store.py、publisher.py 写相册包；
    # client_app.py 的 zf.open(..., "w") 是往内存里的 zip 写，不是文件
    allowed = {"safety.py", "previews.py", "gallery_store.py", "publisher.py", "client_app.py"}
    offenders = []
    for py in pkg.glob("*.py"):
        if py.name in allowed:
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert offenders == []


def test_client_side_never_touches_photo_dir():
    """客户端（client_app.py）和它用的 gallery_store.py 不能接触照片目录：
    不引用照片根目录配置，也不导入扫描、导出、写入照片目录的模块。"""
    import re
    from pathlib import Path

    pkg = Path(safety.__file__).parent
    banned = re.compile(r"photo_root|\bscanner\b|\bsafety\b|\bexporter\b|\bpublisher\b|\bpreviews\b|\borphans\b|from \.app\b")
    offenders = []
    for name in ("client_app.py", "gallery_store.py"):
        for i, line in enumerate((pkg / name).read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if banned.search(code):
                offenders.append(f"{name}:{i}: {line.strip()}")
    assert offenders == []


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", ".hidden", "jpg", "RAW", " x", "x ", "a:b", "a?b",
                                 "x" * 101])
def test_dest_name_rejected(photo_root, bad):
    with pytest.raises(safety.UnsafePathError):
        safety.check_dest_name(bad)


def test_export_to_client_dir(photo_root):
    sess = photo_root / SESSION1
    dest = "client_20261117_abc234"
    d = safety.ensure_select_dir(sess, dest)
    assert d == sess / dest and d.is_dir()
    f, _ = safety.open_part(sess, "C.ARW", dest)
    with f:
        f.write(b"x")
    final = safety.finalize_part(sess, "C.ARW", 0, 0, dest)
    assert final == sess / dest / "C.ARW"
    # 导出目录是指向别处的符号链接时拒绝
    other = photo_root.parent / "elsewhere"
    other.mkdir()
    (sess / "client_link").symlink_to(other)
    with pytest.raises(safety.UnsafePathError):
        safety.ensure_select_dir(sess, "client_link")
