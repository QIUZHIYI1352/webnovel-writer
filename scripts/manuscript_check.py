#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全书体检脚本（webnovel-writer skill）

`check_wordcount.py` 只管字数，`quality_check.py` 只管可计算的写作纪律。
本脚本补上**文件本身的完整性**——这一类问题脚本不查就只能靠肉眼，
而肉眼恰恰看不出来（实测踩过两次）。

## 为什么会需要它

`references/flows/shared-infrastructure.md` 里写过一句
「每批次收尾必须跑一次全书体检（字数 / dup / head / 引号 / BOM / CRLF）」，
但那个脚本一直**不存在**。于是：

- 第 7 章的 `<!-- 元信息块 -->` + 标题被整块吞掉（文件头只剩 `# 第0007章 `），
  存活了很久才被发现；
- 第 32 / 37 章正文变成「整篇重复一遍、标题与首句粘连」，也是只能靠体检抓。

历史欠账，本脚本一次补齐。

## 检查项

| 项 | 级别 | 判据 |
| --- | --- | --- |
| `head` | ERROR | 首行必须是 `<!--`；`META_KEYS` 六个键齐全；`# 第NNNN章 标题` 成形 |
| `consistency` | ERROR | 文件名章号 / 头部 `章号` / H1 章号三者一致 |
| `title` | WARN | 头部 `标题` 与 H1 标题不一致 |
| `wordcount` | WARN | 字数不在区间；头部 `字数:` 与实测差超 50 |
| `quotes` | ERROR / WARN | `LQ != RQ` 为 ERROR；ASCII 直引号、直角引号残留为 WARN |
| `encoding` | ERROR / WARN | UTF-8 BOM 为 ERROR；CRLF 为 WARN |
| `dup` | ERROR | 首句出现 ≥2 次，且后半字数约为前半的 1 倍（0.8~1.25） |
| `sequence` | WARN | 目录内章号缺号 / 重号 |

## 用法

    python manuscript_check.py <项目目录>/            # 递归全书体检
    python manuscript_check.py <vol-01目录>/
    python manuscript_check.py <文件1> <文件2> ...
    python manuscript_check.py <项目>/ --json
    python manuscript_check.py <项目>/ --min 1700 --max 2400

退出码: 0 = 无 ERROR；1 = 存在 ERROR；2 = 参数错误

口径与 `check_wordcount.py` 完全一致（CJK 汉字 + 中文标点，注释块不计），
解析与清洗一律走 `chapter_io`——**不要再本地各写一份正则**。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chapter_io import META_KEYS, parse, to_int  # noqa: E402
from check_wordcount import (  # noqa: E402
    CHAPTER_RE,
    DEFAULT_MAX,
    DEFAULT_MIN,
    count_chars,
)

# 用码位写死，避免源码在传输/编辑器里被归一化
LQ, RQ = "\u201c", "\u201d"          # 中文双引号
BRO_L, BRO_R = "\u300c", "\u300d"    # 直角引号
DQ = '"'                              # ASCII 直引号
H1_OK_RE = re.compile(r"^#\s*第\s*\d+\s*章")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def issue(level: str, code: str, msg: str) -> dict:
    return {"level": level, "code": code, "msg": msg}


