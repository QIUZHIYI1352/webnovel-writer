# -*- coding: utf-8 -*-
"""中文标点归一化：把被工具归一化的半角/替代标点修回中文全角。

背景：用 Write 工具写 .md 正文时，会被宿主做一次归一化：
  - 中文全角逗号 `，` -> 半角 `,`
  - 中文引号 `“”` -> 直角引号 `「」`
  - 中文引号 `“”` -> 半角 ASCII `"`（部分语境，如 `那个"晦气"远一点`）
  - 中文顿号 `、` 通常保留
本脚本把这些还原成网文平台要求的全角标点。

规则（只在中文字符语境下替换，避免误伤代码/英文）：
  1. 逗号：半角 `,` 前后只要有一侧是中日韩字符或全角标点，即替换为 `，`
  2. 直角引号 `「` `」` -> `“` `”`（按出现顺序交替配对）
  2b. ASCII 双引号 `"` 在中文语境下 -> `“` `”`（跟随全篇引号开合状态）
  3. 半角句号 `.` 夹在中文之间 -> `。`（保守：前后都是中文字符才替换）

用法：
    python fix_punct.py <文件1> [文件2 ...]
    python fix_punct.py --check <文件>      # 只报告，不写入
"""
import io
import re
import sys

CJK = r"\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
LQ = "\u201c"
RQ = "\u201d"


def fix(text):
    n_comma = 0
    n_quote = 0
    n_dot = 0

    # 1. 半角逗号 -> 全角（任一侧是中文语境）
    def comma_sub(m):
        nonlocal n_comma
        n_comma += 1
        return "\uff0c"

    text = re.sub(r"(?<=[%s]),|,(?=[%s])" % (CJK, CJK), comma_sub, text)
    # 剩下的裸露半角逗号夹在数字/英文之间不动

    # 1b. 半角问号 / 叹号 / 冒号 / 分号 -> 全角（任一侧是中文语境）
    punct_map = {
        "?": "\uff1f",
        "!": "\uff01",
        ":": "\uff1a",
        ";": "\uff1b",
    }
    for half, full in punct_map.items():
        text = re.sub(
            r"(?<=[%s])\%s|\%s(?=[%s])" % (CJK, half, half, CJK),
            full,
            text,
        )

    # 2. 直角引号 + ASCII 双引号 -> 中文引号，跟随全篇开合状态
    #    同时跟踪已有的 “ ”，保证 ASCII 引号接在正确的一侧
    def in_cjk(s, i, span=3):
        lo, hi = max(0, i - span), min(len(s), i + span + 1)
        return re.search(r"[%s]" % CJK, s[lo:hi]) is not None

    out = []
    opening = True
    for i, ch in enumerate(text):
        if ch == "\u300c":            # 「
            out.append(LQ if opening else RQ)
            opening = not opening
            n_quote += 1
        elif ch == "\u300d":          # 」
            out.append(RQ if not opening else LQ)
            if opening:
                out.append(RQ)
            opening = True
            n_quote += 1
        elif ch == '"' and in_cjk(text, i):
            out.append(LQ if opening else RQ)
            opening = not opening
            n_quote += 1
        elif ch == LQ:
            out.append(ch)
            opening = False
        elif ch == RQ:
            out.append(ch)
            opening = True
        else:
            out.append(ch)
    text = "".join(out)

    # 3. 中文之间的半角句号 -> 全角
    def dot_sub(m):
        nonlocal n_dot
        n_dot += 1
        return "\u3002"

    text = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=[\u4e00-\u9fff])", dot_sub, text)

    return text, n_comma, n_quote, n_dot


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    check_only = False
    if args[0] == "--check":
        check_only = True
        args = args[1:]
    if not args:
        print(__doc__)
        return 2

    for p in args:
        t = io.open(p, encoding="utf-8").read()
        fixed, nc, nq, nd = fix(t)
        if check_only:
            # ⚠️ 这里必须报**实际会被替换**的数量（fix 的返回值），
            # 不要报 `t.count(",")` 那种原始计数——两套口径会让人以为"check 说 5 处、
            # fix 只改了 2 处，是不是漏了"，实际是上下文规则本来就只改 2 处。
            print("%s: would_fix comma=%d quote=%d dot=%d"
                  " | raw halfwidth_comma=%d angle_quote=%d ascii_dquote=%d"
                  % (p, nc, nq, nd, t.count(","),
                     t.count("\u300c") + t.count("\u300d"), t.count('"')))
            continue
        if fixed != t:
            if not t.endswith("\n"):
                fixed += "\n"
            io.open(p, "w", encoding="utf-8", newline="\n").write(fixed)
        print("fixed %s (comma=%d quote=%d dot=%d)" % (p, nc, nq, nd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
