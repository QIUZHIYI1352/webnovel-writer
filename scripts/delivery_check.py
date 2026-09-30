# -*- coding: utf-8 -*-
"""交付终检：分章成品 vs 源文件一致性 + 标点纯净度 + 引号配平 + 上架门槛

投稿前最后一道门。逐章写的时候各脚本都跑过，但**导出成成品文件**是另一条链路，
必须单独验一遍：成品是不是真的等于源文件、标点是不是真的干净。

⚠️ 关键约束：本脚本对源文件的归一化必须与 `split_chapters.py` **完全一致**。
实测教训：终检脚本只去 `**` 而生成脚本去所有 `*`，会把**正确的成品判成「不一致 ❌」**，
于是人去改本来没问题的东西——这种「假失败」比漏检更浪费。

用法：
    python delivery_check.py --src 04-正文 --out 09-分章上架 \
        [--merge 06-全书合并稿.md]

退出码：0 = 全部通过，1 = 有问题
"""
import argparse
import io
import os
import re
import sys

BAD = {
    "半角逗号 ,": ",",
    "半角问号 ?": "?",
    "半角叹号 !": "!",
    "半角冒号 :": ":",
    "半角分号 ;": ";",
    "ASCII 直引号 \"": '"',
    "直角引号左 「": "\u300c",
    "直角引号右 」": "\u300d",
    "Markdown 星号 *": "*",
}


def strip_md(t):
    """与 split_chapters.clean_body 保持一致：星号一次清干净"""
    t = re.sub(r"\*+", "", t)
    t = t.replace("__", "")
    return re.sub(r"\s", "", t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="逐章 md 源目录")
    ap.add_argument("--out", required=True, help="分章成品目录")
    ap.add_argument("--merge", default=None, help="合并稿（可选，Markdown 存档件）")
    ap.add_argument("--first-day", type=int, default=4,
                    help="首日发几章（默认 4，用于粗算 6000 门槛）")
    ap.add_argument("--threshold", type=int, default=6000,
                    help="平台签约字数门槛（番茄完本短故事默认 6000）")
    a = ap.parse_args()

    CH = [f for f in sorted(os.listdir(a.out),
                            key=lambda x: int(re.search(r"\d+", x).group()))
          if f.startswith("第") and f.endswith(".txt") and re.search(r"\d+", f)]
    if not CH:
        raise SystemExit(f"错误：{a.out} 里找不到「第N章-*.txt」成品文件")

    def read_out(f):
        return io.open(os.path.join(a.out, f), encoding="utf-8-sig").read()

    def norm_src(n):
        # 源目录里找含该章号的 md
        cand = [f for f in os.listdir(a.src)
                if f.endswith(".md") and re.search(r"\d+", f)
                and int(re.search(r"\d+", f).group()) == n]
        if not cand:
            return None
        raw = io.open(os.path.join(a.src, cand[0]), encoding="utf-8").read()
        m = re.match(r"^#\s*.+?\n(.*)$", raw, re.S)
        body = m.group(1) if m else raw
        return strip_md("\n".join(l.rstrip() for l in body.split("\n")))

    fail = []

    # ── 1. 一致性 ────────────────────────────────────────────
    print("【1】成品 vs 源文件：第 1 行 = 章节名，正文逐字一致")
    print("-" * 58)
    tot_cjk = tot_all = 0
    for f in CH:
        n = int(re.search(r"\d+", f).group())
        txt = read_out(f)
        parts = txt.split("\n", 2)
        chap_name = parts[0]
        body = parts[2] if len(parts) > 2 else ""
        src = norm_src(n)
        same = (src is not None and strip_md(body) == src)
        cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
        allc = len(re.sub(r"\s", "", body))
        tot_cjk += cjk
        tot_all += allc
        if not same:
            fail.append(f"{f} 与源文件不一致" if src else f"{f} 找不到对应源文件")
        print(f"  {f:<24} {chap_name:<12} 汉字{cjk:>5} 含标点{allc:>5}"
              f"  {'✅' if same else '❌'}")
    print(f"  {'合计':<24} {'':<12} 汉字{tot_cjk:>5} 含标点{tot_all:>5}")

    # ── 2. 标点纯净度 ────────────────────────────────────────
    print()
    print("【2】标点纯净度")
    print("-" * 58)
    bad2 = []
    for f in CH:
        t = read_out(f)
        hits = {k: t.count(v) for k, v in BAD.items() if t.count(v)}
        if hits:
            bad2.append(f)
            print(f"  ❌ {f}: " + "、".join(f"{k}={v}" for k, v in hits.items()))
    # 合并稿是 Markdown 存档件：豁免星号，只看标点
    mbody = ""
    if a.merge and os.path.exists(a.merge):
        m = io.open(a.merge, encoding="utf-8").read()
        idx = m.find("# 第一章")
        mbody = m[idx:] if idx >= 0 else m
        mhits = {k: mbody.count(v) for k, v in BAD.items()
                 if v != "*" and mbody.count(v)}
        if mhits:
            bad2.append("合并稿")
            print(f"  ❌ 合并稿正文: " + "、".join(f"{k}={v}" for k, v in mhits.items()))
    if not bad2:
        extra = " + 合并稿正文" if mbody else ""
        print(f"  全部干净 ✅（{len(CH)} 个成品文件{extra}）")
    fail += bad2

    # ── 3. 引号配平 ──────────────────────────────────────────
    print()
    print("【3】引号配平")
    print("-" * 58)
    for label, text in [("全部分章成品", "".join(read_out(f) for f in CH)),
                        ("合并稿正文", mbody)]:
        if not text:
            continue
        lq, rq = text.count("\u201c"), text.count("\u201d")
        ok = lq == rq
        if not ok:
            fail.append(f"{label} 引号不配平")
        print(f"  {label:<14} “={lq:<4} ”={rq:<4} {'配平 ✅' if ok else '不配平 ❌'}")

    # ── 4. 上架门槛 ──────────────────────────────────────────
    print()
    print(f"【4】上架门槛（满 {a.threshold} 字开放签约入口）")
    print("-" * 58)
    sizes = [len(re.sub(r"\s", "", read_out(f).split("\n", 2)[2])) for f in CH]
    cum = sum(sizes[:a.first_day])
    flag = "✅ 越线" if cum >= a.threshold else f"❌ 差 {a.threshold - cum} 字，门槛未开"
    print(f"  首日发前 {a.first_day} 章 = {cum} 字  {flag}")
    print(f"  全篇 {len(CH)} 章 = {sum(sizes)} 字")

    # ── 5. 结论 ──────────────────────────────────────────────
    print()
    print("【5】结论")
    print("-" * 58)
    if fail:
        print("  ❌ 存在问题：" + "；".join(fail))
        return 1
    print("  ✅ 成品与源文件一致、标点纯净、引号配平，可交付")
    print(f"  正文合计：汉字 {tot_cjk} / 含标点 {tot_all}"
          "（口径：去空白、不含章节名）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
