#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quality_check.py 的反向验证脚手架（改动判定逻辑后**必须先跑这个**）。

为什么需要它：判定器一旦写错，很容易造出「永远 PASS 的体检脚本」——
**比没有门禁更糟**，因为它会替作者盖章。正向看绿灯不能证明任何事，
必须能证明它在该报红的时候**真的会红**。

四条样本，每条只验一件事：

| 样本 | 构造 | 必须发生 |
|------|------|----------|
| A 坏样本 | 12 章全「压」+ 章末全软钩 | 报出连续无收益、开局窗口、软钩占比 |
| B 好样本 | 收益节拍稳定 + 硬软钩交替 | 全绿（阈值不得误伤健康稿） |
| C 无标签 | 章节在、`emotionTag` 缺失 | 情绪类 P0 降级为 P2（不拿词频推断报 P0） |
| D 豁免样本 | A + `waivers.json` | 命中的问题被移进「已豁免」区并打印 |

用法::

    python selftest_quality_check.py            # 用当前解释器
    python selftest_quality_check.py --keep     # 保留样本目录便于人工检查

退出码: 0 = 四项全过；1 = 有未通过项
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "quality_check.py"

# 章末片段：末段 ≤30 字 → 短句留白；末段以对白收尾 → 对白悬停
SOFT_TAIL = "他没有动。\n灯还亮着。"
HARD_TAIL = "原来那把锁上刻的，跟他手心的字一模一样。"
DIALOG_TAIL = "“先别问。”那人说，“明天早上你就知道了，问也没用，反正你记不住。”"


def chapter_text(n: int, tail: str) -> str:
    mid = (
        f"这是第 {n} 章的正文。他走过那条巷子，把手里那张纸又看了一遍。"
        "纸上的字被抹过，抹得很平，别人看就是一小块没字的白。他看见的是字。"
        "他把纸折起来收进怀里，往钟楼的方向走。路上没有人，只有风把墙根的灰吹起来。"
        "他在钟楼底下站了一会儿，数着台阶，一级一级数上去，数到第三十六级停住。"
        "门是关着的，门框上四个坑，三个有印，第四个积着细灰。"
        "他没有推门，转身往回走。走到一半，他停下来了。"
    )
    return mid + "\n" + tail


def write_chapters(proj: Path, tails: list[str]) -> None:
    (proj / "04-正文").mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(tails, 1):
        (proj / "04-正文" / f"第{i}章.md").write_text(
            f"# 第{i}章　测试\n\n{chapter_text(i, t)}\n", encoding="utf-8"
        )


def write_plan(proj: Path, tags: list[str] | None) -> None:
    (proj / "05-细纲").mkdir(parents=True, exist_ok=True)
    chapters = [{"chapterNo": i, "title": f"测试{i}", "emotionTag": t}
                for i, t in enumerate(tags or [], 1)]
    (proj / "05-细纲" / "计划.json").write_text(
        json.dumps({"chapters": chapters}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build(root: Path) -> dict[str, Path]:
    cases_dir = root / "cases"
    if cases_dir.exists():
        shutil.rmtree(cases_dir)
    cases: dict[str, Path] = {}

    a = cases_dir / "A-bad"
    write_chapters(a, [SOFT_TAIL] * 12)
    write_plan(a, ["压"] * 12)
    cases["A-bad"] = a

    b = cases_dir / "B-good"
    write_chapters(b, [HARD_TAIL, DIALOG_TAIL, HARD_TAIL, SOFT_TAIL] * 3)
    write_plan(b, ["爽", "秘辛", "压", "燃", "秘辛", "爽", "压", "秘辛",
                   "燃", "爽", "压", "燃"])
    cases["B-good"] = b

    c = cases_dir / "C-notag"
    write_chapters(c, [SOFT_TAIL] * 12)
    (c / "05-细纲").mkdir(parents=True, exist_ok=True)
    (c / "05-细纲" / "计划.json").write_text(
        json.dumps({"chapters": [{"chapterNo": i, "title": f"测试{i}"}
                                 for i in range(1, 13)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    cases["C-notag"] = c

    d = cases_dir / "D-waived"
    shutil.copytree(a, d)
    (d / "waivers.json").write_text(json.dumps({
        "waivers": [{"check": "emotion", "chapters": [1, 12],
                     "reason": "反例样本：模拟「既有手稿前段不修改」的豁免",
                     "approvedAt": "2026-10-08"}]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    cases["D-waived"] = d

    return cases


EXPECT: dict[str, dict] = {
    "A-bad": {
        "p0_min": 5,
        "p0_has": ["[emotion]", "[hook]"],
        "must": ["第 1-12 章连续 12 章", "前 3 章兑现 0 次", "前 10 章兑现 0 次",
                 "软钩占比 100%"],
    },
    "B-good": {"p0_max": 0, "expect_pass": True},
    "C-notag": {"p0_max": 1, "p0_lacks": ["[emotion]"],
                "must": ["emotionTag", "降级为提示"]},
    "D-waived": {"p0_min": 1, "p0_lacks": ["[emotion]"],
                 "must": ["已豁免（4 项", "豁免理由"]},
}


def p0_section(out: str) -> str:
    """只截 P0 区块，避免 P1/P2 里的同名字样干扰断言。"""
    buf, on = [], False
    for ln in out.splitlines():
        if ln.startswith("── P0"):
            on = True
            continue
        if on and ln.startswith("── "):
            break
        if on:
            buf.append(ln)
    return "\n".join(buf)


def main() -> int:
    keep = "--keep" in sys.argv
    python = sys.executable
    root = Path(tempfile.mkdtemp(prefix="wb-qc-selftest-"))
    try:
        cases = build(root)
        ok = True
        for name, proj in cases.items():
            r = subprocess.run([python, str(TARGET), str(proj)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            out = r.stdout
            exp = EXPECT[name]
            p0sec = p0_section(out)
            summary = next((ln for ln in out.splitlines() if ln.startswith("汇总：")),
                           "（无汇总行）")
            p0n = int(summary.split("P0 ")[1].split(" ")[0]) if "P0 " in summary else -1

            checks = [("P0 下限", p0n >= exp.get("p0_min", 0))]
            if "p0_max" in exp:
                checks.append((f"P0 上限 {exp['p0_max']}", p0n <= exp["p0_max"]))
            for kw in exp.get("p0_has", []):
                checks.append((f"P0 含「{kw}」", kw in p0sec))
            for kw in exp.get("p0_lacks", []):
                checks.append((f"P0 不含「{kw}」", kw not in p0sec))
            for kw in exp.get("must", []):
                checks.append((f"含「{kw}」", kw in out))
            if exp.get("expect_pass"):
                checks.append(("打了 PASS", "PASS" in out))

            good = all(v for _, v in checks)
            ok &= good
            print(f"[{'OK ' if good else 'FAIL'}] {name:9s} {summary}")
            for label, v in checks:
                if not v:
                    print(f"        ✗ {label}")
            if not good:
                print("        " + out.strip().replace("\n", "\n        ")[:1200])
            print()

        print("反向验证：" + ("四项全过 ✅" if ok else "存在未通过项 ❌"))
        if keep or not ok:
            print(f"样本目录：{root}")
        return 0 if ok else 1
    finally:
        if not keep and ok:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
