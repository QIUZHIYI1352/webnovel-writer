# -*- coding: utf-8 -*-
"""把逐章 Markdown 正文转成可直接粘贴上架的纯文本分章文件。

网文平台的写作后台是富文本编辑器，粘 Markdown 会带出 `**` 星号；
章节名是独立输入框，不该混在正文里。本脚本一次解决这两个问题。

产出（每个章节一个 txt）：
    <out>/第NN章-<标题>.txt
      └ 第 1 行   = 章节名（粘到平台「章节名」输入框）
        空行以后 = 正文（已剥离 Markdown 标记与元信息块）

用法：
    python split_chapters.py --src 04-正文 --out 09-分章上架
    python split_chapters.py --src 04-正文 --out 09-分章上架 --keep-md
    python split_chapters.py --src 04-正文 --list      # 只打印清单不写文件

参数：
    --src   逐章 md 目录（文件名需含章节号，如 第1章.md / ch01.md）
    --out   输出目录
    --keep-md  保留 Markdown 加粗标记（默认剥离）
    --bom  以 UTF-8 BOM 写文件（Windows 记事本友好，默认开启）

⚠️ 章节文件若以 `<!-- 元信息块 -->` 开头（`chapter-template.md` 规定的形态），
   该块**必须整块剥掉、不能进成品**。早期版本用 `re.match(r"^#...")` 抓标题，
   而 `re.match` 只从文件最开头匹配 —— 于是永远匹配不上，整块注释被当成正文
   写进成品、`# 第N章 标题` 也一起漏出去、章节名只剩「第一章」。
   现在统一走 `chapter_io.parse()`，头部识别只有一个实现。
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_io import clean_body, cn_num, count_chars, count_cjk, parse  # noqa: E402  (同目录模块)


def parse_chapter(path):
    """从 md 文件取出 (章号, 章名标题, 正文, 全文)。

    章号优先取 H1 / 文件名里的数字；标题优先取元信息块的 `标题`，其次取 H1。
    """
    with open(path, encoding="utf-8-sig") as fp:
        raw = fp.read()
    p = parse(raw)
    n = p["no"]
    if n is None:
        m = re.search(r"\d+", os.path.basename(path))
        n = int(m.group()) if m else 0
    title = p["title"] or p["meta"].get("标题", "")
    return n, title, p["body"], raw


def counts(text):
    """返回 (汉字数, 含标点字符数)。

    ⚠️ **口径来自 `chapter_io`，不要在本文件里另算一套。**
    旧实现是 `len(re.sub(r"\\s", "", text))`（全文所有非空白字符），
    与 `check_wordcount.py` 的 CJK+中文标点口径差 115 字/12 章（22449 vs 22334），
    导致"同一本书三个总字数在各处打架"。现在两边调同一个函数，**永远相等**。

    传参是**章节文件全文**（含 `# 第N章 标题` 行）——与 `check_wordcount.py` 一致。
    """
    return count_cjk(text), count_chars(text)


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
        n, title, body, raw = parse_chapter(os.path.join(a.src, f))
        chap_name = f"第{cn_num(n)}章" + (f"\u3000{title}" if title else "")
        cjk, allc = counts(raw)          # 口径与 check_wordcount.py 一致（全文）
        body = clean_body(body, a.keep_md)
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
    print("口径：CJK 汉字 + 中文标点（含 `# 第N章 标题` 行，元信息块不计）"
          " —— 与 check_wordcount.py 相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
