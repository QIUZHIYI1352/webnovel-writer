# -*- coding: utf-8 -*-
"""投稿封面合成器：AI 底图 + 矢量文字 → 600x800 jpg/png

用法：
    python make_cover.py --bg <底图.png> --title 书名 --author 笔名 \
        --sub "题材标签 · 题材标签" --out <输出目录>

要点：
  - 底图必须**不含任何文字**（AI 画的中文封面字必乱码）
  - 文字全部由本脚本用本机字体绘制，保证锐利可控
  - 输出严格 600x800（平台要求 3:4 / jpg-png / <5MB）
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 600, 800

F_TITLE = "C:/Windows/Fonts/STXINGKA.TTF"   # 华文行楷，书名首选
F_SONG = "C:/Windows/Fonts/STSONG.TTF"      # 华文宋体，署名
F_HEI = "C:/Windows/Fonts/STXIHEI.TTF"      # 华文细黑，副题

GOLD = (216, 186, 122)
GOLD_DIM = (196, 170, 132)
PAPER = (232, 220, 196)
CRIMSON = (176, 48, 44)


def cover_crop(im, tw, th, anchor=0.40):
    """按目标比例居中裁切（cover）；anchor 控制纵向取景重心"""
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = int((nh - th) * anchor)
    return im.crop((left, top, left + tw, top + th))


def strip_watermark(im, box_ratio=(0.80, 0.955, 1.0, 1.0)):
    """淡化右下角平台水印：局部高斯模糊 + 压暗"""
    w, h = im.size
    box = (int(w * box_ratio[0]), int(h * box_ratio[1]),
           int(w * box_ratio[2]), int(h * box_ratio[3]))
    region = im.crop(box).filter(ImageFilter.GaussianBlur(9))
    region = Image.blend(region, Image.new("RGB", region.size, (0, 0, 0)), 0.55)
    im.paste(region, box)
    return im


def vignette(im):
    """顶/中/底三段压暗：顶部托书名、中段分离主体、底部托署名"""
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        a = 0
        if y < 470:
            a = int(214 * (1 - y / 470.0) ** 1.10)
        if 285 <= y <= 690:
            a = max(a, 74)
        if 470 < y < 620:
            a = max(a, int(74 * (1 - abs(y - 545) / 75.0)))
        if y > 615:
            a = max(a, int(152 * ((y - 615) / 185.0) ** 1.2))
        gd.line([(0, y), (W, y)], fill=a)
    return Image.composite(Image.new("RGB", (W, H), (6, 5, 6)), im,
                           grad.point(lambda v: v))


def hline(d, x0, x1, y, color, width=1):
    d.line([(x0, y), (x1, y)], fill=color, width=width)


def build(bg_path, title, author, sub, out_dir):
    im = cover_crop(Image.open(bg_path).convert("RGB"), W, H)
    im = vignette(strip_watermark(im))

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # ---- 书名：竖排书法字（上 1/2）----
    chars = list(title)
    # 字多则缩小、字少则放大，保证整体落在上半部
    size = 96 if len(chars) <= 4 else max(64, int(384 / len(chars)))
    step = int(size * 1.04)
    f_title = ImageFont.truetype(F_TITLE, size)
    y0 = max(30, (400 - step * len(chars)) // 2 + 40)
    for i, ch in enumerate(chars):
        bbox = d.textbbox((0, 0), ch, font=f_title)
        cw = bbox[2] - bbox[0]
        cx = W // 2 - (bbox[0] + cw // 2)
        cy = y0 + i * step
        for dx in range(-3, 4):            # 暗色描边，保证亮部可读
            for dy in range(-3, 4):
                d.text((cx + dx, cy + dy), ch, font=f_title, fill=(24, 8, 8, 190))
        d.text((cx, cy), ch, font=f_title, fill=GOLD + (255,))

    title_bottom = y0 + step * len(chars)
    for yy in (y0 + 38, title_bottom - 42):
        hline(d, 92, 212, yy, GOLD_DIM + (130,), 1)
        hline(d, 388, 508, yy, GOLD_DIM + (130,), 1)

    # ---- 副题：主视觉下沿，字距手工撑开 ----
    if sub:
        f_sub = ImageFont.truetype(F_HEI, 23)
        gap, sub_y = 9, 656
        total = sum(d.textlength(c, font=f_sub) for c in sub) + gap * (len(sub) - 1)
        sx = (W - total) / 2
        for c in sub:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    d.text((sx + dx, sub_y + dy), c, font=f_sub, fill=(10, 6, 6, 200))
            d.text((sx, sub_y), c, font=f_sub, fill=GOLD_DIM + (235,))
            sx += d.textlength(c, font=f_sub) + gap

    # ---- 署名：贴底 + 朱红短线 ----
    f_auth = ImageFont.truetype(F_SONG, 36)
    ay = 735
    ax = (W - d.textlength(author, font=f_auth)) / 2
    for dx in (-2, -1, 0, 1, 2):
        for dy in (-2, -1, 0, 1, 2):
            d.text((ax + dx, ay + dy), author, font=f_auth, fill=(8, 5, 5, 210))
    d.text((ax, ay), author, font=f_auth, fill=PAPER + (255,))
    hline(d, W // 2 - 34, W // 2 + 34, ay - 24, CRIMSON + (225,), 2)

    out = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    os.makedirs(out_dir, exist_ok=True)
    jpg = os.path.join(out_dir, f"{title}-封面-600x800.jpg")
    png = os.path.join(out_dir, f"{title}-封面-600x800.png")
    out.save(jpg, "JPEG", quality=93, optimize=True, subsampling=1)
    out.save(png, "PNG", optimize=True)

    for p in (jpg, png):
        kb = os.path.getsize(p) / 1024
        assert kb < 5120, f"{p} 超过 5MB"
        print(f"{p}  {out.size[0]}x{out.size[1]}  {kb:.0f} KB")
    print("OK：尺寸/格式/体积均合规")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bg", required=True, help="AI 底图（必须无文字）")
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--sub", default="")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    build(a.bg, a.title, a.author, a.sub, a.out)
