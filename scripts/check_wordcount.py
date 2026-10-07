#!/usr/bin/env python3
"""章节字数检查脚本（webnovel-writer skill）

统计口径：CJK 汉字 + 中文标点（网文平台计字口径）。HTML 注释（章节元信息头）不计入。

用法:
    python check_wordcount.py <章节文件.md>                  # 单章，默认达标区间 1700-2400
    python check_wordcount.py <文件1> <文件2> [...]           # 多章（多传的文件不再被忽略）
    python check_wordcount.py --all <目录>/                  # 递归检查目录下全部章节
    python check_wordcount.py <目录>/                         # 直接传目录亦可
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: 字数口径的**唯一实现**在 chapter_io —— 不要在本文件里再写一份 CJK / CN_PUNCT 正则。
#: 实测教训：split_chapters 曾用"全文所有非空白字符"另算一套，同一批 12 章
#: 与本脚本差 115 字（22449 vs 22334），全项目到处对不上账。
from chapter_io import count_chars  # noqa: E402

CHAPTER_RE = re.compile(r"^第(\d+)章.*\.md$")
META_WORDCOUNT = re.compile(r"字数[:：]\s*(\d+)")

DEFAULT_MIN = 1700
DEFAULT_MAX = 2400

# Windows 控制台默认 GBK，输出中文前统一 stdout 编码，避免中文路径/提示乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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


def parse_args(argv: list[str]) -> tuple[list[Path], int, int, bool, bool] | None | str:
    """返回 (待检查路径列表, 下限, 上限, all_mode, json_mode)。

    特殊返回：``"help"`` = 用户要帮助（调用方应 exit 0）；``None`` = 参数错误（exit 2）。

    ⚠️ 早期版本只取 `positional[0]`，**多传的文件会被静默忽略**——
    `check_wordcount.py a.md b.md` 只报 a.md，看起来"全部通过"，实际 b.md 根本没查。
    那是个会骗人的坑，现已在下面显式分流：非纯数字的额外参数一律当成待检查路径。
    """
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return "help"

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

    rest = positional[1:]
    nums = [a for a in rest if re.fullmatch(r"\d+", a)]
    extra = [a for a in rest if not re.fullmatch(r"\d+", a)]

    if len(nums) >= 2:
        # 兼容旧写法：check_wordcount.py <文件> 1600 2600
        lo, hi = int(nums[0]), int(nums[1])

    targets = [Path(positional[0])] + [Path(a) for a in extra]
    return targets, lo, hi, all_mode, json_mode


def main() -> int:
    parsed = parse_args(sys.argv[1:])
    if parsed == "help":
        return 0
    if parsed is None:
        return 2
    targets, lo, hi, all_mode, json_mode = parsed

    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            found = collect_files(target)
            if not found:
                print(f"错误：{target} 下未找到章节文件（第\\d+章-*.md）")
                return 2
            files.extend(found)
        elif target.is_file():
            files.append(target)
        else:
            print(f"错误：{target} 不存在（批量检查目录请直接传目录，或用 --all）")
            return 2

    # 目录批量扫描时按章号排序；显式传入的文件保持传入顺序
    if all_mode or any(t.is_dir() for t in targets):
        seen: dict[int, Path] = {}
        for p in files:
            m = CHAPTER_RE.match(p.name)
            n = int(m.group(1)) if m else 0
            seen.setdefault(n, p)
        files = [seen[k] for k in sorted(seen)]

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
