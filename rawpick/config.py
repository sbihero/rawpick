"""运行配置。全部来自环境变量，Docker 和本地开发共用同一套。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

JPG_EXTS: tuple[str, ...] = (".jpg", ".jpeg")
DEFAULT_RAW_EXTS: tuple[str, ...] = (
    ".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng", ".orf", ".rw2",
)


@dataclass(frozen=True)
class Config:
    photo_root: Path
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    raw_exts: tuple[str, ...] = DEFAULT_RAW_EXTS
    thumb_size: int = 400
    preview_size: int = 2560
    preview_workers: int = 2
    umask: int | None = None
    client_port: int = 8081               # 客户端（相册）端口；0 表示不启动
    gallery_preview_size: int = 2048      # 给客户看的预览长边
    gallery_thumb_size: int = 400
    watermark_font: Path | None = None    # 水印字体；不设置时自动找系统里的中文字体

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rawpick.db"

    @property
    def galleries_dir(self) -> Path:
        return self.data_dir / "galleries"


def _parse_exts(value: str) -> tuple[str, ...]:
    exts = []
    for part in value.replace(";", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        exts.append(part if part.startswith(".") else "." + part)
    return tuple(exts)


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    """从环境变量读取配置。

    PHOTO_ROOT       照片根目录，下面每个一级子目录是一次出门（默认 ./photos）
    DATA_DIR         数据库和预览缓存目录（默认 ./data）
    HOST / PORT      管理员端监听地址（默认 0.0.0.0:8080）
    CLIENT_PORT      客户端（相册）端口（默认 8081，设为 0 不启动）
    WATERMARK_FONT   水印字体文件（默认自动找系统里的中文字体）
    RAW_EXTS         RAW 扩展名白名单，逗号分隔（默认 arw,cr2,cr3,nef,raf,dng,orf,rw2）
    PREVIEW_WORKERS  后台生成预览的线程数（默认 CPU 核数，最多 4）
    UMASK            新建文件的 umask，八进制（Unraid 建议 000）
    """
    cpu = os.cpu_count() or 2
    umask_raw = env.get("UMASK", "").strip()
    return Config(
        photo_root=Path(env.get("PHOTO_ROOT", "./photos")).expanduser(),
        data_dir=Path(env.get("DATA_DIR", "./data")).expanduser(),
        host=env.get("HOST", "0.0.0.0"),
        port=int(env.get("PORT", "8080")),
        raw_exts=_parse_exts(env["RAW_EXTS"]) if env.get("RAW_EXTS") else DEFAULT_RAW_EXTS,
        thumb_size=int(env.get("THUMB_SIZE", "400")),
        preview_size=int(env.get("PREVIEW_SIZE", "2560")),
        preview_workers=max(1, int(env.get("PREVIEW_WORKERS", str(min(4, cpu))))),
        umask=int(umask_raw, 8) if umask_raw else None,
        client_port=int(env.get("CLIENT_PORT", "8081") or 0),
        watermark_font=Path(env["WATERMARK_FONT"]) if env.get("WATERMARK_FONT") else None,
    )
