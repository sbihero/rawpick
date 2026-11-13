"""预览生成：缩略图（网格用）+ 大预览（单张浏览用），缓存在 DATA_DIR/cache。

* Pillow 的 JPEG 草稿模式在解码时直接按 1/2~1/8 缩小，速度快；
* 按 EXIF 方向旋转，嵌入的色彩配置转换为 sRGB；
* 后台线程按顺序批量生成；前端请求到还没生成的图时，由请求线程立即生成（插队）。

只写 DATA_DIR，不碰照片目录。
"""

from __future__ import annotations

import io
import itertools
import json
import logging
import os
import queue
import tempfile
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .config import Config
from .db import Database
from .safety import session_dir
from .scanner import find_subdir, photo_version

try:
    from PIL import ImageCms

    _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
except Exception:  # pragma: no cover - 没有 littlecms 时跳过色彩转换
    ImageCms = None
    _SRGB = None

log = logging.getLogger("rawpick.previews")

KINDS = ("thumb", "preview")


# ---------------------------------------------------------------- EXIF

def _num(v: Any) -> float | None:
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return v[0] / v[1] if v[1] else None
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _fmt_shutter(sec: float) -> str:
    if sec >= 1:
        return f"{sec:g}s"
    frac = Fraction(sec).limit_denominator(16000)
    if frac.numerator == 1:
        return f"1/{frac.denominator}s"
    return f"1/{round(1 / sec)}s"


def extract_exif(im: Image.Image) -> dict[str, str]:
    """读出快门、光圈、ISO、焦距、拍摄时间，转成显示用字符串。"""
    out: dict[str, str] = {}
    try:
        exif = im.getexif()
        ifd = exif.get_ifd(0x8769)
    except Exception:
        return out
    shutter = _num(ifd.get(0x829A))
    if shutter:
        out["shutter"] = _fmt_shutter(shutter)
    fnum = _num(ifd.get(0x829D))
    if fnum:
        out["aperture"] = f"f/{fnum:.1f}".replace(".0", "")
    iso = ifd.get(0x8827)
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    if iso:
        out["iso"] = f"ISO {int(iso)}"
    focal = _num(ifd.get(0x920A))
    if focal:
        out["focal"] = f"{focal:.0f}mm"
    taken = ifd.get(0x9003)
    if isinstance(taken, str) and taken.strip():
        out["taken"] = taken.strip()
    return out


def read_exif_only(path: Path) -> dict[str, str]:
    with Image.open(path) as im:
        return extract_exif(im)


# ---------------------------------------------------------------- 渲染

def _to_srgb(im: Image.Image, icc: bytes | None) -> Image.Image:
    if not icc or ImageCms is None:
        return im.convert("RGB")
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in ImageCms.getProfileDescription(src).lower():
            return im.convert("RGB")
        if im.mode not in ("RGB", "CMYK"):
            im = im.convert("RGB")
        return ImageCms.profileToProfile(im, src, _SRGB, outputMode="RGB")
    except Exception:
        return im.convert("RGB")