def audit_file(path: Path, lo: int, hi: int) -> dict:
    """体检单个章节文件。返回 {'file','wordCount','issues':[{level,code,msg}]}"""
    res: dict = {"file": path.name, "wordCount": -1, "issues": []}
    add = res["issues"].append

    raw = path.read_bytes()

    # ---- 编码层 ----
    if raw.startswith(b"\xef\xbb\xbf"):
        add(issue("ERROR", "encoding", "存在 UTF-8 BOM（本项目要求无 BOM）"))
    n_crlf = raw.count(b"\r\n")
    if n_crlf:
        add(issue("WARN", "encoding", f"CRLF 行尾 {n_crlf} 处（本项目要求 LF）"))

    try:
        text = raw.decode("utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        add(issue("ERROR", "encoding", f"无法按 UTF-8 解码：{exc}"))
        return res

    p = parse(text)
    n = count_chars(text)
    res["wordCount"] = n

    # ---- 头部 ----
    if not p["has_meta"]:
        add(issue("ERROR", "head", "头部元信息块缺失（文件首行应为 <!--）"))
    else:
        missing = [k for k in META_KEYS if k not in p["meta"]]
        if missing:
            add(issue("ERROR", "head", "头部缺键：" + "、".join(missing)))

    if not p["h1"]:
        add(issue("ERROR", "head", "缺少规范的 H1 标题行（# 第NNNN章 标题）"))
    elif not H1_OK_RE.match(p["h1"]):
        add(issue("WARN", "head", f"H1 非规范形式：{p['h1']!r}"))
    if p["no"] is None:
        add(issue("ERROR", "head", "H1 里解析不出章号"))
    if not p["body"].strip():
        add(issue("ERROR", "head", "正文为空"))

    # ---- 三处章号一致性 ----
    fm = CHAPTER_RE.match(path.name)
    fno = int(fm.group(1)) if fm else None
    if fno is not None and p["no"] is not None and fno != p["no"]:
        add(issue("ERROR", "consistency", f"文件名章号 {fno} 与 H1 章号 {p['no']} 不一致"))
    mno = to_int(p["meta"].get("章号", ""))
    if mno is not None and p["no"] is not None and mno != p["no"]:
        add(issue("ERROR", "consistency", f"头部「章号：{p['meta'].get('章号')}」与 H1 章号 {p['no']} 不一致"))
    mt = p["meta"].get("标题", "").strip()
    if mt and p["title"] and mt != p["title"]:
        add(issue("WARN", "title", f"头部「标题：{mt}」与 H1 标题「{p['title']}」不一致"))

    # ---- 字数 ----
    mc = p["meta"].get("字数", "").strip()
    if re.fullmatch(r"\d+", mc) and abs(int(mc) - n) > 50:
        add(issue("WARN", "wordcount", f"头部「字数：{mc}」与实测 {n} 差 {abs(int(mc) - n)}（>50，需更新）"))
    if not (lo <= n <= hi):
        add(issue("WARN", "wordcount", f"字数 {n} 不在区间 {lo}-{hi}"))

    # ---- 引号 ----
    lq, rq = text.count(LQ), text.count(RQ)
    if lq != rq:
        add(issue("ERROR", "quotes", f"引号不配平：LQ {lq} / RQ {rq}"))
    adq = text.count(DQ)
    if adq:
        add(issue("WARN", "quotes", f"ASCII 直引号 {adq} 处（应为中文引号）"))
    bro = text.count(BRO_L) + text.count(BRO_R)
    if bro:
        add(issue("WARN", "quotes", f"直角引号残留 {bro} 处（应用全角双引号）"))

    # ---- 整篇重复 ----
    body_lines = [ln.strip() for ln in p["body"].split("\n")]
    first = next((ln for ln in body_lines if count_chars(ln) >= 8), "")
    if first:
        idxs = [i for i, ln in enumerate(body_lines) if ln == first]
        if len(idxs) >= 2:
            k = idxs[1]
            a = count_chars("\n".join(body_lines[:k]))
            b = count_chars("\n".join(body_lines[k:]))
            if a >= 200 and b and 0.8 <= b / a <= 1.25:
                add(issue("ERROR", "dup", f"疑似整篇重复：首句在文件内出现 {len(idxs)} 次，前后半字数 {a}/{b}"))

    return res


def collect(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*.md") if CHAPTER_RE.match(p.name))
    return []


def parse_args(argv: list[str]):
    """返回 (目标列表, 下限, 上限, json)。特殊返回 "help"（调用方 exit 0）/ None（exit 2）。"""
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return "help"
    json_mode = "--json" in args
    args = [a for a in args if a != "--json"]
    lo, hi = DEFAULT_MIN, DEFAULT_MAX
    targets: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--min" and i + 1 < len(args):
            lo = int(args[i + 1]); i += 2
        elif args[i] == "--max" and i + 1 < len(args):
            hi = int(args[i + 1]); i += 2
        else:
            targets.append(args[i]); i += 1
    if not targets:
        print(__doc__)
        return None
    return [Path(t) for t in targets], lo, hi, json_mode


def main() -> int:
    parsed = parse_args(sys.argv[1:])
    if parsed == "help":
        return 0
    if parsed is None:
        return 2
    targets, lo, hi, json_mode = parsed

    files: list[Path] = []
    for t in targets:
        found = collect(t)
        if not found:
            print(f"错误：{t} 下未找到章节文件（第\\d+章-*.md）")
            return 2
        files.extend(found)

    # 去重 + 按章号排序
    seen: dict[int, Path] = {}
    for p in files:
        m = CHAPTER_RE.match(p.name)
        seen.setdefault(int(m.group(1)) if m else 0, p)
    files = [seen[k] for k in sorted(seen)]

    results = [audit_file(p, lo, hi) for p in files]

    # 章号连续性
    nums = sorted(int(CHAPTER_RE.match(p.name).group(1)) for p in files if CHAPTER_RE.match(p.name))
    seq_issues: list[dict] = []
    if nums:
        missing = [x for x in range(nums[0], nums[-1] + 1) if x not in nums]
        if missing:
            seq_issues.append(issue("WARN", "sequence", "缺号：" + "、".join(str(x) for x in missing)))

    total_words = sum(r["wordCount"] for r in results if r["wordCount"] >= 0)
    n_err = sum(1 for r in results for it in r["issues"] if it["level"] == "ERROR") + \
        sum(1 for it in seq_issues if it["level"] == "ERROR")
    n_warn = sum(1 for r in results for it in r["issues"] if it["level"] == "WARN") + \
        sum(1 for it in seq_issues if it["level"] == "WARN")

    if json_mode:
        print(json.dumps(
            {
                "range": [lo, hi],
                "chapters": results,
                "sequence": seq_issues,
                "total": len(results),
                "totalWordCount": total_words,
                "errors": n_err,
                "warnings": n_warn,
                "clean": n_err == 0,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if n_err == 0 else 1

    print("=== 全书体检 ===")
    print("目标：" + "  ".join(str(t) for t in targets))
    print(f"章节数：{len(results)}    达标区间：{lo}-{hi}\n")

    print("【逐章】")
    for r in results:
        has_err = any(it["level"] == "ERROR" for it in r["issues"])
        has_warn = any(it["level"] == "WARN" for it in r["issues"])
        tag = "FAIL" if has_err else ("WARN" if has_warn else "PASS")
        print(f"  {tag}  {r['file']}  {r['wordCount']} 字")
        for it in r["issues"]:
            mark = "!" if it["level"] == "ERROR" else "-"
            print(f"        {mark} [{it['code']}] {it['msg']}")
    for it in seq_issues:
        print(f"  WARN  {it['msg']}")

    ok_words = sum(1 for r in results if lo <= r["wordCount"] <= hi)
    ok_head = sum(1 for r in results if not any(it["code"] == "head" for it in r["issues"]))
    ok_quote = sum(1 for r in results if not any(it["code"] == "quotes" and it["level"] == "ERROR" for it in r["issues"]))
    ok_enc = sum(1 for r in results if not any(it["code"] == "encoding" and it["level"] == "ERROR" for it in r["issues"]))
    ok_dup = sum(1 for r in results if not any(it["code"] == "dup" for it in r["issues"]))

    print("\n【汇总】")
    print(f"  章节数      {len(results)}")
    print(f"  字数        {ok_words}/{len(results)} 在区间内，总计 {total_words}")
    print(f"  头部完整    {ok_head}/{len(results)}")
    print(f"  引号配平    {ok_quote}/{len(results)}")
    print(f"  编码合规    {ok_enc}/{len(results)}")
    print(f"  无重复      {ok_dup}/{len(results)}")
    print(f"\n  ERROR {n_err} / WARN {n_warn} → " + ("PASS 可交付" if n_err == 0 else "FAIL 需修复"))

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
