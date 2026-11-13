"""照片目录里唯一允许的写操作都在这里。

照片根目录是读写挂载的（select/ 建在每个出门目录里），所以源文件的安全
完全靠这一层保证：

* 只能写到 <出门目录>/select/ 里，路径会解析真实位置，挡住 .. 和符号链接；
* 只会新建文件，不会覆盖已有文件；
* 唯一的删除操作是清理我们自己写的 .part 临时文件。

程序其他地方不允许直接对照片目录调用 open(..., "w")、unlink、rename 等。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

SELECT_DIR_NAME = "select"
PART_SUFFIX = ".part"


class UnsafePathError(Exception):
    """目标路径不在允许写入的位置。"""


def _check_plain_name(name: str) -> None:
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise UnsafePathError(f"非法文件名: {name!r}")


def session_dir(photo_root: Path, dir_name: str) -> Path:
    """返回出门目录路径，并确认它是照片根目录的直接子目录。"""
    _check_plain_name(dir_name)
    if dir_name.startswith("."):
        raise UnsafePathError(f"非法目录名: {dir_name!r}")
    path = photo_root / dir_name
    if path.resolve().parent != photo_root.resolve():
        raise UnsafePathError(f"目录不在照片根目录下: {dir_name!r}")
    return path


def select_dir(session_path: Path) -> Path:
    return session_path / SELECT_DIR_NAME


def ensure_select_dir(session_path: Path) -> Path:
    """创建 select/（已存在则直接用）。拒绝符号链接。"""
    d = select_dir(session_path)
    if d.is_symlink():
        raise UnsafePathError(f"select 是符号链接，拒绝写入: {d}")
    d.mkdir(exist_ok=True)
    if not d.is_dir() or d.is_symlink():
        raise UnsafePathError(f"select 不是普通目录: {d}")
    return d


def target_path(session_path: Path, name: str) -> Path:
    """select/ 下某个文件的路径，检查它确实落在 select/ 里。"""
    _check_plain_name(name)
    d = select_dir(session_path)
    if d.is_symlink():
        raise UnsafePathError(f"select 是符号链接，拒绝写入: {d}")
    p = d / name
    if p.is_symlink():
        raise UnsafePathError(f"目标是符号链接，拒绝写入: {p}")
    if p.parent.resolve() != d.resolve():
        raise UnsafePathError(f"目标不在 select/ 内: {p}")
    return p


def part_path(session_path: Path, name: str) -> Path:
    return target_path(session_path, f".{name}{PART_SUFFIX}")


def open_part(session_path: Path, name: str) -> tuple[BinaryIO, Path]:
    """打开 .<name>.part 临时文件用于写入。"""
    p = part_path(session_path, name)
    return open(p, "wb"), p


def finalize_part(session_path: Path, name: str, atime_ns: int, mtime_ns: int) -> Path:
    """设置修改时间后，把 .part 改名为正式文件。目标已存在则拒绝。"""
    part = part_path(session_path, name)
    final = target_path(session_path, name)
    if final.exists():
        raise FileExistsError(f"目标已存在，不覆盖: {final.name}")
    os.utime(part, ns=(atime_ns, mtime_ns))
    os.rename(part, final)
    return final


def remove_part(session_path: Path, name: str) -> None:
    """删除我们自己的 .part 临时文件（唯一的删除操作）。"""
    p = part_path(session_path, name)
    if p.is_file() and not p.is_symlink():
        p.unlink()
