"""给客户看的预览加文字水印：居中、半透明、白字细黑边，深浅背景上都能看清。

只处理内存里的图片，不读写文件。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("rawpick.watermark")

# 按顺序找第一个存在的字体。Docker 镜像里带了第一个；开发机上一般能找到系统的思源黑体。
FONT_CANDIDATES = (
    "/app/fonts/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)

TEXT_WIDTH = 0.45      # 文字宽度约占图片宽度的比例
MAX_HEIGHT = 0.14      # 字号上限（相对短边）
FILL_ALPHA = 96        # 白字透明度（0–255）
STROKE_ALPHA = 60      # 黑边透明度


def find_font(preferred: Path | None = None) -> Path | None:
    if preferred is not None:
        return preferred if preferred.is_file() else None
    for c in FONT_CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    return None


@functools.lru_cache(maxsize=32)
def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            log.warning("水印字体读取失败：%s", path)
    return ImageFont.load_default(size)


def apply_watermark(img: Image.Image, text: str, font_path: Path | None) -> Image.Image:
    """返回加了水印的新图（RGB）。text 为空时原样返回。"""
    text = text.strip()
    if not text:
        return img
    w, h = img.size
    short = min(w, h)
    fp = str(font_path) if font_path else None
    # 先用一个参考字号量出文字宽度，再按比例算出目标字号
    ref = 100
    l, t, r, b = ImageDraw.Draw(img).textbbox((0, 0), text, font=_font(fp, ref))
    ref_w = max(1, r - l)
    size = int(ref * (w * TEXT_WIDTH) / ref_w)
    size = max(10, min(size, int(short * MAX_HEIGHT)))
    font = _font(fp, size)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    stroke = max(1, size // 32)
    l, t, r, b = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x = (w - (r - l)) / 2 - l
    y = (h - (b - t)) / 2 - t
    draw.text((x, y), text, font=font, fill=(255, 255, 255, FILL_ALPHA),
              stroke_width=stroke, stroke_fill=(0, 0, 0, STROKE_ALPHA))
    out = img.convert("RGBA")
    out.alpha_composite(overlay)
    return out.convert("RGB")
