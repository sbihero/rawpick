from __future__ import annotations

import os
import shutil

import pytest
from conftest import SESSION1, SESSION2

from rawpick.orphans import (
    AdoptError, adopt, auto_adopt, blank_folders, find_orphans, manual_candidates,
)
from rawpick.scanner import ensure_session_row, survey_sessions, sync_session


def names(cfg, db):
    return [i["name"] for i in survey_sessions(cfg.photo_root, db)]


def open_and_pick(cfg, db, name, *stems):
    sid = ensure_session_row(db, name)
    sync_session(cfg, db, sid)
    for s in stems:
        db.execute("UPDATE photos SET picked = 1 WHERE session_id = ? AND stem = ?", (sid, s))
    return sid


def test_no_orphans_no_work(cfg, db, monkeypatch):
    open_and_pick(cfg, db, SESSION1, "dsc00001")
    import rawpick.orphans as o

    monkeypatch.setattr(o, "folder_stems", lambda *a: pytest.fail("不应读取目录"))
    assert auto_adopt(cfg, db, names(cfg, db)) == ([], 0)


def test_auto_adopt_after_rename(cfg, db, photo_root):
    sid = open_and_pick(cfg, db, SESSION1, "dsc00001", "dsc00002")
    db.execute("UPDATE sessions SET cursor_photo_id = 99, last_export_at = 123 WHERE id = ?", (sid,))
    ensure_session_row(db, SESSION2)  # 另一个空白文件夹，照片不同，不应被误配
    os.rename(photo_root / SESSION1, photo_root / "20261108_改名后")
    n = names(cfg, db)
    assert [o["dir_name"] for o in find_orphans(db, n)] == [SESSION1]
    adopted, left = auto_adopt(cfg, db, n)
    assert adopted == [{"from": SESSION1, "to": "20261108_改名后"}] and left == 0
    row = db.query_one("SELECT * FROM sessions WHERE dir_name = '20261108_改名后'")
    assert row["id"] == sid and row["cursor_photo_id"] == 99 and row["last_export_at"] == 123
    picked = db.query("SELECT stem FROM photos WHERE session_id = ? AND picked = 1 ORDER BY stem", (sid,))
    assert [r["stem"] for r in picked] == ["dsc00001", "dsc00002"]
    # 再打开扫描一次，标记还在
    sync_session(cfg, db, sid)
    assert db.query_one("SELECT COUNT(*) n FROM photos WHERE session_id = ? AND picked = 1", (sid,))["n"] == 2


def test_auto_skips_when_files_differ(cfg, db, photo_root):
    open_and_pick(cfg, db, SESSION1, "dsc00001")
    os.rename(photo_root / SESSION1, photo_root / "renamed")
    (photo_root / "renamed" / "jpg" / "DSC00001.JPG").unlink()
    adopted, left = auto_adopt(cfg, db, names(cfg, db))
    assert adopted == [] and left == 1


def test_auto_skips_when_ambiguous(cfg, db, photo_root):
    open_and_pick(cfg, db, SESSION1, "dsc00001")
    shutil.copytree(photo_root / SESSION1, photo_root / "copy-a")
    os.rename(photo_root / SESSION1, photo_root / "copy-b")
    adopted, left = auto_adopt(cfg, db, names(cfg, db))
    assert adopted == [] and left == 1


def test_manual_candidates_and_adopt(cfg, db, photo_root):
    sid = open_and_pick(cfg, db, SESSION1, "dsc00001", "dsc00012")
    os.rename(photo_root / SESSION1, photo_root / "renamed")
    (photo_root / "renamed" / "jpg" / "DSC00012.JPG").unlink()
    n = names(cfg, db)
    cands = manual_candidates(cfg, db, n)
    assert len(cands) == 1 and cands[0]["id"] == sid and cands[0]["photos"] == 11
    by_name = {c["name"]: c for c in cands[0]["candidates"]}
    assert set(by_name) == {"renamed", SESSION2}
    assert by_name["renamed"] == {"name": "renamed", "overlap": 10, "count": 10}
    # 相机编号会重复：另一次出门的文件名也可能大量重合，所以只做提示、由用户决定
    assert by_name[SESSION2]["overlap"] == 11
    adopt(db, sid, "renamed")
    sync_session(cfg, db, sid)
    picked = db.query("SELECT stem FROM photos WHERE session_id = ? AND picked = 1", (sid,))
    assert [r["stem"] for r in picked] == ["dsc00001"]  # 不存在的照片的标记被丢弃
    assert find_orphans(db, names(cfg, db)) == []


def test_cannot_adopt_into_folder_with_data(cfg, db, photo_root):
    sid = open_and_pick(cfg, db, SESSION1, "dsc00001")
    open_and_pick(cfg, db, SESSION2)
    os.rename(photo_root / SESSION1, photo_root / "renamed")
    assert SESSION2 not in blank_folders(db, names(cfg, db))
    with pytest.raises(AdoptError):
        adopt(db, sid, SESSION2)
