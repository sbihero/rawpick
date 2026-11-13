"""SQLite 存储。单连接 + 全局锁，访问量很小，这样最简单可靠。"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY,
    dir_name        TEXT NOT NULL UNIQUE,
    last_scan_at    REAL,
    last_export_at  REAL,
    cursor_photo_id INTEGER
);

CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    stem          TEXT NOT NULL,          -- 配对键：小写的文件名主体
    jpg_name      TEXT NOT NULL,
    raw_name      TEXT,                   -- 没有对应 RAW 时为 NULL
    jpg_mtime_ns  INTEGER NOT NULL,
    jpg_size      INTEGER NOT NULL,
    picked        INTEGER NOT NULL DEFAULT 0,
    exif          TEXT,                   -- JSON，生成预览时填写
    preview_ver   TEXT,                   -- 生成预览时的文件版本
    preview_error TEXT,
    UNIQUE (session_id, stem)
);
CREATE INDEX IF NOT EXISTS photos_session ON photos (session_id, jpg_name);

CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,             -- raw_only / conflict / subdir
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_jobs (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,            -- running / done / failed
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    bytes_done  INTEGER NOT NULL DEFAULT 0,
    report      TEXT,                     -- JSON
    error       TEXT,
    started_at  REAL,
    finished_at REAL
);
"""


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
