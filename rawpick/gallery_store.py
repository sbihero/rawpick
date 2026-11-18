"""客户相册的数据和文件（相册包）。管理员端和客户端共用。

这个模块和客户端（client_app.py）都不接触照片目录：只读写数据库和 DATA_DIR/galleries/。
有测试检查这两个文件里不出现照片目录相关的名字。

相册包目录结构：
    DATA_DIR/galleries/<相册 id>/
        p/<照片 id>-thumb.jpg     网格缩略图（可能带水印）
        p/<照片 id>-preview.jpg   大图（可能带水印）
        d/<成片 id>.jpg           交付的成片原图
        d/<成片 id>-thumb.jpg     成片缩略图
        d/<成片 id>-preview.jpg   成片大图
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .db import Database

PHOTO_KINDS = ("thumb", "preview")
DELIVERY_KINDS = ("thumb", "preview", "file")
LANGS = ("auto", "zh", "en")
LIMIT_MODES = ("block", "warn")
NOTE_MAX = 1000
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 去掉容易看错的 i l o 0 1
SECRET_KEY = "_cookie_secret"                       # settings 表里的内部项，不通过接口返回
DEFAULT_WATERMARK = "严记肘子铺"


# ---------------------------------------------------------------- 路径

def bundle_dir(galleries_dir: Path, gid: int) -> Path:
    return galleries_dir / str(int(gid))


def photo_file(galleries_dir: Path, gid: int, pid: int, kind: str) -> Path:
    assert kind in PHOTO_KINDS
    return bundle_dir(galleries_dir, gid) / "p" / f"{int(pid)}-{kind}.jpg"


def delivery_file(galleries_dir: Path, gid: int, did: int, kind: str) -> Path:
    assert kind in DELIVERY_KINDS
    name = f"{int(did)}.jpg" if kind == "file" else f"{int(did)}-{kind}.jpg"
    return bundle_dir(galleries_dir, gid) / "d" / name


def remove_bundle(galleries_dir: Path, gid: int) -> None:
    """删除一个相册的全部文件（只会删 DATA_DIR/galleries/<id>）。"""
    d = bundle_dir(galleries_dir, gid)
    if d.is_dir() and not d.is_symlink() and d.parent == galleries_dir:
        shutil.rmtree(d, ignore_errors=True)


def remove_delivery_files(galleries_dir: Path, gid: int) -> None:
    d = bundle_dir(galleries_dir, gid) / "d"
    if d.is_dir() and not d.is_symlink() and d.parent.parent == galleries_dir:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 编号、密码、Cookie

def new_token() -> str:
    """客户链接里的随机串：128 位。"""
    return secrets.token_urlsafe(16)


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def cookie_secret(db: Database) -> bytes:
    """签 Cookie 用的密钥，第一次用时生成并存进 settings 表。"""
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (SECRET_KEY,))
    if row:
        return bytes.fromhex(row["value"].strip('"'))
    value = secrets.token_hex(32)
    db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (SECRET_KEY, f'"{value}"'))
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (SECRET_KEY,))
    return bytes.fromhex(row["value"].strip('"'))


def cookie_name(g: dict) -> str:
    return f"rpg_{g['code']}"


def cookie_value(secret: bytes, g: dict) -> str:
    """改密码后旧 Cookie 自动失效（密码哈希参与签名）。"""
    msg = f"{g['id']}:{g['token']}:{g['password_hash'] or ''}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- 查询

def get(db: Database, gid: int) -> dict | None:
    return db.query_one("SELECT * FROM galleries WHERE id = ?", (gid,))


def get_by_token(db: Database, token: str) -> dict | None:
    if not token or len(token) > 64:
        return None
    return db.query_one("SELECT * FROM galleries WHERE token = ?", (token,))


def lock_reason(g: dict, now: float | None = None) -> str | None:
    """客户不能再修改的原因：submitted（已提交）/ deadline（已截止）/ None。"""
    now = time.time() if now is None else now
    if g["status"] == "submitted":
        return "submitted"
    if g["deadline_at"] and now > g["deadline_at"]:
        return "deadline"
    return None


def counts(db: Database, gid: int) -> dict[str, int]:
    row = db.query_one(
        "SELECT COUNT(*) AS total, COALESCE(SUM(ready), 0) AS ready,"
        " COALESCE(SUM(error IS NOT NULL), 0) AS failed, COALESCE(SUM(selected), 0) AS selected,"
        " COALESCE(SUM(note != ''), 0) AS notes FROM gallery_photos WHERE gallery_id = ?",
        (gid,),
    )
    return {k: int(row[k]) for k in ("total", "ready", "failed", "selected", "notes")}


def client_photos(db: Database, gid: int) -> list[dict]:
    rows = db.query(
        "SELECT id, jpg_name, width, height, selected, note FROM gallery_photos"
        " WHERE gallery_id = ? AND ready = 1 ORDER BY idx",
        (gid,),
    )
    return [{"id": r["id"], "name": r["jpg_name"], "w": r["width"], "h": r["height"],
             "selected": bool(r["selected"]), "note": r["note"]} for r in rows]


def deliveries(db: Database, gid: int) -> list[dict]:
    rows = db.query(
        "SELECT id, name, size, width, height FROM gallery_deliveries"
        " WHERE gallery_id = ? AND ready = 1 ORDER BY idx",
        (gid,),
    )
    return [{"id": r["id"], "name": r["name"], "size": r["size"], "w": r["width"], "h": r["height"]}
            for r in rows]


def delivery_ready(g: dict) -> bool:
    return g["delivery_status"] == "ready" and not g["deleted_at"]


# ---------------------------------------------------------------- 客户操作

class GalleryError(Exception):
    """客户操作不被允许。code 给前端翻译用，message 是中文说明。"""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def ensure_open(g: dict) -> None:
    if g["deleted_at"]:
        raise GalleryError(410, "deleted", "相册已删除")
    if g["status"] == "building":
        raise GalleryError(409, "building", "相册还在准备中")


def ensure_editable(g: dict) -> None:
    ensure_open(g)
    reason = lock_reason(g)
    if reason:
        raise GalleryError(409, "locked", "已提交，不能再修改" if reason == "submitted" else "选片已截止",
                           reason=reason)


def update_photo(db: Database, g: dict, pid: int, selected: bool | None, note: str | None) -> dict:
    ensure_editable(g)
    row = db.query_one("SELECT * FROM gallery_photos WHERE id = ? AND gallery_id = ? AND ready = 1",
                       (pid, g["id"]))
    if row is None:
        raise GalleryError(404, "not_found", "照片不存在")
    sets, params = [], []
    if selected is not None:
        sets.append("selected = ?")
        params.append(int(selected))
    if note is not None:
        if len(note) > NOTE_MAX:
            raise GalleryError(400, "note_too_long", f"备注最多 {NOTE_MAX} 个字")
        sets.append("note = ?")
        params.append(note.strip())
    if sets:
        db.execute(f"UPDATE gallery_photos SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                   (*params, time.time(), pid))
    row = db.query_one("SELECT selected, note FROM gallery_photos WHERE id = ?", (pid,))
    return {"id": pid, "selected": bool(row["selected"]), "note": row["note"],
            "selected_count": counts(db, g["id"])["selected"]}


def submit(db: Database, g: dict, confirm_over: bool) -> dict:
    ensure_editable(g)
    n = counts(db, g["id"])["selected"]
    if n == 0:
        raise GalleryError(400, "nothing_selected", "还没有选照片")
    limit = g["limit_count"]
    if limit and n > limit:
        if g["limit_mode"] == "block":
            raise GalleryError(400, "over_limit", f"最多可以选 {limit} 张，现在选了 {n} 张",
                               limit=limit, selected=n)
        if not confirm_over:
            raise GalleryError(409, "over_limit_confirm", f"超出可选张数 {n - limit} 张，需要确认",
                               limit=limit, selected=n)
    now = time.time()
    db.execute("UPDATE galleries SET status = 'submitted', submitted_at = ? WHERE id = ?", (now, g["id"]))
    return {"ok": True, "selected": n, "submitted_at": now}


# ---------------------------------------------------------------- 密码尝试次数限制

class LoginLimiter:
    """同一个相册、同一个来源 IP，15 分钟内最多输错 10 次。"""

    def __init__(self, max_failures: int = 10, window: float = 900):
        self.max_failures = max_failures
        self.window = window
        self._fails: dict[tuple[int, str], list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple[int, str], now: float) -> list[float]:
        items = [t for t in self._fails.get(key, []) if now - t < self.window]
        if items:
            self._fails[key] = items
        else:
            self._fails.pop(key, None)
        return items

    def blocked(self, gid: int, ip: str) -> bool:
        with self._lock:
            return len(self._recent((gid, ip), time.time())) >= self.max_failures

    def fail(self, gid: int, ip: str) -> None:
        with self._lock:
            now = time.time()
            self._recent((gid, ip), now)
            self._fails.setdefault((gid, ip), []).append(now)

    def reset(self, gid: int, ip: str) -> None:
        with self._lock:
            self._fails.pop((gid, ip), None)
