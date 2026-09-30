# -*- coding: utf-8 -*-
"""投稿封面合成器：AI 底图 + 矢量文字 → 600x800 jpg/png

用法：
    python make_cover.py --bg <底图.png> --title 书名 --author 笔名 \
        [--sub "题材标签 · 题材标签"] --out <输出目录>

要点：
  - 底图必须**不含任何文字**（AI 画的中文封面字必乱码，实测硬伤）
  - 文字全部由本脚本用本机字体绘制，保证锐利可控
  - 输出严格 600x800（平台要求 3:4 / jpg-png / <5MB）

长书名（5-8 字）的关键：竖排单列会自动缩字号并**在书名区内居中**，
所以必须同时确认「书名区下沿」不撞主视觉——用 --anchor 微调取景，
让主体往下走，给书名腾出干净空间。
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
    """按目标比例居中裁切（cover）；anchor 控制纵向取景重心。
    anchor 越小 → 保留越多顶部 → 主体在画面中的位置越靠下。"""
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = int((nh - th) * anchor)
    return im.crop((left, top, left + tw, top + th))


def strip_watermark(im, x0=0.80, y0=0.955, blur=9):
    """淡化右下角平台水印：局部高斯模糊 + 压暗"""
    w, h = im.size
    box = (int(w * x0), int(h * y0), w, h)
    region = im.crop(box).filter(ImageFilter.GaussianBlur(blur))
    region = Image.blend(region, Image.new("RGB", region.size, (0, 0, 0)), 0.55)
    im.paste(region, box)
    return im


def vignette(im, mid1=285, mid2=690, mid_a=74):
    """顶/中/底三段压暗：顶部托书名、中段分离主体、底部托署名"""
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        a = 0
        if y < 470:
            a = int(214 * (1 - y / 470.0) ** 1.10)
        if mid1 <= y <= mid2:
            a = max(a, mid_a)
        if 470 < y < 620:
            a = max(a, int(mid_a * (1 - abs(y - 545) / 75.0)))
        if y > 615:
            a = max(a, int(152 * ((y - 615) / 185.0) ** 1.2))
        gd.line([(0, y), (W, y)], fill=a)
    return Image.composite(Image.new("RGB", (W, H), (6, 5, 6)), im,
                           grad.point(lambda v: v))


def hline(d, x0, x1, y, color, width=1):
    d.line([(x0, y), (x1, y)], fill=color, width=width)


def fit_title_layout(title, zone_top, zone_h, size_max, size_min):
    """竖排书名自适应：算出字号、字距、起始 y，使整列在书名区内居中。"""
    n = len(title)
    size = min(size_max, max(size_min, int(zone_h / (n * 1.06))))
    step = int(size * 1.06)
    total = step * n
    y0 = int(zone_top + max(0, (zone_h - total) / 2))
    return size, step, y0, total


def build(bg_path, title, author, sub, out_dir, anchor, zone_top, zone_h,
          size_max, size_min, wm):
    im = cover_crop(Image.open(bg_path).convert("RGB"), W, H, anchor)
    im = vignette(strip_watermark(im, y0=wm))

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # ---- 书名：竖排书法字，在书名区内居中 ----
    size, step, y0, total = fit_title_layout(title, zone_top, zone_h,
                                             size_max, size_min)
    f_title = ImageFont.truetype(F_TITLE, size)
    for i, ch in enumerate(title):
        bbox = d.textbbox((0, 0), ch, font=f_title)
        cw = bbox[2] - bbox[0]
        cx = W // 2 - (bbox[0] + cw // 2)
        cy = y0 + i * step
        for dx in range(-3, 4):            # 暗色描边，保证亮部可读
            for dy in range(-3, 4):
                d.text((cx + dx, cy + dy), ch, font=f_title, fill=(24, 8, 8, 190))
        d.text((cx, cy), ch, font=f_title, fill=GOLD + (255,))

    # 书名签：竖排单列两侧各一道细线（横向让开字形，不会压到字）
    # ⚠️ 不要用横线做装饰：竖排字距只有 3-6px，横线必然穿过字形。
    title_bottom = y0 + total
    lx = int(size * 0.94)
    vpad = max(4, int(size * 0.10))
    for x in (W // 2 - lx, W // 2 + lx):
        d.line([(x, y0 - vpad), (x, title_bottom + vpad)],
               fill=GOLD_DIM + (120,), width=1)

    # ---- 副题：主视觉下沿，字距手工撑开 ----
    if sub:
        f_sub = ImageFont.truetype(F_HEI, 23)
        gap, sub_y = 9, int(zone_top + zone_h + 8)
        total_w = sum(d.textlength(c, font=f_sub) for c in sub) + gap * (len(sub) - 1)
        sx = (W - total_w) / 2
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

    print(f"书名 {title}（{len(title)} 字）  字号 {size}  字距 {step}  "
          f"列 {y0}–{y0 + total}")
    for p in (jpg, png):
        kb = os.path.getsize(p) / 1024
        assert kb < 5120, f"{p} 超过 5MB"
        print(f"  {os.path.basename(p)}  {out.size[0]}x{out.size[1]}  {kb:.0f} KB")
    print("OK：尺寸/格式/体积均合规")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bg", required=True, help="AI 底图（必须无文字）")
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--sub", default="")
    ap.add_argument("--out", default=".")
    ap.add_argument("--anchor", type=float, default=0.40,
                    help="裁切锚点。越小主体越靠下（默认 0.40）")
    ap.add_argument("--title-top", type=int, default=26)
    ap.add_argument("--title-h", type=int, default=440)
    ap.add_argument("--size-max", type=int, default=96)
    ap.add_argument("--size-min", type=int, default=44)
    ap.add_argument("--wm", type=float, default=0.955, help="水印区上沿比例")
    a = ap.parse_args()
    build(a.bg, a.title, a.author, a.sub, a.out, a.anchor, a.title_top,
          a.title_h, a.size_max, a.size_min, a.wm)
