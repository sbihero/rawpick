from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rawpick.config import Config  # noqa: E402
from rawpick.db import Database  # noqa: E402

SESSION1 = "2026-11-08 测试出门1"  # 含各种异常
SESSION2 = "2026-11-07 测试出门2"


@pytest.fixture(scope="session")
def photo_template(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("template") / "photos"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_testdata.py"), str(root),
         "--count", "12", "--width", "480", "--height", "320", "--raw-mb", "0.2",
         "--edge-cases"],
        check=True, capture_output=True,
    )
    return root


@pytest.fixture
def photo_root(photo_template, tmp_path) -> Path:
    dst = tmp_path / "photos"
    shutil.copytree(photo_template, dst, copy_function=shutil.copy2)
    return dst


@pytest.fixture
def cfg(photo_root, tmp_path) -> Config:
    return Config(photo_root=photo_root, data_dir=tmp_path / "data",
                  thumb_size=200, preview_size=640, preview_workers=2)


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg.db_path)
    yield d
    d.close()


def snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    """照片目录（除 select/ 外）所有文件的 (大小, 修改时间, 内容)。"""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "select" not in p.relative_to(root).parts:
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns, p.read_bytes())
    return out
