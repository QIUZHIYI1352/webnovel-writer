# -*- coding: utf-8 -*-
"""把正文里的 ASCII 直引号按行交替转成中文全角引号 U+201C / U+201D。

背景：用 Write 工具写 .md 正文时，中文引号可能被归一化成 0x22。
本脚本是修复手段。**每行的直引号数必须为偶数**（开闭成对），否则报错退出。

⚠️ `--check` 报的数量与真正执行时**完全一致**（同一个 `process` 走一遍），
不要另写一套"原始计数"——`fix_punct.py` 就踩过这个坑：
`--check` 报原始半角逗号数、`fix` 报实际替换数，两边数字不同，人会以为脚本漏改了。

用法：
    python fix_quotes.py <文件1> [文件2 ...]
    python fix_quotes.py --check <文件1> [文件2 ...]   # 只报告，不写入
"""
import io
import sys

LQ = "\u201c"
RQ = "\u201d"


def convert_line(ln):
    """把一行里的直引号按出现顺序交替转成 “ 与 ”。调用方保证个数为偶数。"""
    buf = []
    opening = True
    for ch in ln:
        if ch == '"':
            buf.append(LQ if opening else RQ)
            opening = not opening
        else:
            buf.append(ch)
    return "".join(buf)


def process(path, write=True):
    """返回该文件里的直引号总数。write=False 时只报告不落盘。"""
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
        out.append(convert_line(ln))
    if bad:
        for i, ln in bad:
            print("ODD line %d: %s" % (i, ln))
        raise SystemExit("FAILED: odd quote count, nothing written")
    n_dq = t.count('"')
    if write and n_dq:
        io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    return n_dq


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    check_only = False
    if argv[0] == "--check":
        check_only = True
        argv = argv[1:]
    if not argv:
        print(__doc__)
        return 2
    for p in argv:
        n = process(p, write=not check_only)
        print("%s %s (%d quotes)" % ("would_fix" if check_only else "fixed", p, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
