#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量体检脚本（webnovel-writer skill）

把「情绪节奏 / 开局留存 / 钩子轮换与强度 / 爽点间距 / 设定越界 / AI 腔密度」
这些原本只能靠模型自评的质量维度，变成可计算、可复现、可趋势化的客观指标。

设计原则：
  1. 只做能确定性判定的检查。语义判断（这段话好不好）仍归模型，
     本脚本只回答「是否违反了已写死的纪律」。
  2. 所有阈值集中在 RULES，便于按书调参；判定结果带明确证据（章号/原句）。
  3. JSON 输出键名全 ASCII，供 Phase 4 校验与 CI 消费。
  4. **收益优先于压抑**：判定「读者拿到了什么」而不是「作者写了什么情绪」。
     见 `parse_emotion_tag` 与 `check_opening_retention` 的说明。

用法:
    python quality_check.py <项目目录>                      # 全量体检
    python quality_check.py <项目目录> --json                # 机器可读
    python quality_check.py <项目目录> --chapters 35-39      # 只查指定章段
    python quality_check.py <项目目录> --strict              # 有 P0 即退出码 1

豁免台账（可选）: 项目根或 `_engine/` 下放 `waivers.json`，形如
    {"waivers": [{"check": "emotion", "chapters": [1, 8],
                  "reason": "Phase 0 迁入的既有手稿，字数不达标不修改",
                  "approvedAt": "2026-09-16"}]}
命中的问题会被降级为 P2 并在「已豁免」区单独打印——**不是让门禁闭嘴，
而是让「带着已知缺陷上线」这件事每次都被看见**。

退出码: 0 = 无 P0；1 = 存在 P0 问题；2 = 参数错误
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- 规则阈值
RULES = {
    # 情绪节奏
    "max_continuous_pressure": 2,      # 连续「只有成本、没有收益」的上限（超出必须给收益）
    "max_gap_without_payoff": 8,       # 两次爽/燃兑现之间最多允许的空档章数
    # 开局留存窗口（番茄等平台的追读率主要看前 3 章与前 10 章）
    "opening_payoff_first3": 1,        # 前 3 章至少 N 次兑现
    "opening_payoff_first10": 2,       # 前 10 章至少 N 次兑现
    "opening_gain_first10": 3,         # 前 10 章至少 N 次「读者拿到了东西」（兑现+秘辛）
    # 钩子轮换与强度
    "max_same_hook_run": 2,            # 同一钩子形状最多连用章数（第 3 章即违规）
    "max_soft_hook_share": 0.60,       # 软钩（短句留白/对白悬停）占比上限
    # 代入感
    "max_gap_without_agency": 6,       # 主角连续无主动收益的上限
    # AI 腔
    "ai_cliche_per_1k": 6.0,           # 每千字 AI 腔高频词上限
    # 设定越界
    "allow_unknown_terms": 0,          # 允许的词典外专有名词数
}

PUNCT_R = "\u201d\u3002\uff01\uff1f"
CHAPTER_RE = re.compile(r"^第(\d+)章.*\.md$")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
CJK = re.compile(r"[\u4e00-\u9fff]")

# 情绪词库（按标签归一）
EMOTION_WORDS = {
    "爽": ["打脸", "赢了", "得手", "收下", "拿到", "扣平", "过稿", "成了", "松了", "挣"],
    "燃": ["燃", "拼", "冲", "护住", "一起", "并肩", "顶", "豁出去"],
    "泪": ["泪", "哭", "哽", "哑", "低头", "冰凉", "凉了", "没说出口"],
    "压": ["不能", "不敢", "来不及", "没了", "失去", "退", "关", "封", "断"],
}

# 情绪标签归一化用的关键词（不参与 parse_emotion_tag 的落点判定，见下）
TAG_PAYOFF = re.compile(r"[爽燃暖]")
TAG_REVEAL = re.compile(r"秘辛")
TAG_COST = re.compile(r"[压泪]")
# 作者用来表示「这一章其实给过甜头」的模糊批注。它们**不作数**——
# 见 parse_emotion_tag 的说明，`压(微爽:活下来)` 正是自我安慰的典型。
TAG_FUZZY = re.compile(r"[（(][^）)]*(爽|燃|兑现|得法|小胜|活下来|动了手)[^）)]*[）)]")

# 钩子强度分类。
# 软钩靠氛围与语气收尾——读完了但没得到新东西，也没有被逼着往下点；
# 硬钩给出新信息、逼出选择、划定期限或推进动作，是追读的主要驱动力。
# ⚠️「不连用」只能保证读者不腻，**保证不了读者想追**。只查连用不查强度，
#   就会出现「门禁全绿、全书 2/3 章末是软钩」这种账面健康、实际掉追读的局面。
HARD_HOOKS = {"信息炸", "抉择式", "倒计时式", "登场式", "动作悬停"}
SOFT_HOOKS = {"短句留白", "对白悬停"}


