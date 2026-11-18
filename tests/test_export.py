from __future__ import annotations

import os

import pytest
from conftest import SESSION1, snapshot

from rawpick.exporter import ExportBusy, ExportManager, build_plan
from rawpick.scanner import ensure_session_row, sync_session


@pytest.fixture
def setup(cfg, db, photo_root):
    sid = ensure_session_row(db, SESSION1)
    sync_session(cfg, db, sid)
    return sid, photo_root / SESSION1


def pick(db, *stems):
    for s in stems:
        db.execute("UPDATE photos SET picked = 1 WHERE stem = ?", (s,))


def unpick(db, *stems):
    for s in stems:
        db.execute("UPDATE photos SET picked = 0 WHERE stem = ?", (s,))


def test_plan_categories(setup, cfg, db):
    sid, sess = setup
    pick(db, "dsc00001", "dsc00002", "dsc00003", "dsc00005", "dsc00009")
    sel = sess / "select"
    sel.mkdir()
    (sel / "DSC00002.ARW").write_bytes((sess / "raw" / "DSC00002.ARW").read_bytes())  # 同大小
    (sel / "DSC00003.ARW").write_bytes(b"different")  # 大小不同
    (sel / "DSC00011.ARW").write_bytes(b"old pick")  # 现在未选
    (sel / ".DSC00001.ARW.part").write_bytes(b"leftover")  # 临时文件不算
    plan = build_plan(cfg, db, sid)
    assert plan.picked == 5
    assert plan.copy == ["DSC00001.ARW", "dsc00009.ARW"]
    assert plan.skip == ["DSC00002.ARW"]
    assert plan.conflict == ["DSC00003.ARW"]
    assert plan.missing_raw == ["DSC00005.JPG"]
    assert plan.stale == ["DSC00011.ARW"]
    raw = sess / "raw"
    assert plan.copy_bytes == (raw / "DSC00001.ARW").stat().st_size + (raw / "dsc00009.ARW").stat().st_size


def test_export_copies_and_is_idempotent(setup, cfg, db, photo_root):
    sid, sess = setup
    before = snapshot(photo_root)
    pick(db, "dsc00001", "dsc00002", "dsc00005")
    mgr = ExportManager(cfg, db)
    job = mgr.run_sync(sid)
    assert job["status"] == "done"
    assert job["report"]["copied"] == ["DSC00001.ARW", "DSC00002.ARW"]
    assert job["report"]["missing_raw"] == ["DSC00005.JPG"]
    assert job["done"] == job["total"] == 2
    assert job["bytes_done"] == job["bytes_total"]
    for name in ("DSC00001.ARW", "DSC00002.ARW"):
        src, dst = sess / "raw" / name, sess / "select" / name
        assert dst.read_bytes() == src.read_bytes()
        assert dst.stat().st_mtime_ns == src.stat().st_mtime_ns
    assert not list((sess / "select").glob(".*"))  # 没有残留临时文件
    assert db.query_one("SELECT last_export_at FROM sessions WHERE id = ?", (sid,))["last_export_at"]

    # 源文件完全没动
    assert snapshot(photo_root) == before

    # 再导出一次：全部跳过
    job2 = mgr.run_sync(sid)
    assert job2["report"]["copied"] == []
    assert job2["report"]["skipped"] == ["DSC00001.ARW", "DSC00002.ARW"]

    # 取消一张再导出：只增不删，列为 stale
    unpick(db, "dsc00002")
    pick(db, "dsc00004")
    job3 = mgr.run_sync(sid)
    assert job3["report"]["copied"] == ["DSC00004.ARW"]
    assert job3["report"]["stale"] == ["DSC00002.ARW"]
    assert (sess / "select" / "DSC00002.ARW").exists()


def test_conflict_not_overwritten(setup, cfg, db):
    sid, sess = setup
    pick(db, "dsc00001")
    (sess / "select").mkdir()
    (sess / "select" / "DSC00001.ARW").write_bytes(b"mine, do not touch")
    job = ExportManager(cfg, db).run_sync(sid)
    assert job["report"]["conflicts"] == ["DSC00001.ARW"]
    assert (sess / "select" / "DSC00001.ARW").read_bytes() == b"mine, do not touch"


def test_select_created_only_when_needed(setup, cfg, db):
    sid, sess = setup
    pick(db, "dsc00005")  # 只有缺 RAW 的
    ExportManager(cfg, db).run_sync(sid)
    assert not (sess / "select").exists()


def test_copy_error_recorded_and_part_cleaned(setup, cfg, db, monkeypatch):
    sid, sess = setup
    pick(db, "dsc00001", "dsc00002")
    mgr = ExportManager(cfg, db)
    real = mgr._copy_one

    def flaky(src, s, name, progress, dest="select"):
        if name == "DSC00001.ARW":
            raise OSError("模拟磁盘错误")
        return real(src, s, name, progress, dest)

    monkeypatch.setattr(mgr, "_copy_one", flaky)
    job = mgr.run_sync(sid)
    assert job["status"] == "done"
    assert [e["name"] for e in job["report"]["errors"]] == ["DSC00001.ARW"]
    assert job["report"]["copied"] == ["DSC00002.ARW"]
    assert sorted(os.listdir(sess / "select")) == ["DSC00002.ARW"]


def test_one_job_per_session(setup, cfg, db, monkeypatch):
    sid, _ = setup
    pick(db, "dsc00001")
    mgr = ExportManager(cfg, db)
    import threading

    gate = threading.Event()
    real = mgr._copy_one
    monkeypatch.setattr(mgr, "_copy_one", lambda *a: (gate.wait(5), real(*a))[1])
    mgr.start(sid)
    with pytest.raises(ExportBusy):
        mgr.start(sid)
    gate.set()
    import time

    deadline = time.time() + 5
    while mgr.running_job(sid) is not None and time.time() < deadline:
        time.sleep(0.01)
    assert mgr.running_job(sid) is None


def test_interrupted_jobs_marked_failed(setup, cfg, db):
    sid, _ = setup
    db.execute("INSERT INTO export_jobs (session_id, status) VALUES (?, 'running')", (sid,))
    ExportManager(cfg, db).mark_interrupted_jobs()
    assert db.query_one("SELECT status FROM export_jobs")["status"] == "failed"
