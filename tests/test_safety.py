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
    allowed = {"safety.py", "previews.py"}  # previews.py 只写 DATA_DIR 下的缓存
    offenders = []
    for py in pkg.glob("*.py"):
        if py.name in allowed:
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert offenders == []