def parse_emotion_tag(tag: str) -> dict:
    """把自由文本情绪标签解析成可计算的三个标志位。

    返回 ``{"payoff": 兑现(爽/燃/暖), "reveal": 情报(秘辛), "cost": 成本(压/泪)}``。

    ⚠️ 这里踩过一个坑：早期实现直接写 ``"压" in tag and not ("爽" in tag ...)``，
    于是 ``压(微爽:活下来)`` 这种带括号批注的标签会让连续压制段被「微爽」
    两个字**切断**。实书《刻名》第 1-14 章本来是一整段零兑现，却被拆成
    1-6 与 8-11 两段上报，严重程度被腰斩，人也就更容易忽略。

    现在的规则：**括号里是批注，不参与判定**；``→`` 表示章内转折，只看箭头
    右边（那才是这一章真正的落点）。

    这条规则的代价是「微爽」也不算兑现——**是故意的**。《刻名》第 7 章原标签是
    ``压(微爽:活下来)``，可那一章的实情是：他用十二次换一条出路，代价是丢掉
    半生记忆，收益只是「能出门」。评审的原话是「**这是净亏损**」。
    批注里的「微爽」正是作者替自己找的台阶——机器不能替作者盖这个章。
    真给了收益就写 ``爽`` / ``燃`` / ``秘辛``，别写批注（`check_tag_hygiene` 会提醒）。
    """
    raw = (tag or "").strip()
    if not raw:
        return {"payoff": False, "reveal": False, "cost": False}

    base = re.sub(r"[（(][^）)]*[）)]", "", raw)          # 剥掉批注
    landing = base.split("→")[-1] if "→" in base else base   # 取落点

    return {
        "payoff": bool(TAG_PAYOFF.search(landing)),
        "reveal": bool(TAG_REVEAL.search(landing)),
        "cost": bool(TAG_COST.search(landing)),
    }


def check_tag_hygiene(chs: list[dict], tags: dict[int, str]) -> list[dict]:
    """标签卫生：批注式标签要转成结构化字段，否则统计口径会漂。

    不判作者对错，只做一件事——**把机器读不懂的标签点名出来**。
    这本书的留存判定全都建立在 ``emotionTag`` 上，标签含糊 = 判定含糊。
    """
    bad = []
    for c in chs:
        tag = tags.get(c["no"])
        if tag and TAG_FUZZY.search(tag):
            bad.append((c["no"], tag))
    if not bad:
        return []
    sample = "、".join(f"第{n}章「{t}」" for n, t in bad[:5])
    return [{
        "check": "emotion", "level": "P2",
        "chapter": bad[0][0],
        "evidence": f"{len(bad)} 章的标签带模糊批注，机器无法据此判定收益：{sample}",
        "rule": "emotionTag 必须能被机器解析（兑现用「爽/燃/暖」，情报用「秘辛」，"
                "成本用「压/泪」）；括号批注不参与判定",
        "action": "把这些章改成主标签 + 在细纲的收益列写清这一章到底给了什么；"
                  "若确实给了收益却仍标「压」，说明标签写错了，请改成「压→爽」或「爽」",
    }]

# 钩子形状判定器（顺序敏感，先判先中）
def hook_shape(tail: str) -> str:
    """按章末最后几段判定钩子形状。

    顺序即优先级：越具体的信号越先判。判定标准参考
    guides/emotion-and-pacing.md 的「钩子十二式」，归并成可计算的六类。
    """
    t = tail.strip()
    if not t:
        return "空"

    # 1) 倒计时 / 期限（最明确：必须有具体的期限词，不是凡含「响」都算）
    if re.search(r"(只剩|还有\s*[一二三四五六七八九十百\d]|倒计时|明天|今夜|天亮之前|三日|三天|三个月|"
                 r"第[二三四五六七八九十]声(钟|响))", t):
        return "倒计时式"

    # 2) 新角色 / 新势力压轴登场
    if re.search(r"(来了一个|有人叫他|提着一盏灯|背着.{0,4}包袱|站在.{0,8}(台阶|门口|阴影))", t):
        return "登场式"

    # 3) 信息炸：末句抛出反常信息（含"原来/其实/是同一/一样"等揭底词）
    if re.search(r"(原来|竟然是|一模一样|同一个人|同一支手|不是.{0,6}是)", t):
        return "信息炸"

    # 4) 抉择：主角面临两难后做出的决定
    if re.search(r"(他不知道|他决定|他选了|他挑|要不要|还是|宁可)", t):
        return "抉择式"

    # 5) 对白收尾：只看**最后一段**是否以对白结束（而非整个 tail）
    last_para = t.split("\n")[-1].strip()
    if last_para.endswith(PUNCT_R) or (last_para.count("\u201c") > 0 and last_para.count("\u201d") > 0):
        return "对白悬停"

    # 6) 动作悬停 vs 短句留白：按末段长度区分
    if len(last_para) <= 30:
        return "短句留白"
    return "动作悬停"


