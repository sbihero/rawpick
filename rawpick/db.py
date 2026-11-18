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

-- 首页用的目录统计缓存：目录修改时间没变就直接用上次的结果，避免每次都读几百个目录
CREATE TABLE IF NOT EXISTS dir_cache (
    dir_name        TEXT PRIMARY KEY,
    dir_mtime_ns    INTEGER NOT NULL,
    valid           INTEGER NOT NULL,     -- 是否同时有 jpg/ 和 raw/
    jpg_mtime_ns    INTEGER,
    jpg_count       INTEGER NOT NULL DEFAULT 0,
    select_mtime_ns INTEGER,
    select_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                   -- JSON
);

CREATE TABLE IF NOT EXISTS export_jobs (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    dest        TEXT NOT NULL DEFAULT 'select', -- 导出到出门目录下的哪个文件夹
    gallery_id  INTEGER,                  -- 导出客户所选时是哪个相册
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

-- 客户相册：从一次出门的已选照片发布出来的快照。文件在 DATA_DIR/galleries/<id>/
CREATE TABLE IF NOT EXISTS galleries (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,   -- 6 位短编号，用在导出文件夹名里
    token         TEXT NOT NULL UNIQUE,   -- 客户链接里的随机串
    session_id    INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    dir_name      TEXT NOT NULL,          -- 发布时的出门文件夹名（显示用）
    title         TEXT NOT NULL,
    client_name   TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    lang          TEXT NOT NULL DEFAULT 'auto',   -- auto / zh / en
    limit_count   INTEGER,                -- NULL 表示不限
    limit_mode    TEXT NOT NULL DEFAULT 'block',  -- block：超出不能提交；warn：提醒后可提交
    watermark     INTEGER NOT NULL DEFAULT 1,
    watermark_text TEXT NOT NULL DEFAULT '',
    password_hash TEXT,                   -- NULL 表示公开
    deadline_at   REAL,                   -- 过了之后不能再改选择
    delete_at     REAL,                   -- 到时自动删除相册文件
    status        TEXT NOT NULL DEFAULT 'building', -- building / open / submitted
    build_error   TEXT,
    created_at    REAL NOT NULL,
    published_at  REAL,
    submitted_at  REAL,
    export_dir    TEXT,
    last_export_at REAL,
    delivery_dir  TEXT,                   -- 成片来自出门目录下的哪个文件夹
    delivery_status TEXT,                 -- NULL / building / ready / failed
    delivery_error TEXT,
    delivered_at  REAL,
    deleted_at    REAL                    -- 相册文件已删除（记录保留）
);

CREATE TABLE IF NOT EXISTS gallery_photos (
    id          INTEGER PRIMARY KEY,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    stem        TEXT NOT NULL,
    jpg_name    TEXT NOT NULL,
    raw_name    TEXT,
    width       INTEGER,
    height      INTEGER,
    ready       INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    selected    INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  REAL,
    UNIQUE (gallery_id, stem)
);
CREATE INDEX IF NOT EXISTS gallery_photos_g ON gallery_photos (gallery_id, idx);

CREATE TABLE IF NOT EXISTS gallery_deliveries (
    id          INTEGER PRIMARY KEY,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    width       INTEGER,
    height      INTEGER,
    ready       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS gallery_deliveries_g ON gallery_deliveries (gallery_id, idx);
"""

# 旧版本数据库升级：给已有的表补上新加的列
MIGRATIONS = [
    ("export_jobs", "dest", "TEXT NOT NULL DEFAULT 'select'"),
    ("export_jobs", "gallery_id", "INTEGER"),
]


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
            for table, col, decl in MIGRATIONS:
                cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

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
