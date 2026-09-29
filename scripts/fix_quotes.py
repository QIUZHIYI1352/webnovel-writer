# -*- coding: utf-8 -*-
"""把正文里的 ASCII 直引号按行交替转成中文全角引号 U+201C / U+201D。

背景：用 Write 工具写 .md 正文时，中文引号可能被归一化成 0x22。
本脚本是修复手段。**每行的直引号数必须为偶数**（开闭成对），否则报错退出。

用法：
    python fix_quotes.py <文件1> [文件2 ...]
"""
import io
import sys

LQ = "\u201c"
RQ = "\u201d"


def fix(path):
    t = io.open(path, encoding="utf-8").read()
    out = []
    bad = []
    for i, ln in enumerate(t.split("\n"), 1):
        n = ln.count('"')
        if n == 0:
            out.append(ln)
            continue
        if n % 2:
            bad.append((i, ln))
            out.append(ln)
            continue
        buf = []
        opening = True
        for ch in ln:
            if ch == '"':
                buf.append(LQ if opening else RQ)
                opening = not opening
            else:
                buf.append(ch)
        out.append("".join(buf))
    if bad:
        for i, ln in bad:
            print("ODD line %d: %s" % (i, ln))
        raise SystemExit("FAILED: odd quote count, nothing written")
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    return t.count('"')


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python fix_quotes.py <file> [file ...]")
    for p in sys.argv[1:]:
        print("fixed %s (%d quotes)" % (p, fix(p)))