# AI 腔高频词（去 AI 味检查的确定性部分）
AI_CLICHE = [
    "涌起", "说不出的", "不由自主", "五味杂陈", "心中一凛", "若有所思",
    "深吸一口气", "缓缓", "静静", "微微", "淡淡地", "仿佛", "似乎",
    "不由自主地", "轻轻地", "深深地", "默默地",
]

# 四字格连用检测（一段内 ≥3 个四字成语）
IDIOM = re.compile(r"[\u4e00-\u9fff]{4}")


def count_chars(text: str) -> int:
    body = COMMENT.sub("", text)
    return len(CJK.findall(body))


def read_chapters(project: Path, chapters: set[int] | None = None) -> list[dict]:
    """递归扫描项目下所有章节文件，返回按章号排序的列表。"""
    found: dict[int, Path] = {}
    for p in project.rglob("*.md"):
        m = CHAPTER_RE.match(p.name)
        if not m:
            continue
        n = int(m.group(1))
        if chapters and n not in chapters:
            continue
        # 同名文件保留路径最短的（避免 _engine 备份干扰）
        if n not in found or len(str(p)) < len(str(found[n])):
            found[n] = p
    out = []
    for n in sorted(found):
        text = found[n].read_text(encoding="utf-8")
        paras = [
            ln.strip() for ln in text.split("\n")
            if ln.strip() and not ln.strip().startswith("#")
            and not ln.strip().startswith("<!--") and not ln.strip().startswith("-->")
        ]
        out.append({
            "no": n,
            "file": found[n].name,
            "path": str(found[n]),
            "text": text,
            "paras": paras,
            "chars": count_chars(text),
        })
    return out


