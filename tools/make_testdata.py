"""生成测试用的出门目录：带 EXIF 的 JPG + 同名的假 RAW。

用法：
    python tools/make_testdata.py <照片根目录> [--sessions 2] [--count 30]
                                  [--width 1600] [--height 1067] [--raw-mb 2]
                                  [--edge-cases] [--noise]

--edge-cases 会在第一个出门目录里放进各种异常：缺 RAW、只有 RAW、
扩展名冲突、大小写混用、jpg/ 里有子目录、竖拍（Orientation=6）。
--noise 生成带噪点的图，文件大小接近真实照片（测速度用）。
"""

from __future__ import annotations

import argparse
import colorsys
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, TiffImagePlugin

SHUTTERS = [(1, 4000), (1, 1000), (1, 250), (1, 60), (1, 15), (1, 2), (2, 1)]
APERTURES = [(14, 10), (18, 10), (28, 10), (40, 10), (56, 10), (80, 10), (110, 10)]
ISOS = [100, 200, 400, 800, 1600, 3200]
FOCALS = [16, 24, 35, 50, 85, 135, 200]


def make_exif(rng: random.Random, orientation: int, index: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    exif[0x0110] = "TestCam R1"  # Model
    ifd = exif.get_ifd(0x8769)
    s = rng.choice(SHUTTERS)
    a = rng.choice(APERTURES)
    ifd[0x829A] = TiffImagePlugin.IFDRational(*s)  # ExposureTime
    ifd[0x829D] = TiffImagePlugin.IFDRational(*a)  # FNumber
    ifd[0x8827] = rng.choice(ISOS)  # ISOSpeedRatings
    ifd[0x920A] = TiffImagePlugin.IFDRational(rng.choice(FOCALS), 1)  # FocalLength
    ifd[0x9003] = f"2026:11:08 10:{index // 60 % 60:02d}:{index % 60:02d}"  # DateTimeOriginal
    return exif.tobytes()


def make_image(rng: random.Random, w: int, h: int, label: str, noise: bool) -> Image.Image:
    hue = rng.random()
    c1 = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(hue, 0.55, 0.85))
    c2 = tuple(int(v * 255) for v in colorsys.hsv_to_rgb((hue + 0.15) % 1, 0.7, 0.35))
    # 先在小图上画渐变再放大，速度快
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126
            px[x, y] = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
    img = small.resize((w, h), Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        r = rng.randint(h // 12, h // 4)
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        col = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(rng.random(), 0.4, 0.95))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=max(2, h // 150))
    try:
        font = ImageFont.load_default(size=h // 6)
    except TypeError:  # 老版本 Pillow
        font = ImageFont.load_default()
    draw.text((w // 2, h // 2), label, fill=(255, 255, 255), font=font, anchor="mm",
              stroke_width=max(2, h // 200), stroke_fill=(0, 0, 0))
    if noise:
        n = Image.effect_noise((w, h), 48).convert("RGB")
        img = Image.blend(img, n, 0.22)
    return img


def write_photo(jpg_dir: Path, raw_dir: Path | None, stem: str, rng: random.Random, args,
                index: int, orientation: int = 1, jpg_ext: str = ".JPG", raw_ext: str = ".ARW",
                label: str | None = None) -> None:
    w, h = args.width, args.height
    if orientation in (6, 8):
        # 竖拍：像素按横向存储，靠 Orientation 标签旋转显示
        label = (label or stem) + " (rotated)"
    img = make_image(rng, w, h, label or stem, args.noise)
    img.save(jpg_dir / f"{stem}{jpg_ext}", quality=92 if args.noise else 85,
             exif=make_exif(rng, orientation, index))
    if raw_dir is not None:
        size = int(args.raw_mb * 1024 * 1024 * rng.uniform(0.8, 1.2))
        (raw_dir / f"{stem}{raw_ext}").write_bytes(os.urandom(size))


def make_session(root: Path, name: str, args, rng: random.Random, edge_cases: bool) -> None:
    sess = root / name
    jpg_dir, raw_dir = sess / "jpg", sess / "raw"
    jpg_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, args.count + 1):
        stem = f"DSC{i:05d}"
        if edge_cases and i == 3:
            # 竖拍
            write_photo(jpg_dir, raw_dir, stem, rng, args, i, orientation=6)
        elif edge_cases and i == 5:
            # 缺 RAW
            write_photo(jpg_dir, None, stem, rng, args, i, label=f"{stem} no RAW")
        elif edge_cases and i == 7:
            # 扩展名冲突：同一主体有 .JPG 和 .jpeg
            write_photo(jpg_dir, raw_dir, stem, rng, args, i)
            write_photo(jpg_dir, None, stem, rng, args, i, jpg_ext=".jpeg")
        elif edge_cases and i == 9:
            # 大小写混用
            write_photo(jpg_dir, raw_dir, "dsc00009", rng, args, i, jpg_ext=".jpg", raw_ext=".ARW")
        else:
            write_photo(jpg_dir, raw_dir, stem, rng, args, i)
    if edge_cases:
        # 只有 RAW
        (raw_dir / "DSC09999.ARW").write_bytes(os.urandom(1024 * 64))
        # jpg/ 里多出来的子目录
        (jpg_dir / "extra").mkdir(exist_ok=True)
        # 不相关的文件应被忽略
        (raw_dir / "DSC00001.xmp").write_text("<x:xmpmeta/>")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root", type=Path)
    p.add_argument("--sessions", type=int, default=2)
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--width", type=int, default=1600)
    p.add_argument("--height", type=int, default=1067)
    p.add_argument("--raw-mb", type=float, default=2.0)
    p.add_argument("--edge-cases", action="store_true")
    p.add_argument("--noise", action="store_true")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.seed)
    args.root.mkdir(parents=True, exist_ok=True)
    for s in range(args.sessions):
        name = f"2026-11-{8 - s:02d} 测试出门{s + 1}"
        make_session(args.root, name, args, rng, edge_cases=args.edge_cases and s == 0)
        print(f"已生成 {args.root / name}")
    # 不符合约定的目录：只有 jpg/，应被忽略
    (args.root / "不是出门目录" / "jpg").mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
