"""发现出门目录、按文件名主体配对 JPG 与 RAW，并同步到数据库。

只读文件系统，不写照片目录。
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import JPG_EXTS, Config
from .db import Database
from .safety import session_dir


@dataclass(frozen=True)
class FileInfo:
    name: str
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class PairedPhoto:
    stem: str  # 小写的配对键
    jpg: FileInfo
    raw_name: str | None


@dataclass
class ScanResult:
    photos: list[PairedPhoto] = field(default_factory=list)
    issues: list[tuple[str, str]] = field(default_factory=list)  # (kind, name)


def find_subdir(session_path: Path, name: str) -> Path | None:
    """在出门目录里找 jpg/ 或 raw/，名字大小写不敏感。"""
    try:
        with os.scandir(session_path) as it:
            for e in it:
                if e.name.lower() == name and e.is_dir(follow_symlinks=False):
                    return Path(e.path)
    except OSError:
        return None
    return None


def discover_sessions(photo_root: Path) -> list[str]:
    """照片根目录下所有同时含 jpg/ 和 raw/ 的一级子目录，按名字倒序（新的在前）。"""
    names = []
    with os.scandir(photo_root) as it:
        for e in it:
            if e.name.startswith(".") or not e.is_dir(follow_symlinks=False):
                continue
            sess = Path(e.path)
            if find_subdir(sess, "jpg") and find_subdir(sess, "raw"):
                names.append(e.name)
    names.sort(key=str.lower, reverse=True)
    return names


def count_jpgs(session_path: Path) -> int:
    jpg_dir = find_subdir(session_path, "jpg")
    if jpg_dir is None:
        return 0
    with os.scandir(jpg_dir) as it:
        return sum(
            1 for e in it
            if not e.name.startswith(".") and os.path.splitext(e.name)[1].lower() in JPG_EXTS
        )


def _list_dir(directory: Path, exts: tuple[str, ...], label: str,
              issues: list[tuple[str, str]]) -> dict[str, list[FileInfo]]:
    groups: dict[str, list[FileInfo]] = defaultdict(list)
    with os.scandir(directory) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            if e.is_dir(follow_symlinks=False):
                issues.append(("subdir", f"{label}/{e.name}"))
                continue
            if not e.is_file(follow_symlinks=False):
                continue
            stem, ext = os.path.splitext(e.name)
            if ext.lower() not in exts:
                continue  # 其他文件（如 .xmp）直接忽略
            st = e.stat(follow_symlinks=False)
            groups[stem.lower()].append(FileInfo(e.name, st.st_mtime_ns, st.st_size))
    return groups


def scan_session_dir(session_path: Path, raw_exts: tuple[str, ...]) -> ScanResult:
    result = ScanResult()
    jpg_dir = find_subdir(session_path, "jpg")
    raw_dir = find_subdir(session_path, "raw")
    if jpg_dir is None or raw_dir is None:
        raise FileNotFoundError(f"{session_path} 里缺少 jpg/ 或 raw/")

    jpgs = _list_dir(jpg_dir, JPG_EXTS, "jpg", result.issues)
    raws = _list_dir(raw_dir, raw_exts, "raw", result.issues)

    for key in sorted(set(jpgs) | set(raws)):
        j, r = jpgs.get(key, []), raws.get(key, [])
        if len(j) > 1 or len(r) > 1:
            names = sorted([f"jpg/{f.name}" for f in j] + [f"raw/{f.name}" for f in r])
            result.issues.append(("conflict", "、".join(names)))
            continue
        if not j:
            result.issues.append(("raw_only", r[0].name))
            continue
        result.photos.append(PairedPhoto(key, j[0], r[0].name if r else None))

    result.photos.sort(key=lambda p: p.jpg.name.lower())
    result.issues.sort()
    return result


def photo_version(mtime_ns: int, size: int) -> str:
    """文件版本号：修改时间 + 大小。用于预览缓存和 URL。"""
    return f"{mtime_ns:x}-{size:x}"


def ensure_session_row(db: Database, dir_name: str) -> int:
    db.execute("INSERT OR IGNORE INTO sessions (dir_name) VALUES (?)", (dir_name,))
    row = db.query_one("SELECT id FROM sessions WHERE dir_name = ?", (dir_name,))
    assert row is not None
    return row["id"]


def sync_session(cfg: Config, db: Database, session_id: int) -> dict:
    """扫描一个出门目录并合并进数据库。已有照片的标记保留。"""
    sess = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if sess is None:
        raise KeyError(session_id)
    path = session_dir(cfg.photo_root, sess["dir_name"])
    result = scan_session_dir(path, cfg.raw_exts)

    with db.transaction() as conn:
        existing = {
            r["stem"]: dict(r)
            for r in conn.execute(
                "SELECT id, stem, jpg_mtime_ns, jpg_size FROM photos WHERE session_id = ?",
                (session_id,),
            )
        }
        seen = set()
        added = 0
        for p in result.photos:
            seen.add(p.stem)
            old = existing.get(p.stem)
            if old is None:
                conn.execute(
                    "INSERT INTO photos (session_id, stem, jpg_name, raw_name, jpg_mtime_ns, jpg_size)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, p.stem, p.jpg.name, p.raw_name, p.jpg.mtime_ns, p.jpg.size),
                )
                added += 1
            elif old["jpg_mtime_ns"] != p.jpg.mtime_ns or old["jpg_size"] != p.jpg.size:
                # JPG 变了：清掉 EXIF，预览会因版本不符自动重建
                conn.execute(
                    "UPDATE photos SET jpg_name = ?, raw_name = ?, jpg_mtime_ns = ?, jpg_size = ?,"
                    " exif = NULL, preview_error = NULL WHERE id = ?",
                    (p.jpg.name, p.raw_name, p.jpg.mtime_ns, p.jpg.size, old["id"]),
                )
            else:
                conn.execute(
                    "UPDATE photos SET jpg_name = ?, raw_name = ? WHERE id = ?",
                    (p.jpg.name, p.raw_name, old["id"]),
                )
        removed = [old["id"] for stem, old in existing.items() if stem not in seen]
        for pid in removed:
            conn.execute("DELETE FROM photos WHERE id = ?", (pid,))
        conn.execute("DELETE FROM issues WHERE session_id = ?", (session_id,))
        conn.executemany(
            "INSERT INTO issues (session_id, kind, name) VALUES (?, ?, ?)",
            [(session_id, kind, name) for kind, name in result.issues],
        )
        conn.execute("UPDATE sessions SET last_scan_at = ? WHERE id = ?", (time.time(), session_id))

    return {
        "photos": len(result.photos),
        "added": added,
        "removed": len(removed),
        "issues": len(result.issues),
    }
