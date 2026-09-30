# -*- coding: utf-8 -*-
"""把逐章 Markdown 正文转成可直接粘贴上架的纯文本分章文件。

网文平台的写作后台是富文本编辑器，粘 Markdown 会带出 `**` 星号；
章节名是独立输入框，不该混在正文里。本脚本一次解决这两个问题。

产出（每个章节一个 txt）：
    <out>/第NN章-<标题>.txt
      └ 第 1 行   = 章节名（粘到平台「章节名」输入框）
        空行以后 = 正文（已剥离 Markdown 标记）

用法：
    python split_chapters.py --src 04-正文 --out 09-分章上架
    python split_chapters.py --src 04-正文 --out 09-分章上架 --keep-md
    python split_chapters.py --src 04-正文 --list      # 只打印清单不写文件

参数：
    --src   逐章 md 目录（文件名需含章节号，如 第1章.md / ch01.md）
    --out   输出目录
    --keep-md  保留 Markdown 加粗标记（默认剥离）
    --bom  以 UTF-8 BOM 写文件（Windows 记事本友好，默认开启）
"""
import argparse
import os
import re
import sys

CN = "零一二三四五六七八九十"


def cn_num(n):
    """1 -> 一, 10 -> 十, 11 -> 十一, 21 -> 二十一"""
    if n <= 10:
        return "十" if n == 10 else CN[n]
    if n < 20:
        return "十" + CN[n - 10]
    if n < 100:
        return CN[n // 10] + "十" + (CN[n % 10] if n % 10 else "")
    return str(n)


def clean_body(body, keep_md=False):
    if not keep_md:
        # 星号要**一次清干净**：`**加粗**` 和 `*斜体*` 都会在平台编辑器里
        # 显示成字面星号。实测漏掉单星号斜体（第 7 章 `*犯者三，当归一。*`）
        # 会在正文里留下 2 个孤立的 `*`。
        body = re.sub(r"\*+", "", body)
        body = body.replace("__", "")
    lines = [ln.rstrip() for ln in body.split("\n")]
    out = []
    for ln in lines:
        if ln == "" and out and out[-1] == "":
            continue
        out.append(ln)
    return "\n".join(out).strip("\n")


def parse_chapter(path):
    """从 md 文件取出 (章号, 章名标题, 正文)"""
    t = open(path, encoding="utf-8").read().strip("\n")
    n = int(re.search(r"\d+", os.path.basename(path)).group())
    m = re.match(r"^#\s*(.+?)\s*\n(.*)$", t, re.S)
    if m:
        head, body = m.group(1), m.group(2)
    else:                      # 没有 # 标题，整篇当正文
        head, body = "", t
    part = re.split(r"[\u3000\s]+", head, maxsplit=1)
    title = part[1] if len(part) > 1 else ""
    return n, title, body


def counts(text):
    """返回 (汉字数, 含标点字符数) —— 唯一口径：去空白"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    allc = len(re.sub(r"\s", "", text))
    return cjk, allc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep-md", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-bom", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(a.src):
        raise SystemExit(f"错误：{a.src} 不是目录")
    files = [f for f in os.listdir(a.src)
             if f.endswith((".md", ".txt")) and re.search(r"\d+", f)]
    files.sort(key=lambda x: int(re.search(r"\d+", x).group()))
    if not files:
        raise SystemExit(f"错误：{a.src} 里找不到含章节号的文件")

    enc = "utf-8" if a.no_bom else "utf-8-sig"
    if a.out and not a.list:
        os.makedirs(a.out, exist_ok=True)

    rows = []
    for f in files:
        n, title, body = parse_chapter(os.path.join(a.src, f))
        body = clean_body(body, a.keep_md)
        chap_name = f"第{cn_num(n)}章" + (f"\u3000{title}" if title else "")
        cjk, allc = counts(body)
        rows.append((n, title, chap_name, cjk, allc))

        if a.out and not a.list:
            dst = os.path.join(a.out, f"第{n:02d}章-{title or '无题'}.txt")
            with open(dst, "w", encoding=enc, newline="\n") as fp:
                fp.write(chap_name + "\n\n" + body + "\n")

    w = max(len(r[2]) for r in rows) + 2
    print(f"{'章号':<6}{'章节名':<{w}}{'汉字':>7}{'含标点':>9}")
    print("-" * (13 + w))
    for n, title, name, cjk, allc in rows:
        print(f"{n:<6}{name:<{w}}{cjk:>7}{allc:>9}")
    print("-" * (13 + w))
    print(f"{'合计':<6}{'':<{w}}{sum(r[3] for r in rows):>7}"
          f"{sum(r[4] for r in rows):>9}")
    if a.out and not a.list:
        print(f"\n输出目录：{a.out}  共 {len(rows)} 章")
    return 0


if __name__ == "__main__":
    sys.exit(main())
