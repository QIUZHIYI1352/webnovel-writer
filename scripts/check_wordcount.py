#!/usr/bin/env python3
"""章节字数检查脚本（webnovel-writer skill）

统计口径：CJK 汉字 + 中文标点（网文平台计字口径）。HTML 注释（章节元信息头）不计入。

用法:
    python check_wordcount.py <章节文件.md>                  # 单章，默认达标区间 1700-2400
    python check_wordcount.py --all <目录>/                  # 递归检查目录下全部章节
    python check_wordcount.py <文件> 1600 2600               # 自定义达标区间
    python check_wordcount.py --all <目录>/ --min 1700 --max 2400
    python check_wordcount.py --all <目录>/ --json           # 机器可读输出

退出码: 0 = 全部通过；1 = 存在未通过章节或读取失败；2 = 参数错误
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
CN_PUNCT = re.compile(r"[\u3000-\u303f\uff00-\uffef\u2018\u2019\u201c\u201d\u2026\u2014\u00b7]")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
CHAPTER_RE = re.compile(r"^第\d+章.*\.md$")
META_WORDCOUNT = re.compile(r"字数[:：]\s*(\d+)")

DEFAULT_MIN = 1700
DEFAULT_MAX = 2400

# Windows 控制台默认 GBK，输出中文前统一 stdout 编码，避免中文路径/提示乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def count_chars(text: str) -> int:
    body = COMMENT.sub("", text)
    return len(CJK.findall(body)) + len(CN_PUNCT.findall(body))


def check_file(path: Path, lo: int, hi: int) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"file": path.name, "wordCount": -1, "pass": False, "reason": f"无法读取: {exc}"}

    n = count_chars(text)
    ok = lo <= n <= hi
    reason = ""
    if n < lo:
        reason = f"低于下限 {lo}，需扩充"
    elif n > hi:
        reason = f"高于上限 {hi}，需删水"

    meta = META_WORDCOUNT.search(text)
    if meta and abs(int(meta.group(1)) - n) > 50:
        stale = f"元信息字数({meta.group(1)})与实际({n})不符，请更新"
        reason = f"{reason}；{stale}" if reason else stale

    return {"file": path.name, "wordCount": n, "pass": ok, "reason": reason}


def collect_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*.md") if CHAPTER_RE.match(p.name))
    return []


def parse_args(argv: list[str]) -> tuple[Path, int, int, bool, bool] | None:
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return None

    all_mode = "--all" in args
    json_mode = "--json" in args
    args = [a for a in args if a not in ("--all", "--json")]

    lo, hi = DEFAULT_MIN, DEFAULT_MAX
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--min" and i + 1 < len(args):
            lo = int(args[i + 1])
            i += 2
        elif args[i] == "--max" and i + 1 < len(args):
            hi = int(args[i + 1])
            i += 2
        else:
            positional.append(args[i])
            i += 1

    if not positional:
        print(__doc__)
        return None

    nums = positional[1:]
    if len(nums) >= 2:
        lo, hi = int(nums[0]), int(nums[1])

    return Path(positional[0]), lo, hi, all_mode, json_mode


def main() -> int:
    parsed = parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    target, lo, hi, all_mode, json_mode = parsed

    if all_mode:
        files = collect_files(target)
        if not files:
            print(f"错误：{target} 下未找到章节文件（第\\d+章-*.md）")
            return 2
    else:
        if not target.is_file():
            print(f"错误：{target} 不是文件（如需批量检查请加 --all）")
            return 2
        files = [target]

    results = [check_file(p, lo, hi) for p in files]
    passed = sum(1 for r in results if r["pass"])
    total_words = sum(r["wordCount"] for r in results if r["wordCount"] >= 0)

    if json_mode:
        print(json.dumps(
            {
                "range": [lo, hi],
                "chapters": results,
                "total": len(results),
                "passed": passed,
                "failed": len(results) - passed,
                "totalWordCount": total_words,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        for r in results:
            tag = "PASS" if r["pass"] else "FAIL"
            line = f"{tag}  {r['file']}: {r['wordCount']} 字"
            if r["reason"]:
                line += f"（{r['reason']}）"
            print(line)
        print(f"\n共 {len(results)} 章，通过 {passed}，未通过 {len(results) - passed}，总字数 {total_words}"
              f"（达标区间 {lo}-{hi}）")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