def load_tags(project: Path) -> dict[int, str]:
    """从两层状态机读取情绪标签（若有）。"""
    tags: dict[int, str] = {}
    for jf in project.rglob("计划.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for c in d.get("chapters", []):
            no = c.get("chapterNo") or c.get("number")
            if no and c.get("emotionTag"):
                tags[int(no)] = str(c["emotionTag"])
    return tags


def load_dict_terms(project: Path) -> set[str]:
    """从设定词典抽取已知专有名词（书名号/引号内、词典表格中的名词列）。"""
    terms: set[str] = set()
    for name in ("04-设定词典.md", "03-设定.md"):
        for p in project.rglob(name):
            t = p.read_text(encoding="utf-8")
            for m in re.finditer(r"[「“]([^\u201d」]{2,8})[\u201d」]", t):
                terms.add(m.group(1))
            for m in re.finditer(r"^\|\s*([A-Za-z\u4e00-\u9fff]{2,8})\s*\|", t, re.M):
                terms.add(m.group(1))
    return terms


def infer_emotion(text: str) -> str:
    """按词频推断主情绪（仅用于交叉核对，不作为唯一依据）。"""
    score = {k: 0 for k in EMOTION_WORDS}
    for k, ws in EMOTION_WORDS.items():
        for w in ws:
            score[k] += text.count(w)
    top = max(score, key=lambda k: score[k])
    return top if score[top] > 0 else ""


def emotion_flags(chs: list[dict], tags: dict[int, str]) -> dict[int, dict]:
    """逐章算出情绪标志位（标签优先，缺失时用词频推断兜底）。"""
    out: dict[int, dict] = {}
    for c in chs:
        tag = tags.get(c["no"], "") or infer_emotion(c["text"])
        f = parse_emotion_tag(tag)
        f["tag"] = tag
        out[c["no"]] = f
    return out


def check_emotion_rhythm(chs: list[dict], tags: dict[int, str]) -> list[dict]:
    """情绪节奏检查。

    要点：
      · **一段连续「纯成本」只报一条**，并在整段结束后报告真实长度。
        早期实现每超阈一次就重置 run，导致「连续 6 章压制」被拆成两条碎片告警，
        真正致命的长度信息反而丢失。改为先切段、后判定。
      · 「纯成本」= 既没有兑现（爽/燃）也没有情报收益（秘辛）。压/泪都算成本。
      · **兑现间距从第 1 章起算**。原先写的是 ``gap = no - last_payoff if last_payoff else 0``，
        第一个兑现章的缺口恒为 0 —— 于是「开局连续 N 章没兑现」这条最要命的
        记录**永远算不出来**。实书《刻名》前 14 章零兑现，门禁从没报过这一条。
    """
    issues = []
    flags = emotion_flags(chs, tags)
    items = [{"no": n, **flags[n]} for n in sorted(flags)]

    def has_gain(it: dict) -> bool:
        return it["payoff"] or it["reveal"]

    # ① 连续「纯成本」段落：整段一次性判定
    i = 0
    while i < len(items):
        if has_gain(items[i]):
            i += 1
            continue
        j = i
        while j < len(items) and not has_gain(items[j]):
            j += 1
        run = j - i
        if run > RULES["max_continuous_pressure"]:
            start, end = items[i]["no"], items[j - 1]["no"]
            issues.append({
                "check": "emotion", "level": "P0",
                "chapter": end,
                "evidence": f"第 {start}-{end} 章连续 {run} 章只有成本、无任何收益"
                            "（既没有兑现，也没有情报推进）",
                "rule": f"连续纯成本不得超过 {RULES['max_continuous_pressure']} 章，"
                        "压两章后必须给一次收益",
                "action": f"把第 {start + RULES['max_continuous_pressure']} 章之后的某章改为收益章："
                          "兑现（打脸/升级/收获）或情报（拿到一条能拿去用的线索），"
                          "或插入一次带净收益的主动决策",
            })
        i = j

    # ② 兑现间距：从第 1 章起算，缺口 = 两个兑现章之间的空档章数
    issues += _gap_issues(items, RULES["max_gap_without_payoff"])
    return issues


def _gap_issues(items: list[dict], limit: int) -> list[dict]:
    """兑现间距。首章之前也计入空档——开局无兑现是最贵的一种断档。"""
    if not items:
        return []
    issues: list[dict] = []
    prev = 0
    worst = (0, 0, 0)          # (空档章数, 空档结束章, 兑现章)
    for it in items:
        if not it["payoff"]:
            continue
        gap = it["no"] - prev - 1
        if gap > worst[0]:
            worst = (gap, it["no"], prev)
        prev = it["no"]

    gap, at, from_no = worst
    if gap > limit:
        where = f"第 {from_no + 1}-{at - 1} 章" if at - 1 > from_no else f"第 {at} 章之前"
        issues.append({
            "check": "emotion", "level": "P0",
            "chapter": at,
            "evidence": f"{where}共 {gap} 章无兑现（首次兑现出现在第 {at} 章）",
            "rule": f"两次兑现之间最多允许 {limit} 章空档；开局段尤其致命"
                    "（平台按前 3 章 / 前 10 章判追读率）",
            "action": "在这段区间内补一次净收益兑现；若为空档落在开局，"
                      "优先改第 1-3 章而不是往中间塞戏",
        })

    # ③ 尾部断档：最后一次兑现之后的空档
    tail = items[-1]["no"] - prev if prev else items[-1]["no"]
    if tail > limit:
        issues.append({
            "check": "emotion", "level": "P0",
            "chapter": items[-1]["no"],
            "evidence": f"截至第 {items[-1]['no']} 章已连续 {tail} 章无兑现",
            "rule": f"两次兑现之间最多允许 {limit} 章空档",
            "action": "下一章必须兑现一次净收益",
        })
    return issues


def check_opening_retention(chs: list[dict], tags: dict[int, str]) -> list[dict]:
    """开局留存窗口：前 3 章 / 前 10 章必须已经给过收益。

    这一项是从《刻名》的实战数据里长出来的——它是本书追读率低的直接原因，
    而**当时的门禁完全没有覆盖**：
      · 全书前 14 章：压 13 章 + 泪 1 章，零兑现、零秘辛；
      · 第一次小胜在第 15 章，第一次标「爽(兑现)」在第 18 章；
      · 收益出现在约 3 万字之后，而平台的追读判定窗口在 6000 字内就关闭了。

    另有一条同源的教训：判定标签必须**机器可读**。当时第 7 章写「压(微爽:活下来)」，
    括号里的两个既骗过了「连续压」的算法，也让作者自己以为这里已经给了甜头。

    窗口只在被写完时才判定，避免新书前几章就被误报。
    """
    flags = emotion_flags(chs, tags)
    nums = sorted(flags)
    issues: list[dict] = []

    def brief(lo: int, hi: int) -> str:
        parts = []
        for n in range(lo, hi + 1):
            if n in flags:
                parts.append(f"{n}{'兑现' if flags[n]['payoff'] else ('情报' if flags[n]['reveal'] else '成本')}")
        return "｜".join(parts)

    if all(n in flags for n in (1, 2, 3)):
        n = sum(1 for k in (1, 2, 3) if flags[k]["payoff"])
        if n < RULES["opening_payoff_first3"]:
            issues.append({
                "check": "emotion", "level": "P0",
                "chapter": 3,
                "evidence": f"前 3 章兑现 {n} 次（要求 ≥{RULES['opening_payoff_first3']}）：{brief(1, 3)}",
                "rule": "黄金三章必须走完「钩住—亮牌—兑现」的完整回路；"
                        "三章内没有一次让读者拿到东西，弃书率在这一段就已经定死",
                "action": "改第 1-3 章：至少让第三章落一次净收益（见 golden-three-chapters.md，"
                          "收益不限于打脸，情报/道具/盟友/位置/名分都算）",
            })

    if all(n in flags for n in range(1, 11)):
        w = [flags[k] for k in range(1, 11)]
        n_pay = sum(1 for f in w if f["payoff"])
        n_gain = sum(1 for f in w if f["payoff"] or f["reveal"])
        if n_pay < RULES["opening_payoff_first10"] or n_gain < RULES["opening_gain_first10"]:
            issues.append({
                "check": "emotion", "level": "P0",
                "chapter": 10,
                "evidence": f"前 10 章兑现 {n_pay} 次（要求 ≥{RULES['opening_payoff_first10']}）、"
                            f"收益 {n_gain} 次（要求 ≥{RULES['opening_gain_first10']}）：{brief(1, 10)}",
                "rule": "前 10 章是追读率的主判定窗口，必须给出稳定的收益节拍（约每 2-3 章一次）",
                "action": "在前 10 章内补足收益节拍；优先把已有的「只给信息不给兑现」的章节改成"
                          "「给出可用的信息 + 一次小胜」",
            })
    return issues


def hook_shapes(chs: list[dict]) -> list[tuple[int, str, str]]:
    """逐章判定章末钩子形状，返回 ``[(章号, 形状, 末段摘录)]``。"""
    out = []
    for c in chs:
        paras = c["paras"]
        tail = "\n".join(paras[-3:]) if len(paras) >= 3 else "\n".join(paras)
        out.append((c["no"], hook_shape(tail), tail[-36:].replace("\n", " ")))
    return out


def check_hook_rotation(chs: list[dict]) -> list[dict]:
    """钩子形状轮换检查。

    与情绪检查同理：**一段连续同形状只报一条**，报告真实长度与改写范围。
    早期实现每超阈一次就报一条，第 15-23 章那段被拆成 9 条碎片告警。
    """
    shapes = hook_shapes(chs)

    issues = []
    i = 0
    while i < len(shapes):
        j = i
        while j + 1 < len(shapes) and shapes[j + 1][1] == shapes[i][1]:
            j += 1
        run = j - i + 1
        if run > RULES["max_same_hook_run"]:
            start, end, kind = shapes[i][0], shapes[j][0], shapes[i][1]
            soft = "（软钩）" if kind in SOFT_HOOKS else ""
            issues.append({
                "check": "hook", "level": "P0",
                "chapter": end,
                "evidence": f"第 {start}-{end} 章钩子形状均为「{kind}」{soft}（连用 {run} 章）：…{shapes[j][2]}",
                "rule": f"同一钩子形状不得连用超过 {RULES['max_same_hook_run']} 章",
                "action": f"改写第 {start + RULES['max_same_hook_run']} 章起的章末，"
                          "轮换钩子类型（见 emotion-and-pacing.md 钩子十二式）",
            })
        i = j + 1
    return issues


def check_hook_variety(chs: list[dict]) -> list[dict]:
    """钩子**强度分布**检查（连用检查之外的第二道）。

    「不连用」只保证读者不腻，保证不了读者想追。软钩（短句留白 / 对白悬停）
    靠氛围与语气收尾，读者读完没得到新东西；硬钩（信息炸 / 抉择 / 倒计时 /
    登场 / 动作悬停）给出新信息或逼出选择，才是追读的驱动力。

    实书《刻名》84 章：短句留白 33 + 对白悬停 22 = 65%，硬钩合计 11 ——
    连用检查全部通过，追读率却持续走低。**这道检查就是为这个缺口补的。**
    """
    shapes = hook_shapes(chs)
    if len(shapes) < 10:
        return []
    soft = [s for _, s, _ in shapes if s in SOFT_HOOKS]
    hard = [s for _, s, _ in shapes if s in HARD_HOOKS]
    share = len(soft) / len(shapes)
    if share <= RULES["max_soft_hook_share"]:
        return []

    from collections import Counter
    soft_top = "、".join(f"{k}×{v}" for k, v in Counter(soft).most_common())
    hard_top = "、".join(f"{k}×{v}" for k, v in Counter(hard).most_common()) or "无"
    return [{
        "check": "hook", "level": "P1",
        "chapter": shapes[-1][0],
        "evidence": f"软钩占比 {share:.0%}（{len(soft)}/{len(shapes)} 章，上限 "
                    f"{RULES['max_soft_hook_share']:.0%}）：{soft_top}；硬钩：{hard_top}",
        "rule": "章末钩子不能只靠氛围与语气收尾——软钩占比过高时读者没有追读的理由",
        "action": "把一部分章末改成硬钩：抛出新信息（信息炸）、逼出选择（抉择式）、"
                  "划定明确期限（倒计时式）或让关键角色登场",
        "chapterList": [n for n, s, _ in shapes if s in SOFT_HOOKS],
    }]


def check_ai_cliche(chs: list[dict]) -> list[dict]:
    issues = []
    for c in chs:
        body = COMMENT.sub("", c["text"])
        n = count_chars(c["text"])
        if n < 200:
            continue
        hits = {w: body.count(w) for w in AI_CLICHE if body.count(w) > 0}
        total = sum(hits.values())
        per_1k = total / (n / 1000)
        if per_1k > RULES["ai_cliche_per_1k"]:
            top = sorted(hits.items(), key=lambda kv: -kv[1])[:4]
            issues.append({
                "check": "ai_cliche", "level": "P1",
                "chapter": c["no"],
                "evidence": f"每千字 {per_1k:.1f} 处 AI 腔高频词（上限 {RULES['ai_cliche_per_1k']}）："
                            + "、".join(f"{k}×{v}" for k, v in top),
                "rule": "去 AI 味：抽象情绪须改为具体动作/对话/细节",
                "action": "按 style-guide.md 润色步骤 1 删除病灶",
            })
    return issues


def check_setting_breach(chs: list[dict], terms: set[str]) -> list[dict]:
    """检测正文中出现的疑似专有名词是否已在设定词典登记。

    早期实现用「三字以上中文词」正则匹配，会把「的时候」「了一下」这类
    高频虚词全部误报（实测噪声 93 个），完全不可用。

    改用两个高精度信号：
      A. 书名号/引号包起来的词（作者显式强调的术语）
      B. 带引号且反复出现的二字以上名词
    并且**只在词典非空时启用**，避免词典缺失时满屏误报。
    """
    if not terms:
        return []
    known = set(terms)
    unknown: dict[str, int] = {}

    for c in chs:
        body = COMMENT.sub("", c["text"])
        # 信号 A：显式术语标记
        for m in re.finditer(r"[「“]([^\u201d」\n]{2,10})[\u201d」]", body):
            w = m.group(1).strip()
            if w and w not in known and w not in unknown:
                unknown[w] = c["no"]

    # 过滤掉明显的非术语：含标点、口语短句、虚词开头、疑问/否定短语、称谓叠词
    NOISE = re.compile(
        r"[，。！？、；：\s…—]|"                     # 含标点或省略号
        r"^(的|了|是|在|他|她|我|你|这|那|有|不|就|也|还)|"   # 虚词开头
        r"(什么|怎么|不是|没有|一个|这个|那个|如果|因为|可以|知道|时候|自己|别的|一样)|"
        r"(啊|吧|吗|呢|哦|喂)$|"                     # 语气词结尾
        r"^(慢走|到了|过了|接着|然后|于是)"
    )
    unknown = {w: n for w, n in unknown.items() if not NOISE.search(w)}

    if len(unknown) > RULES["allow_unknown_terms"]:
        sample = list(unknown.items())[:8]
        return [{
            "check": "setting", "level": "P2",
            "chapter": 0,
            "evidence": f"疑似未登记的引用术语 {len(unknown)} 个，示例："
                        + "、".join(f"{w}(第{n}章)" for w, n in sample),
            "rule": "正文不得出现设定词典之外的能力/设定名词（同义新造）",
            "action": "人工确认是新设定还是同义混用；新设定须登记词典并标注首现章",
        }]
    return []


def parse_declaration(plan: str) -> dict | None:
    """解析细纲中的结构化声明块 ``<!-- BATCH-DECLARATION ... -->``。

    这是塌陷点一（规划与产出漂移）的核心修复：把「本批次承诺做什么」
    变成机器可读的契约，批次末由 check_foreshadow_execution 核对。

    返回 {"batch":int, "range":(lo,hi), "forced_payoff":set, "planned_ops":dict}
    """
    m = re.search(r"<!--\s*BATCH-DECLARATION\s*(.*?)-->", plan, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    decl: dict = {"batch": 0, "range": (0, 0), "forced_payoff": set(), "planned_ops": {}}

    for line in body.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "batch":
            try:
                decl["batch"] = int(val)
            except ValueError:
                pass
        elif key == "range":
            parts = re.split(r"[-,]", val)
            if len(parts) >= 2:
                try:
                    decl["range"] = (int(parts[0]), int(parts[1]))
                except ValueError:
                    pass
        elif key in ("forced_payoff", "force_payoff"):
            decl["forced_payoff"] = set(re.findall(r"F\d+", val))
        elif key == "planned_ops":
            # 形如 "45: plant:F61, advance:F45" 的多行
            for sub in val.split(";"):
                if ":" not in sub:
                    continue
                ch, _, ops = sub.partition(":")
                try:
                    decl["planned_ops"][int(ch.strip())] = re.findall(r"F\d+", ops)
                except ValueError:
                    continue
    return decl


def check_foreshadow_execution(project: Path, chs: list[dict]) -> list[dict]:
    """核对细纲声明的伏笔动作是否在本批次实际执行（塌陷点一的闭环）。

    优先使用结构化声明块；无声明块时回退到自然语言解析（兼容旧项目）。
    """
    issues = []
    plan_files = list(project.rglob("卷细纲.md"))
    ledger_files = (
        list(project.rglob("*伏笔*.md"))
        + list(project.rglob("03-设定.md"))
        + list(project.rglob("04-设定词典.md"))
    )
    if not plan_files or not ledger_files:
        return []

    plan = plan_files[0].read_text(encoding="utf-8")
    ledger = "\n".join(p.read_text(encoding="utf-8") for p in ledger_files)
    written = {c["no"] for c in chs}

    decl = parse_declaration(plan)
    if decl and decl["forced_payoff"]:
        declared = decl["forced_payoff"]
        lo, hi = decl["range"]
        scope = {n for n in written if (not lo or lo <= n <= hi)}
        source = f"结构化声明块（批次 {decl['batch']}）"
    else:
        # 回退：自然语言「强制回收伏笔」行
        m = re.search(r"强制回收伏笔[^\n]*", plan)
        if not m:
            return []
        declared = set(re.findall(r"F\d+", m.group(0)))
        scope = written
        source = "自然语言「强制回收伏笔」行（建议改用结构化声明块）"
    if not declared:
        return []

    # 实际动作：台账里提到「第 N 章」且 N 落在本批次范围内
    actual: set[str] = set()
    for line in ledger.split("\n"):
        fm = re.match(r"^\|\s*(F\d+)", line)
        if not fm:
            continue
        for cm in re.finditer(r"第\s*(\d+)\s*章", line):
            if int(cm.group(1)) in scope:
                actual.add(fm.group(1))
                break

    missing = sorted(declared - actual, key=lambda s: int(s[1:]))
    if missing:
        issues.append({
            "check": "foreshadow", "level": "P0",
            "chapter": 0,
            "evidence": f"{source}声明「强制回收」{len(declared)} 条，"
                        f"其中 {len(missing)} 条在已写章节中未见动作："
                        + "、".join(missing)
                        + f"（执行率 {len(declared & actual)}/{len(declared)}）",
            "rule": "细纲声明的伏笔动作必须在批次内执行或显式改期，不得静默跳过",
            "action": "逐条处理：补写、改期并更新声明块、或补登账本",
        })
    if not decl:
        issues.append({
            "check": "foreshadow", "level": "P2",
            "chapter": 0,
            "evidence": "未找到结构化声明块 BATCH-DECLARATION，已退化为自然语言解析（精度较低）",
            "rule": "细纲应包含 BATCH-DECLARATION 结构化声明块",
            "action": "按 phase2-arc-planning.md 2.1 节补写声明块",
        })
    return issues


def load_waivers(project: Path) -> list[dict]:
    """读取豁免台账（项目根或 `_engine/` 下的 ``waivers.json``）。

    豁免的用意**不是让门禁闭嘴**，而是让「带着已知缺陷上线」这件事每次都被看见。
    《刻名》的教训：项目里有一条口头约定「第 1-8 章不修改（永久 FAIL）」，
    脚本不知道，于是门禁连着报 P0、人也连着忽略，最贵的缺陷一路走到线上。
    落盘成台账之后，它就从「默认忽略」变成「每次体检都打印一行」。
    """
    for p in (project / "waivers.json", project / "_engine" / "waivers.json"):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = data.get("waivers") if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    return []


def _waiver_hit(issue: dict, w: dict) -> bool:
    if w.get("check") and w["check"] != issue.get("check"):
        return False
    rng = w.get("chapters")
    if not rng:
        return True                      # 未限定章号 → 命中该项目该检查的全部问题
    ch = issue.get("chapter") or 0
    if not ch:
        return True                      # 项目级问题（chapter=0）不按章号过滤
    lo = int(rng[0])
    hi = int(rng[1]) if len(rng) > 1 else lo
    return lo <= ch <= hi


def apply_waivers(issues: list[dict], waivers: list[dict]) -> tuple[list[dict], list[dict]]:
    """把命中的问题降级为 P2 并挪进「已豁免」区，返回 ``(保留, 已豁免)``。"""
    if not waivers:
        return issues, []
    kept, waived = [], []
    for it in issues:
        for w in waivers:
            if _waiver_hit(it, w):
                it = dict(it)
                it["level"] = "P2"
                it["waived"] = w.get("reason", "（未填理由）")
                it["waivedAt"] = w.get("approvedAt", "")
                waived.append(it)
                break
        else:
            kept.append(it)
    return kept, waived


def retention_overview(chs: list[dict], tags: dict[int, str]) -> list[str]:
    """把与追读率直接相关的几个数**每次都打出来**，不藏在告警里。

    告警可以被忽略，速览不行——它回答的是「这本书现在能不能让人追下去」。
    """
    flags = emotion_flags(chs, tags)
    nums = sorted(flags)
    if not nums:
        return []
    total = len(nums)
    pay = [n for n in nums if flags[n]["payoff"]]
    gain = [n for n in nums if flags[n]["payoff"] or flags[n]["reveal"]]

    def cnt(rng):
        return sum(1 for n in rng if n in flags and flags[n]["payoff"])

    def gain_cnt(rng):
        return sum(1 for n in rng if n in flags and (flags[n]["payoff"] or flags[n]["reveal"]))

    shapes = hook_shapes(chs)
    soft = sum(1 for _, s, _ in shapes if s in SOFT_HOOKS)

    lines = [
        "留存速览（追读率相关，逐项对照 RULES 阈值）",
        f"  前 3 章兑现    {cnt(range(1, 4))} 次"
        f"（要求 ≥{RULES['opening_payoff_first3']}）",
        f"  前 10 章兑现   {cnt(range(1, 11))} 次"
        f"（要求 ≥{RULES['opening_payoff_first10']}）｜收益 {gain_cnt(range(1, 11))} 次"
        f"（要求 ≥{RULES['opening_gain_first10']}）",
        f"  首次兑现       " + (f"第 {pay[0]} 章" if pay else "**全书无兑现**"),
        f"  全书兑现       {len(pay)}/{total} 章"
        f"｜收益（兑现+秘辛）{len(gain)}/{total} 章",
        f"  软钩占比       {soft}/{total}"
        + (f" = {soft / total:.0%}" if total else "")
        + f"（上限 {RULES['max_soft_hook_share']:.0%}）",
    ]
    return lines


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not args:
        print(__doc__)
        return 2

    json_mode = "--json" in args
    strict = "--strict" in args
    span = None
    for i, a in enumerate(args):
        if a == "--chapters" and i + 1 < len(args):
            lo, _, hi = args[i + 1].partition("-")
            span = set(range(int(lo), int(hi or lo) + 1))
    positional = [a for a in args if not a.startswith("--") and not re.fullmatch(r"\d+-\d+|\d+", a)]
    if not positional:
        print(__doc__)
        return 2
    project = Path(positional[0])
    if not project.exists():
        print(f"错误：{project} 不存在")
        return 2

    chs = read_chapters(project, span)
    if not chs:
        print(f"错误：{project} 下未找到章节文件")
        return 2

    tags = load_tags(project)
    terms = load_dict_terms(project)
    # 留存判定全部建立在 emotionTag 上。标签大面积缺失时，宁可降级为提示——
    # 用词频推断出来的「主情绪」去报 P0，是在制造「虚假的精确」。
    tag_coverage = (sum(1 for c in chs if tags.get(c["no"])) / len(chs)) if chs else 0.0

    issues: list[dict] = []
    issues += check_emotion_rhythm(chs, tags)
    issues += check_opening_retention(chs, tags)
    issues += check_tag_hygiene(chs, tags)
    issues += check_hook_rotation(chs)
    issues += check_hook_variety(chs)
    issues += check_ai_cliche(chs)
    issues += check_setting_breach(chs, terms)
    # 伏笔声明核对是**批次级**检查，单章模式下无意义（会误报全批次的差异）
    if span is None:
        issues += check_foreshadow_execution(project, chs)

    if tag_coverage < 0.8:
        issues = [i for i in issues
                  if not (i["check"] == "emotion" and i["level"] == "P0")]
        issues.append({
            "check": "emotion", "level": "P2", "chapter": 0,
            "evidence": f"只有 {tag_coverage:.0%} 的章节在 计划.json 里记了 emotionTag，"
                        "情绪节奏与开局留存判定已降级为提示",
            "rule": "留存判定建立在 emotionTag 上；标签缺失时用词频推断，结论不可靠",
            "action": "补齐两层 计划.json 的 emotionTag（兑现「爽/燃/暖」、情报「秘辛」、"
                      "成本「压/泪」）后重跑",
        })

    waived: list[dict] = []
    if span is None:
        issues, waived = apply_waivers(issues, load_waivers(project))

    p0 = [i for i in issues if i["level"] == "P0"]
    p1 = [i for i in issues if i["level"] == "P1"]
    p2 = [i for i in issues if i["level"] == "P2"]
    overview = retention_overview(chs, tags)

    if json_mode:
        print(json.dumps({
            "project": str(project),
            "chapters": len(chs),
            "totalChars": sum(c["chars"] for c in chs),
            "counts": {"p0": len(p0), "p1": len(p1), "p2": len(p2),
                       "waived": len(waived)},
            "retention": overview,
            "issues": issues,
            "waived": waived,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"质量体检：{project}")
        print(f"扫描 {len(chs)} 章 / {sum(c['chars'] for c in chs)} 字\n")
        for ln in overview:
            print(ln)
        print()
        if not issues and not waived:
            print("PASS  未发现违反纪律的问题")
        for lv in ("P0", "P1", "P2"):
            group = [i for i in issues if i["level"] == lv]
            if not group:
                continue
            print(f"── {lv}（{len(group)} 项）")
            for i in group:
                loc = f"第 {i['chapter']} 章 " if i["chapter"] else ""
                print(f"  [{i['check']}] {loc}{i['evidence']}")
                print(f"     规则：{i['rule']}")
                print(f"     动作：{i['action']}")
            print()
        if waived:
            print(f"── 已豁免（{len(waived)} 项，来自 waivers.json）")
            print("   ⚠️ 这些是**已知但决定不改**的问题，会随书一路带下去。")
            for i in waived:
                loc = f"第 {i['chapter']} 章 " if i["chapter"] else ""
                print(f"  [{i['check']}] {loc}{i['evidence'][:60]}")
                print(f"     豁免理由：{i['waived']}")
            print()
        print(f"汇总：P0 {len(p0)} / P1 {len(p1)} / P2 {len(p2)}"
              + (f"　⚠️ 另有已豁免 {len(waived)} 项" if waived else ""))

    return 1 if (strict and p0) else 0


if __name__ == "__main__":
    sys.exit(main())
