from __future__ import annotations

import io
import json
import os
import struct
import time

import pytest
from conftest import SESSION1
from PIL import Image, ImageCms

from rawpick.previews import PreviewManager, extract_exif, render_previews
from rawpick.scanner import ensure_session_row, sync_session


@pytest.fixture
def mgr(cfg, db):
    sid = ensure_session_row(db, SESSION1)
    sync_session(cfg, db, sid)
    m = PreviewManager(cfg, db)
    yield m, sid
    m.stop()


def photo_id(db, stem):
    return db.query_one("SELECT id FROM photos WHERE stem = ?", (stem,))["id"]


def test_render_sizes_and_exif(mgr, db, cfg):
    m, sid = mgr
    pid = photo_id(db, "dsc00001")
    row = m.ensure(pid)
    prev = Image.open(m.cache_path(sid, pid, "preview"))
    thumb = Image.open(m.cache_path(sid, pid, "thumb"))
    assert max(prev.size) == 480  # 原图 480 宽，小于 preview_size 时不放大
    assert max(thumb.size) == cfg.thumb_size
    exif = json.loads(row["exif"])
    assert set(exif) == {"shutter", "aperture", "iso", "focal", "taken"}
    assert exif["shutter"].endswith("s") and exif["aperture"].startswith("f/")


def test_orientation_applied(mgr, db):
    m, sid = mgr
    pid = photo_id(db, "dsc00003")  # Orientation=6，像素横向存储
    m.ensure(pid)
    w, h = Image.open(m.cache_path(sid, pid, "preview")).size
    assert h > w


def test_downscale_large_image(tmp_path):
    src = tmp_path / "big.jpg"
    Image.new("RGB", (6000, 4000), (120, 80, 40)).save(src, quality=90)
    info = render_previews(src, tmp_path / "p.jpg", tmp_path / "t.jpg", 2560, 400)
    assert Image.open(tmp_path / "p.jpg").size == (2560, 1707)
    assert Image.open(tmp_path / "t.jpg").size == (400, 267)
    assert info == {}


def fake_wide_profile() -> bytes:
    """以内置 sRGB 配置为底，交换红绿原色并改掉描述，得到一个“非 sRGB”的 RGB 配置。"""
    data = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    count = struct.unpack(">I", data[128:132])[0]
    entries = {}
    for i in range(count):
        off = 132 + 12 * i
        entries[bytes(data[off:off + 4])] = off
    r, g = entries[b"rXYZ"], entries[b"gXYZ"]
    data[r + 4:r + 12], data[g + 4:g + 12] = data[g + 4:g + 12], data[r + 4:r + 12]
    data[:] = data.replace("sRGB".encode("utf-16-be"), "Wide".encode("utf-16-be"))
    data[:] = data.replace(b"sRGB", b"Wide")
    return bytes(data)


def test_non_srgb_profile_is_converted(tmp_path):
    """嵌入非 sRGB 配置的 JPG 应被转换（颜色改变），sRGB 的保持不变。"""
    color = (200, 120, 60)
    wide = fake_wide_profile()
    assert "srgb" not in ImageCms.getProfileDescription(
        ImageCms.ImageCmsProfile(io.BytesIO(wide))).lower()
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    out = {}
    for name, icc in (("xyz", wide), ("srgb", srgb)):
        src = tmp_path / f"{name}.jpg"
        Image.new("RGB", (800, 600), color).save(src, quality=100, icc_profile=icc)
        render_previews(src, tmp_path / f"{name}-p.jpg", tmp_path / f"{name}-t.jpg", 640, 200)
        out[name] = Image.open(tmp_path / f"{name}-p.jpg").getpixel((320, 240))
    assert all(abs(a - b) <= 3 for a, b in zip(out["srgb"], color))
    assert any(abs(a - b) > 10 for a, b in zip(out["xyz"], color))


def test_regenerates_when_jpg_changes(mgr, db, cfg, photo_root):
    m, sid = mgr
    pid = photo_id(db, "dsc00002")
    m.ensure(pid)
    first = m.cache_path(sid, pid, "preview").stat().st_mtime_ns
    jpg = photo_root / SESSION1 / "jpg" / "DSC00002.JPG"
    st = jpg.stat()
    os.utime(jpg, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
    sync_session(cfg, db, sid)
    time.sleep(0.01)
    m.ensure(pid)
    assert m.cache_path(sid, pid, "preview").stat().st_mtime_ns != first


def test_background_workers_fill_session(mgr, db):
    m, sid = mgr
    m.start()
    assert m.enqueue_session(sid) == 11
    deadline = time.time() + 20
    while time.time() < deadline:
        st = m.status(sid)
        if st["ready"] + st["failed"] == st["total"]:
            break
        time.sleep(0.05)
    assert m.status(sid) == {"total": 11, "ready": 11, "failed": 0}
    assert m.enqueue_session(sid) == 0


def test_corrupt_jpg_recorded(mgr, db, photo_root):
    m, sid = mgr
    (photo_root / SESSION1 / "jpg" / "DSC00004.JPG").write_bytes(b"not a jpeg")
    pid = photo_id(db, "dsc00004")
    with pytest.raises(Exception):
        m.ensure(pid)
    assert db.query_one("SELECT preview_error FROM photos WHERE id = ?", (pid,))["preview_error"]


def test_extract_exif_handles_missing():
    assert extract_exif(Image.new("RGB", (10, 10))) == {}
