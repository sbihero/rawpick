"""导出：把已选照片的 RAW 拷到 <出门目录>/select/（客户相册导出到 client_.../）。只增不删，不覆盖。

所有对照片目录的写入都经过 safety 模块。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import safety
from .config import Config
from .db import Database
from .scanner import find_subdir

log = logging.getLogger("rawpick.export")

CHUNK = 4 * 1024 * 1024


class ExportBusy(Exception):
    """这个会话已有导出任务在运行。"""


@dataclass
class Plan:
    picked: int = 0
    copy: list[str] = field(default_factory=list)          # 要拷贝的 RAW 文件名
    skip: list[str] = field(default_factory=list)          # 导出目录里已有同名同大小
    conflict: list[str] = field(default_factory=list)      # 导出目录里有同名但大小不同
    missing_raw: list[str] = field(default_factory=list)   # 已选但没有 RAW（JPG 文件名）
    stale: list[str] = field(default_factory=list)         # 导出目录里有、但当前未选
    copy_bytes: int = 0
    dest: str = safety.SELECT_DIR_NAME

    def to_dict(self) -> dict:
        return asdict(self)


def _session_paths(cfg: Config, dir_name: str) -> tuple[Path, Path]:
    sess = safety.session_dir(cfg.photo_root, dir_name)
    raw_dir = find_subdir(sess, "raw")
    if raw_dir is None:
        raise FileNotFoundError(f"{dir_name} 缺少 raw/")
    return sess, raw_dir


def _existing_in_select(sess: Path, dest: str = safety.SELECT_DIR_NAME) -> dict[str, tuple[str, int]]:
    """导出目录里的正式文件：小写名 -> (原名, 大小)。不含 . 开头的临时文件。"""
    d = safety.select_dir(sess, dest)
    out: dict[str, tuple[str, int]] = {}
    if not d.is_dir() or d.is_symlink():
        return out
    with os.scandir(d) as it:
        for e in it:
            if e.name.startswith(".") or not e.is_file(follow_symlinks=False):
                continue
            out[e.name.lower()] = (e.name, e.stat(follow_symlinks=False).st_size)
    return out


def build_plan(cfg: Config, db: Database, session_id: int,
               dest: str = safety.SELECT_DIR_NAME, items: list[dict] | None = None) -> Plan:
    """items：要导出的照片（含 jpg_name、raw_name）。不传时用这次出门的已选照片。"""
    safety.check_dest_name(dest)
    sess_row = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if sess_row is None:
        raise KeyError(session_id)
    sess, raw_dir = _session_paths(cfg, sess_row["dir_name"])
    existing = _existing_in_select(sess, dest)
    if items is None:
        picked = db.query(
            "SELECT jpg_name, raw_name FROM photos WHERE session_id = ? AND picked = 1"
            " ORDER BY jpg_name COLLATE NOCASE",
            (session_id,),
        )
    else:
        picked = items
    plan = Plan(picked=len(picked), dest=dest)
    wanted = set()
    for p in picked:
        raw = p["raw_name"]
        if not raw:
            plan.missing_raw.append(p["jpg_name"])
            continue
        try:
            size = (raw_dir / raw).stat().st_size
        except FileNotFoundError:
            plan.missing_raw.append(p["jpg_name"])
            continue
        wanted.add(raw.lower())
        ex = existing.get(raw.lower())
        if ex is None:
            plan.copy.append(raw)
            plan.copy_bytes += size
        elif ex[1] == size:
            plan.skip.append(raw)
        else:
            plan.conflict.append(raw)
    plan.stale = sorted((name for key, (name, _) in existing.items() if key not in wanted),
                        key=str.lower)
    return plan


class ExportManager:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self._lock = threading.Lock()
        self._running: dict[int, int] = {}  # session_id -> job_id

    def mark_interrupted_jobs(self) -> None:
        """上次进程退出时还在跑的任务标记为失败。"""
        self.db.execute(
            "UPDATE export_jobs SET status = 'failed', error = '服务重启，任务中断', finished_at = ?"
            " WHERE status = 'running'",
            (time.time(),),
        )

    def running_job(self, session_id: int) -> int | None:
        with self._lock:
            return self._running.get(session_id)

    def get_job(self, job_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM export_jobs WHERE id = ?", (job_id,))
        if row and row["report"]:
            row["report"] = json.loads(row["report"])
        return row

    def start(self, session_id: int, dest: str = safety.SELECT_DIR_NAME,
              items: list[dict] | None = None, gallery_id: int | None = None) -> int:
        """开始导出。同一次出门同时只允许一个导出任务（不管导出到哪个目录）。"""
        with self._lock:
            if session_id in self._running:
                raise ExportBusy(self._running[session_id])
            plan = build_plan(self.cfg, self.db, session_id, dest, items)
            cur = self.db.execute(
                "INSERT INTO export_jobs (session_id, dest, gallery_id, status, total, bytes_total, started_at)"
                " VALUES (?, ?, ?, 'running', ?, ?, ?)",
                (session_id, dest, gallery_id, len(plan.copy), plan.copy_bytes, time.time()),
            )
            job_id = cur.lastrowid
            self._running[session_id] = job_id
        t = threading.Thread(target=self._run, args=(job_id, session_id, plan, gallery_id),
                             name=f"export-{job_id}", daemon=True)
        t.start()
        return job_id

    def run_sync(self, session_id: int, **kw) -> dict:
        """测试用：同步执行一次导出并返回任务结果。"""
        job_id = self.start(session_id, **kw)
        while self.running_job(session_id) == job_id:
            time.sleep(0.01)
        job = self.get_job(job_id)
        assert job is not None
        return job

    # ------------------------------------------------------------------
    def _run(self, job_id: int, session_id: int, plan: Plan, gallery_id: int | None = None) -> None:
        report = {
            "copied": [], "skipped": list(plan.skip), "conflicts": list(plan.conflict),
            "missing_raw": list(plan.missing_raw), "stale": list(plan.stale), "errors": [],
        }
        status, error = "done", None
        done = 0
        bytes_done = 0
        last_update = 0.0

        def progress(n: int) -> None:
            nonlocal bytes_done, last_update
            bytes_done += n
            now = time.monotonic()
            if now - last_update > 0.3:
                last_update = now
                self.db.execute("UPDATE export_jobs SET bytes_done = ? WHERE id = ?",
                                (bytes_done, job_id))

        try:
            sess_row = self.db.query_one("SELECT dir_name FROM sessions WHERE id = ?", (session_id,))
            sess, raw_dir = _session_paths(self.cfg, sess_row["dir_name"])
            dest = plan.dest
            if plan.copy:
                safety.ensure_select_dir(sess, dest)
            for name in plan.copy:
                try:
                    outcome = self._copy_one(raw_dir / name, sess, name, progress, dest)
                    report[outcome].append(name)
                except Exception as e:
                    log.warning("拷贝失败 %s: %s", name, e)
                    report["errors"].append({"name": name, "error": f"{type(e).__name__}: {e}"})
                    try:
                        safety.remove_part(sess, name, dest)
                    except Exception:
                        pass
                done += 1
                self.db.execute("UPDATE export_jobs SET done = ?, bytes_done = ? WHERE id = ?",
                                (done, bytes_done, job_id))
        except Exception as e:
            log.exception("导出任务失败")
            status, error = "failed", f"{type(e).__name__}: {e}"
        finally:
            now = time.time()
            self.db.execute(
                "UPDATE export_jobs SET status = ?, error = ?, report = ?, done = ?, bytes_done = ?,"
                " finished_at = ? WHERE id = ?",
                (status, error, json.dumps(report, ensure_ascii=False), done, bytes_done, now, job_id),
            )
            if status == "done" and gallery_id is not None:
                self.db.execute("UPDATE galleries SET last_export_at = ?, export_dir = ? WHERE id = ?",
                                (now, plan.dest, gallery_id))
            elif status == "done" and plan.dest == safety.SELECT_DIR_NAME:
                self.db.execute("UPDATE sessions SET last_export_at = ? WHERE id = ?", (now, session_id))
            with self._lock:
                self._running.pop(session_id, None)

    def _copy_one(self, src: Path, sess: Path, name: str, progress,
                  dest: str = safety.SELECT_DIR_NAME) -> str:
        """拷一个文件。返回 report 里的分类：copied / skipped / conflicts。"""
        st = src.stat()
        final = safety.target_path(sess, name, dest)
        if final.exists():  # 计划之后才出现的同名文件
            return "skipped" if final.stat().st_size == st.st_size else "conflicts"
        out, part = safety.open_part(sess, name, dest)
        with out, open(src, "rb") as inp:
            while True:
                chunk = inp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                progress(len(chunk))
        if part.stat().st_size != st.st_size:
            safety.remove_part(sess, name, dest)
            raise OSError(f"大小校验失败：源 {st.st_size}，写入 {part.stat().st_size}")
        try:
            safety.finalize_part(sess, name, st.st_atime_ns, st.st_mtime_ns, dest)
        except FileExistsError:
            safety.remove_part(sess, name, dest)
            return "conflicts"
        return "copied"
