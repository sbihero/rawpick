from __future__ import annotations

import os

from conftest import SESSION1, SESSION2

from rawpick.scanner import (
    discover_sessions, ensure_session_row, scan_session_dir, sync_session,
)


def test_discover_only_dirs_with_jpg_and_raw(photo_root):
    (photo_root / ".hidden" / "jpg").mkdir(parents=True)
    (photo_root / ".hidden" / "raw").mkdir()
    (photo_root / "a-file.txt").write_text("x")
    # 大小写不同的 JPG/RAW 子目录也算
    (photo_root / "2025-01-01 大写" / "JPG").mkdir(parents=True)
    (photo_root / "2025-01-01 大写" / "RAW").mkdir()
    assert discover_sessions(photo_root) == [SESSION1, SESSION2, "2025-01-01 大写"]


def test_pairing_and_issues(photo_root, cfg):
    r = scan_session_dir(photo_root / SESSION1, cfg.raw_exts)
    by_stem = {p.stem: p for p in r.photos}
    # 12 张里 DSC00007 冲突被排除
    assert len(r.photos) == 11
    assert "dsc00007" not in by_stem
    # 大小写混用照样配对
    assert by_stem["dsc00009"].jpg.name == "dsc00009.jpg"
    assert by_stem["dsc00009"].raw_name == "dsc00009.ARW"
    # 缺 RAW 的照片照常参与筛选
    assert by_stem["dsc00005"].raw_name is None
    kinds = {}
    for kind, name in r.issues:
        kinds.setdefault(kind, []).append(name)
    assert kinds["raw_only"] == ["DSC09999.ARW"]
    assert kinds["subdir"] == ["jpg/extra"]
    assert len(kinds["conflict"]) == 1 and "DSC00007.jpeg" in kinds["conflict"][0]
    # .xmp 被忽略
    assert not any("xmp" in n.lower() for _, n in r.issues)
    # 按文件名排序
    names = [p.jpg.name.lower() for p in r.photos]
    assert names == sorted(names)


def test_sync_keeps_picks_and_handles_changes(photo_root, cfg, db):
    sid = ensure_session_row(db, SESSION1)
    s = sync_session(cfg, db, sid)
    assert s == {"photos": 11, "added": 11, "removed": 0, "issues": 3}

    db.execute("UPDATE photos SET picked = 1 WHERE stem IN ('dsc00001', 'dsc00002')")
    first = db.query_one("SELECT id FROM photos WHERE stem = 'dsc00001'")

    # 新增一张、删掉一张、修改一张
    jpg = photo_root / SESSION1 / "jpg"
    (jpg / "DSC00013.JPG").write_bytes((jpg / "DSC00001.JPG").read_bytes())
    (jpg / "DSC00012.JPG").unlink()
    st = (jpg / "DSC00002.JPG").stat()
    os.utime(jpg / "DSC00002.JPG", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))

    s = sync_session(cfg, db, sid)
    assert s["added"] == 1 and s["removed"] == 1 and s["photos"] == 11
    rows = {r["stem"]: r for r in db.query("SELECT * FROM photos WHERE session_id = ?", (sid,))}
    assert rows["dsc00001"]["picked"] == 1 and rows["dsc00001"]["id"] == first["id"]
    assert rows["dsc00002"]["picked"] == 1  # 文件变了，标记仍保留
    assert "dsc00012" not in rows
    assert rows["dsc00013"]["raw_name"] is None


def test_sessions_are_independent(photo_root, cfg, db):
    a = ensure_session_row(db, SESSION1)
    b = ensure_session_row(db, SESSION2)
    sync_session(cfg, db, a)
    sync_session(cfg, db, b)
    assert db.query_one("SELECT COUNT(*) n FROM photos WHERE session_id = ?", (b,))["n"] == 12
    assert db.query_one("SELECT COUNT(*) n FROM issues WHERE session_id = ?", (b,))["n"] == 0


def test_survey_counts_and_cache(photo_root, cfg, db, monkeypatch):
    from rawpick import scanner
    from rawpick.scanner import survey_sessions

    (photo_root / "not-a-session").mkdir()
    first = {s["name"]: s for s in survey_sessions(photo_root, db)}
    assert set(first) == {SESSION1, SESSION2}
    assert first[SESSION1]["jpg_count"] == 13 and first[SESSION1]["select_count"] == 0

    # 目录没变：不重新数
    calls = []
    real = scanner.count_jpgs
    monkeypatch.setattr(scanner, "count_jpgs", lambda p: calls.append(p) or real(p))
    survey_sessions(photo_root, db)
    assert calls == []

    # 加一张 JPG、建 select/ 放一个文件：只重新数变了的那个
    sess = photo_root / SESSION1
    (sess / "jpg" / "NEW.JPG").write_bytes(b"x")
    (sess / "select").mkdir()
    (sess / "select" / "DSC00001.ARW").write_bytes(b"x")
    (sess / "select" / ".DSC00002.ARW.part").write_bytes(b"x")  # 临时文件不算
    again = {s["name"]: s for s in survey_sessions(photo_root, db)}
    assert again[SESSION1]["jpg_count"] == 14 and again[SESSION1]["select_count"] == 1
    assert [p.name for p in calls] == [SESSION1]

    # 删除的文件夹从缓存里清掉
    import shutil
    shutil.rmtree(photo_root / SESSION2)
    assert [s["name"] for s in survey_sessions(photo_root, db)] == [SESSION1]
    assert {r["dir_name"] for r in db.query("SELECT dir_name FROM dir_cache")} == {SESSION1, "not-a-session", "不是出门目录"}