def _atomic_save(im: Image.Image, dest: Path, quality: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-", suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as f:
            im.save(f, "JPEG", quality=quality, optimize=False)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render_previews(src: Path, preview_path: Path, thumb_path: Path,
                    preview_size: int, thumb_size: int) -> dict[str, str]:
    """生成大预览和缩略图，返回 EXIF 信息。"""
    with Image.open(src) as im:
        info = extract_exif(im)
        icc = im.info.get("icc_profile")
        im.draft("RGB", (preview_size, preview_size))
        img = ImageOps.exif_transpose(im)
    img = _to_srgb(img, icc)
    img.thumbnail((preview_size, preview_size), Image.Resampling.BICUBIC, reducing_gap=2.0)
    _atomic_save(img, preview_path, quality=85)
    img.thumbnail((thumb_size, thumb_size), Image.Resampling.BICUBIC, reducing_gap=2.0)
    _atomic_save(img, thumb_path, quality=80)
    return info


# ---------------------------------------------------------------- 管理器

class PreviewManager:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self._queue: queue.PriorityQueue[tuple[int, int, int]] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # 路径 -----------------------------------------------------------
    def cache_path(self, session_id: int, photo_id: int, kind: str) -> Path:
        assert kind in KINDS
        return self.cfg.cache_dir / str(session_id) / f"{photo_id}-{kind}.jpg"

    def jpg_path(self, photo: dict, dir_name: str) -> Path:
        sess = session_dir(self.cfg.photo_root, dir_name)
        jpg_dir = find_subdir(sess, "jpg")
        if jpg_dir is None:
            raise FileNotFoundError(f"{dir_name} 缺少 jpg/")
        return jpg_dir / photo["jpg_name"]

    # 生命周期 -------------------------------------------------------
    def start(self) -> None:
        for i in range(self.cfg.preview_workers):
            t = threading.Thread(target=self._worker, name=f"preview-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for _ in self._threads:
            self._queue.put((-1, next(self._seq), -1))

    def _lock_for(self, photo_id: int) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(photo_id)
            if lock is None:
                lock = self._locks[photo_id] = threading.Lock()
            return lock

    # 对外接口 -------------------------------------------------------
    def enqueue_session(self, session_id: int) -> int:
        rows = self.db.query(
            "SELECT id, jpg_mtime_ns, jpg_size, preview_ver, preview_error FROM photos"
            " WHERE session_id = ? ORDER BY jpg_name COLLATE NOCASE",
            (session_id,),
        )
        n = 0
        for r in rows:
            if r["preview_error"]:
                continue
            if r["preview_ver"] != photo_version(r["jpg_mtime_ns"], r["jpg_size"]):
                self._queue.put((10, next(self._seq), r["id"]))
                n += 1
        return n

    def is_ready(self, photo: dict) -> bool:
        return photo["preview_ver"] == photo_version(photo["jpg_mtime_ns"], photo["jpg_size"])

    def ensure(self, photo_id: int) -> dict:
        """确保这张照片的预览存在且是最新的；需要时立即生成。返回照片行。"""
        with self._lock_for(photo_id):
            row = self.db.query_one(
                "SELECT p.*, s.dir_name FROM photos p JOIN sessions s ON s.id = p.session_id"
                " WHERE p.id = ?",
                (photo_id,),
            )
            if row is None:
                raise KeyError(photo_id)
            version = photo_version(row["jpg_mtime_ns"], row["jpg_size"])
            paths = [self.cache_path(row["session_id"], photo_id, k) for k in KINDS]
            if row["preview_ver"] == version and all(p.exists() for p in paths):
                return row
            src = self.jpg_path(row, row["dir_name"])
            try:
                info = render_previews(src, paths[1], paths[0],
                                       self.cfg.preview_size, self.cfg.thumb_size)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.db.execute("UPDATE photos SET preview_error = ? WHERE id = ?", (msg, photo_id))
                log.warning("生成预览失败 %s: %s", src, msg)
                raise
            self.db.execute(
                "UPDATE photos SET preview_ver = ?, preview_error = NULL, exif = ? WHERE id = ?",
                (version, json.dumps(info, ensure_ascii=False), photo_id),
            )
            row.update(preview_ver=version, preview_error=None, exif=json.dumps(info))
            return row

    def status(self, session_id: int) -> dict[str, int]:
        rows = self.db.query(
            "SELECT jpg_mtime_ns, jpg_size, preview_ver, preview_error FROM photos WHERE session_id = ?",
            (session_id,),
        )
        ready = sum(1 for r in rows if self.is_ready(r))
        failed = sum(1 for r in rows if r["preview_error"] and not self.is_ready(r))
        return {"total": len(rows), "ready": ready, "failed": failed}

    def _worker(self) -> None:
        while not self._stop.is_set():
            _, _, photo_id = self._queue.get()
            if photo_id < 0:
                return
            try:
                self.ensure(photo_id)
            except KeyError:
                pass  # 照片已被删除
            except Exception:
                pass  # 已记录到 preview_error
