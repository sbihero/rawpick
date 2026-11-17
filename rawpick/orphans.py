"""文件夹改名或移动后，把旧记录（标记、进度、导出记录、预览）接到新文件夹上。

* 孤立记录：数据库里有照片记录，但磁盘上已经找不到这个名字的出门目录。
* 空白文件夹：磁盘上有，但数据库里没有任何照片记录（从没打开过）。

自动模式：孤立记录和空白文件夹的照片（按扫描时的配对规则，比较文件名主体，不分大小写）
完全相同，张数自然也相同，并且双方都只匹配到对方一个时，自动关联。
手动模式：用户在设置页为孤立记录指定一个空白文件夹。

只修改数据库（把会话改个名字），不碰磁盘上的任何文件。照片记录按会话 id 关联，
改名后标记、进度、导出记录、预览缓存全部原样保留；下次打开时按文件名重新对一遍，
新增的照片当作未选，不存在的照片的记录删除。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import Config
from .db import Database
from .safety import UnsafePathError, session_dir
from .scanner import scan_session_dir


class AdoptError(Exception):
    pass


def _sessions_with_data(db: Database) -> list[dict]:
    return db.query(
        "SELECT s.id, s.dir_name, s.last_export_at, COUNT(p.id) AS photos,"
        " COALESCE(SUM(p.picked), 0) AS picked"
        " FROM sessions s JOIN photos p ON p.session_id = s.id GROUP BY s.id"
        " ORDER BY s.dir_name COLLATE NOCASE DESC"
    )


def find_orphans(db: Database, folder_names: list[str]) -> list[dict]:
    existing = set(folder_names)
    return [r for r in _sessions_with_data(db) if r["dir_name"] not in existing]


def blank_folders(db: Database, folder_names: list[str]) -> list[str]:
    with_data = {r["dir_name"] for r in _sessions_with_data(db)}
    return [n for n in folder_names if n not in with_data]


def folder_stems(cfg: Config, name: str) -> set[str]:
    """文件夹里参与筛选的照片（按扫描时同样的配对规则），用小写的文件名主体表示。"""
    try:
        result = scan_session_dir(session_dir(cfg.photo_root, name), cfg.raw_exts)
    except (UnsafePathError, OSError):
        return set()
    return {p.stem for p in result.photos}


def record_stems(db: Database, session_id: int) -> set[str]:
    return {r["stem"] for r in db.query("SELECT stem FROM photos WHERE session_id = ?", (session_id,))}


def adopt(db: Database, orphan_id: int, target: str) -> None:
    """把孤立记录改名为 target。target 必须是没有任何照片记录的文件夹。"""
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM sessions WHERE dir_name = ?", (target,)).fetchone()
        if row is not None:
            if row[0] == orphan_id:
                return
            if conn.execute("SELECT 1 FROM photos WHERE session_id = ? LIMIT 1", (row[0],)).fetchone():
                raise AdoptError("目标文件夹已经有标记，不能关联")
            conn.execute("DELETE FROM sessions WHERE id = ?", (row[0],))
        conn.execute("UPDATE sessions SET dir_name = ? WHERE id = ?", (target, orphan_id))


def auto_adopt(cfg: Config, db: Database, folder_names: list[str]) -> tuple[list[dict], int]:
    """自动关联。返回 (关联结果 [{from, to}], 还剩多少孤立记录)。"""
    orphans = find_orphans(db, folder_names)
    if not orphans:
        return [], 0  # 最常见的情况：什么都不用读
    blanks = blank_folders(db, folder_names)
    if not blanks:
        return [], len(orphans)
    blank_sets = {b: folder_stems(cfg, b) for b in blanks}
    matches = {}
    for o in orphans:
        names = record_stems(db, o["id"])
        matches[o["id"]] = [b for b, s in blank_sets.items() if s and s == names]
    claimed = Counter(b for bs in matches.values() for b in bs)
    adopted = []
    for o in orphans:
        bs = matches[o["id"]]
        if len(bs) == 1 and claimed[bs[0]] == 1:
            adopt(db, o["id"], bs[0])
            adopted.append({"from": o["dir_name"], "to": bs[0]})
    return adopted, len(orphans) - len(adopted)


def manual_candidates(cfg: Config, db: Database, folder_names: list[str]) -> list[dict]:
    """设置页用：每条孤立记录，以及每个空白文件夹与它的文件名重合情况。"""
    orphans = find_orphans(db, folder_names)
    if not orphans:
        return []
    blank_sets = {b: folder_stems(cfg, b) for b in blank_folders(db, folder_names)}
    out = []
    for o in orphans:
        names = record_stems(db, o["id"])
        cands = [{"name": b, "overlap": len(names & s), "count": len(s)} for b, s in blank_sets.items()]
        cands.sort(key=lambda c: (-c["overlap"], c["name"].lower()))
        out.append({
            "id": o["id"], "name": o["dir_name"], "photos": o["photos"], "picked": o["picked"],
            "last_export_at": o["last_export_at"], "candidates": cands,
        })
    return out
