"""管理员端的相册操作：发布（生成相册包）、交付成片、导出客户所选 RAW、到期自动删除。

读照片目录（只读），写 DATA_DIR/galleries/。导出 RAW 交给 exporter（经过 safety）。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from . import gallery_store as gs
from .config import JPG_EXTS, Config
from .db import Database
from .exporter import ExportManager, Plan, build_plan
from .previews import atomic_save, load_display_image
from .safety import SELECT_DIR_NAME, check_dest_name, session_dir
from .scanner import find_subdir
from .watermark import apply_watermark, find_font

log = logging.getLogger("rawpick.publisher")

# 交付时不列出的文件夹（大小写不敏感）
DELIVERY_EXCLUDE = {"jpg", "raw", SELECT_DIR_NAME}


class PublishError(Exception):
    pass


def _copy_into_bundle(src: Path, dest: Path) -> None:
    """把成片拷进相册包（先写临时文件再改名）。只写 DATA_DIR。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-", suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            while chunk := inp.read(4 * 1024 * 1024):
                out.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_export_dir(g: dict) -> str:
    ts = g["published_at"] or g["created_at"]
    return f"client_{datetime.fromtimestamp(ts):%Y%m%d}_{g['code']}"


class Publisher:
    def __init__(self, cfg: Config, db: Database, exports: ExportManager):
        self.cfg = cfg
        self.db = db
        self.exports = exports
        self.font = find_font(cfg.watermark_font)
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, int], threading.Thread] = {}
        if self.font is None:
            log.warning("没有找到水印字体，中文水印会显示成方框。可以用 WATERMARK_FONT 指定字体文件")

    def current_dir(self, g: dict) -> str:
        """相册对应的出门文件夹的当前名字（文件夹改名、找回标记后跟着变）。"""
        if g["session_id"] is not None:
            row = self.db.query_one("SELECT dir_name FROM sessions WHERE id = ?", (g["session_id"],))
            if row:
                return row["dir_name"]
        return g["dir_name"]

    # ------------------------------------------------------------ 后台任务
    def _spawn(self, kind: str, gid: int, fn, *args) -> None:
        with self._lock:
            t = self._jobs.get((kind, gid))
            if t and t.is_alive():
                return
            t = threading.Thread(target=fn, args=(gid, *args), name=f"{kind}-{gid}", daemon=True)
            self._jobs[(kind, gid)] = t
        t.start()

    def wait(self, timeout: float = 30) -> None:
        """测试用：等所有后台任务结束。"""
        deadline = time.time() + timeout
        while True:
            with self._lock:
                alive = [t for t in self._jobs.values() if t.is_alive()]
            if not alive or time.time() > deadline:
                return
            alive[0].join(timeout=0.05)

    def resume(self) -> None:
        """服务启动时，把上次没做完的生成任务接着做。"""
        for g in self.db.query("SELECT id FROM galleries WHERE status = 'building' AND deleted_at IS NULL"):
            self._spawn("build", g["id"], self._build)
        for g in self.db.query(
                "SELECT id, delivery_dir FROM galleries WHERE delivery_status = 'building' AND deleted_at IS NULL"):
            self._spawn("delivery", g["id"], self._import_delivery, g["delivery_dir"])

    # ------------------------------------------------------------ 发布
    def publish(self, session_id: int, opts: dict[str, Any], watermark_text: str) -> int:
        sess = self.db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if sess is None:
            raise KeyError(session_id)
        rows = self.db.query(
            "SELECT stem, jpg_name, raw_name FROM photos WHERE session_id = ? AND picked = 1"
            " ORDER BY jpg_name COLLATE NOCASE",
            (session_id,),
        )
        if not rows:
            raise PublishError("这次出门还没有已选的照片。先粗筛，再发布")
        now = time.time()
        with self.db.transaction() as conn:
            while True:
                code, token = gs.new_code(), gs.new_token()
                if not conn.execute("SELECT 1 FROM galleries WHERE code = ? OR token = ?",
                                    (code, token)).fetchone():
                    break
            cur = conn.execute(
                "INSERT INTO galleries (code, token, session_id, dir_name, title, client_name, message, lang,"
                " limit_count, limit_mode, watermark, watermark_text, password_hash, deadline_at, delete_at,"
                " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'building', ?)",
                (code, token, session_id, sess["dir_name"], opts["title"], opts.get("client_name", ""),
                 opts.get("message", ""), opts.get("lang", "auto"), opts.get("limit_count"),
                 opts.get("limit_mode", "block"), int(opts.get("watermark", True)), watermark_text,
                 opts.get("password_hash"), opts.get("deadline_at"), opts.get("delete_at"), now),
            )
            gid = cur.lastrowid
            conn.executemany(
                "INSERT INTO gallery_photos (gallery_id, idx, stem, jpg_name, raw_name) VALUES (?,?,?,?,?)",
                [(gid, i, r["stem"], r["jpg_name"], r["raw_name"]) for i, r in enumerate(rows)],
            )
        self._spawn("build", gid, self._build)
        return gid

    def _render_photo(self, g: dict, jpg_dir: Path, row: dict) -> None:
        gdir = self.cfg.galleries_dir
        try:
            img, _ = load_display_image(jpg_dir / row["jpg_name"], self.cfg.gallery_preview_size)
            thumb = img.copy()
            thumb.thumbnail((self.cfg.gallery_thumb_size, self.cfg.gallery_thumb_size))
            if g["watermark"]:
                img = apply_watermark(img, g["watermark_text"], self.font)
                thumb = apply_watermark(thumb, g["watermark_text"], self.font)
            atomic_save(img, gs.photo_file(gdir, g["id"], row["id"], "preview"), quality=85)
            atomic_save(thumb, gs.photo_file(gdir, g["id"], row["id"], "thumb"), quality=80)
            self.db.execute(
                "UPDATE gallery_photos SET ready = 1, error = NULL, width = ?, height = ? WHERE id = ?",
                (img.width, img.height, row["id"]))
        except Exception as e:
            log.warning("相册 %s 生成 %s 失败：%s", g["id"], row["jpg_name"], e)
            self.db.execute("UPDATE gallery_photos SET error = ? WHERE id = ?",
                            (f"{type(e).__name__}: {e}", row["id"]))

    def _build(self, gid: int) -> None:
        g = gs.get(self.db, gid)
        if g is None or g["deleted_at"]:
            return
        try:
            name = self.current_dir(g)
            sess = session_dir(self.cfg.photo_root, name)
            jpg_dir = find_subdir(sess, "jpg")
            if jpg_dir is None:
                raise FileNotFoundError(f"{name} 缺少 jpg/")
            rows = self.db.query(
                "SELECT id, jpg_name FROM gallery_photos WHERE gallery_id = ? AND ready = 0 AND error IS NULL"
                " ORDER BY idx", (gid,))
            with ThreadPoolExecutor(max_workers=self.cfg.preview_workers) as ex:
                list(ex.map(lambda r: self._render_photo(g, jpg_dir, r), rows))
            c = gs.counts(self.db, gid)
            if c["ready"] == 0:
                raise PublishError("所有照片都生成失败")
            err = f"{c['failed']} 张照片生成失败，客户看不到它们" if c["failed"] else None
            self.db.execute(
                "UPDATE galleries SET status = 'open', published_at = ?, build_error = ?"
                " WHERE id = ? AND status = 'building'", (time.time(), err, gid))
        except Exception as e:
            log.exception("相册 %s 生成失败", gid)
            self.db.execute("UPDATE galleries SET build_error = ? WHERE id = ?",
                            (f"{type(e).__name__}: {e}", gid))

    # ------------------------------------------------------------ 交付成片
    def delivery_folders(self, g: dict) -> list[dict]:
        """出门目录下可以作为成片发布的子文件夹（排除 jpg、raw、select），带 JPG 张数。"""
        sess = session_dir(self.cfg.photo_root, self.current_dir(g))
        out = []
        with os.scandir(sess) as it:
            for e in it:
                if e.name.startswith(".") or e.name.lower() in DELIVERY_EXCLUDE:
                    continue
                if not e.is_dir(follow_symlinks=False):
                    continue
                out.append({"name": e.name, "jpg_count": len(self._jpgs(Path(e.path)))})
        out.sort(key=lambda x: x["name"].lower())
        return out

    @staticmethod
    def _jpgs(folder: Path) -> list[os.DirEntry]:
        with os.scandir(folder) as it:
            files = [e for e in it if not e.name.startswith(".") and e.is_file(follow_symlinks=False)
                     and os.path.splitext(e.name)[1].lower() in JPG_EXTS]
        files.sort(key=lambda e: e.name.lower())
        return files

    def start_delivery(self, g: dict, folder: str) -> None:
        names = {f["name"] for f in self.delivery_folders(g)}
        if folder not in names:
            raise PublishError("这个文件夹不能用来交付")
        self.db.execute(
            "UPDATE galleries SET delivery_dir = ?, delivery_status = 'building', delivery_error = NULL"
            " WHERE id = ?", (folder, g["id"]))
        self._spawn("delivery", g["id"], self._import_delivery, folder)

    def _import_delivery(self, gid: int, folder: str) -> None:
        g = gs.get(self.db, gid)
        if g is None or g["deleted_at"]:
            return
        gdir = self.cfg.galleries_dir
        try:
            src_dir = session_dir(self.cfg.photo_root, self.current_dir(g)) / folder
            files = self._jpgs(src_dir)
            if not files:
                raise PublishError(f"{folder} 里没有 JPG")
            # 替换旧的交付内容
            self.db.execute("DELETE FROM gallery_deliveries WHERE gallery_id = ?", (gid,))
            gs.remove_delivery_files(gdir, gid)
            for i, e in enumerate(files):
                cur = self.db.execute(
                    "INSERT INTO gallery_deliveries (gallery_id, idx, name, size) VALUES (?, ?, ?, ?)",
                    (gid, i, e.name, e.stat(follow_symlinks=False).st_size))
                did = cur.lastrowid
                src = Path(e.path)
                _copy_into_bundle(src, gs.delivery_file(gdir, gid, did, "file"))
                img, _ = load_display_image(src, self.cfg.gallery_preview_size)
                atomic_save(img, gs.delivery_file(gdir, gid, did, "preview"), quality=88)
                w, h = img.size
                img.thumbnail((self.cfg.gallery_thumb_size, self.cfg.gallery_thumb_size))
                atomic_save(img, gs.delivery_file(gdir, gid, did, "thumb"), quality=80)
                self.db.execute("UPDATE gallery_deliveries SET ready = 1, width = ?, height = ? WHERE id = ?",
                                (w, h, did))
            self.db.execute(
                "UPDATE galleries SET delivery_status = 'ready', delivered_at = ? WHERE id = ?",
                (time.time(), gid))
        except Exception as e:
            log.exception("相册 %s 交付失败", gid)
            self.db.execute(
                "UPDATE galleries SET delivery_status = 'failed', delivery_error = ? WHERE id = ?",
                (f"{type(e).__name__}: {e}", gid))

    def cancel_delivery(self, g: dict) -> None:
        self.db.execute("DELETE FROM gallery_deliveries WHERE gallery_id = ?", (g["id"],))
        gs.remove_delivery_files(self.cfg.galleries_dir, g["id"])
        self.db.execute(
            "UPDATE galleries SET delivery_status = NULL, delivery_dir = NULL, delivery_error = NULL,"
            " delivered_at = NULL WHERE id = ?", (g["id"],))

    # ------------------------------------------------------------ 导出客户所选 RAW
    def _export_items(self, g: dict) -> list[dict]:
        return self.db.query(
            "SELECT jpg_name, raw_name FROM gallery_photos WHERE gallery_id = ? AND selected = 1 ORDER BY idx",
            (g["id"],))

    def _export_session(self, g: dict) -> int:
        if g["session_id"] is None:
            raise PublishError("这个相册对应的出门记录已经删除，没法导出")
        return g["session_id"]

    def export_plan(self, g: dict, dest: str) -> Plan:
        check_dest_name(dest)
        return build_plan(self.cfg, self.db, self._export_session(g), dest, self._export_items(g))

    def start_export(self, g: dict, dest: str) -> int:
        check_dest_name(dest)
        return self.exports.start(self._export_session(g), dest, self._export_items(g), gallery_id=g["id"])

    # ------------------------------------------------------------ 删除
    def delete(self, g: dict) -> None:
        """手动删除：相册文件和数据库记录都删掉。"""
        gs.remove_bundle(self.cfg.galleries_dir, g["id"])
        self.db.execute("DELETE FROM galleries WHERE id = ?", (g["id"],))

    def sweep(self, now: float | None = None) -> list[int]:
        """到了自动删除日期的相册：删掉文件，客户链接失效；客户的选择和备注保留在数据库里。"""
        now = time.time() if now is None else now
        rows = self.db.query(
            "SELECT id FROM galleries WHERE delete_at IS NOT NULL AND delete_at <= ? AND deleted_at IS NULL",
            (now,))
        for r in rows:
            gs.remove_bundle(self.cfg.galleries_dir, r["id"])
            self.db.execute(
                "UPDATE galleries SET deleted_at = ?, delivery_status = NULL WHERE id = ?", (now, r["id"]))
            log.info("相册 %s 已到期，文件已删除", r["id"])
        return [r["id"] for r in rows]
