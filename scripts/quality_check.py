#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量体检脚本（webnovel-writer skill）

把「情绪节奏 / 钩子轮换 / 爽点间距 / 设定越界 / AI 腔密度」这五项原本
只能靠模型自评的质量维度，变成可计算、可复现、可趋势化的客观指标。

设计原则：
  1. 只做能确定性判定的检查。语义判断（这段话好不好）仍归模型，
     本脚本只回答「是否违反了已写死的纪律」。
  2. 所有阈值集中在 RULES，便于按书调参；判定结果带明确证据（章号/原句）。
  3. JSON 输出键名全 ASCII，供 Phase 4 校验与 CI 消费。

用法:
    python quality_check.py <项目目录>                      # 全量体检
    python quality_check.py <项目目录> --json                # 机器可读
    python quality_check.py <项目目录> --chapters 35-39      # 只查指定章段
    python quality_check.py <项目目录> --strict              # 有 P0 即退出码 1

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
    "max_continuous_pressure": 2,      # 连续「压」上限（超出必须爆）
    "max_gap_without_payoff": 8,       # 距上次爽/燃兑现的最长章数
    # 钩子轮换
    "max_same_hook_run": 2,            # 同一钩子形状最多连用章数（第 3 章即违规）
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


def check_emotion_rhythm(chs: list[dict], tags: dict[int, str]) -> list[dict]:
    """情绪节奏检查。

    要点：**一段连续压制只报一条**，并在整段结束后报告真实长度。
    早期实现每超阈一次就重置 run，导致「连续 6 章压制」被拆成两条碎片告警，
    真正致命的长度信息反而丢失。改为先切段、后判定。
    """
    issues = []

    # 归一化标签：把「压+泪」「压→爽(小胜)」这类复合标签拆成标志位
    normalized = []
    for c in chs:
        tag = tags.get(c["no"], "") or infer_emotion(c["text"])
        normalized.append({
            "no": c["no"],
            "tag": tag,
            "pressure": ("压" in tag) and not (("爽" in tag) or ("燃" in tag)),
            "payoff": ("爽" in tag) or ("燃" in tag),
        })

    # ① 连续压制段落：整段一次性判定
    i = 0
    while i < len(normalized):
        if not normalized[i]["pressure"]:
            i += 1
            continue
        j = i
        while j < len(normalized) and normalized[j]["pressure"]:
            j += 1
        run = j - i
        if run > RULES["max_continuous_pressure"]:
            start, end = normalized[i]["no"], normalized[j - 1]["no"]
            issues.append({
                "check": "emotion", "level": "P0",
                "chapter": end,
                "evidence": f"第 {start}-{end} 章连续 {run} 章为「压」，无任何兑现",
                "rule": f"连续压不得超过 {RULES['max_continuous_pressure']} 章，压两章后必须爆",
                "action": f"把第 {start + RULES['max_continuous_pressure']} 章之后的某章改为兑现章"
                          "（打脸/升级/收获），或插入一次带净收益的主动决策",
            })
        i = j

    # ② 兑现间距：只在「整段压制结束」或「越界时」报一条，不重置基准
    last_payoff = 0
    worst_gap = 0
    worst_at = 0
    for item in normalized:
        if item["payoff"]:
            gap = item["no"] - last_payoff if last_payoff else 0
            if gap > worst_gap:
                worst_gap, worst_at = gap, item["no"]
            last_payoff = item["no"]
    tail_gap = normalized[-1]["no"] - last_payoff if last_payoff else 0
    if worst_gap > RULES["max_gap_without_payoff"]:
        issues.append({
            "check": "emotion", "level": "P0",
            "chapter": worst_at,
            "evidence": f"第 {worst_at - worst_gap}-{worst_at - 1} 章共 {worst_gap} 章无兑现"
                        f"（首次兑现出现在第 {worst_at} 章）",
            "rule": f"爽点间隔不得超过 {RULES['max_gap_without_payoff']} 章",
            "action": "在这段区间内补一次净收益兑现；开局段尤其致命（前三章决定弃书率）",
        })
    elif tail_gap > RULES["max_gap_without_payoff"]:
        issues.append({
            "check": "emotion", "level": "P0",
            "chapter": normalized[-1]["no"],
            "evidence": f"截至最后一章已连续 {tail_gap} 章无兑现",
            "rule": f"爽点间隔不得超过 {RULES['max_gap_without_payoff']} 章",
            "action": "下一章必须兑现一次净收益",
        })
    return issues


def check_hook_rotation(chs: list[dict]) -> list[dict]:
    """钩子形状轮换检查。

    与情绪检查同理：**一段连续同形状只报一条**，报告真实长度与改写范围。
    早期实现每超阈一次就报一条，第 15-23 章那段被拆成 9 条碎片告警。
    """
    shapes = []
    for c in chs:
        tail = "\n".join(c["paras"][-3:]) if len(c["paras"]) >= 3 else "\n".join(c["paras"])
        shapes.append((c["no"], hook_shape(tail), tail[-36:].replace("\n", " ")))

    issues = []
    i = 0
    while i < len(shapes):
        j = i
        while j + 1 < len(shapes) and shapes[j + 1][1] == shapes[i][1]:
            j += 1
        run = j - i + 1
        if run > RULES["max_same_hook_run"]:
            start, end, kind = shapes[i][0], shapes[j][0], shapes[i][1]
            issues.append({
                "check": "hook", "level": "P0",
                "chapter": end,
                "evidence": f"第 {start}-{end} 章钩子形状均为「{kind}」（连用 {run} 章）：…{shapes[j][2]}",
                "rule": f"同一钩子形状不得连用超过 {RULES['max_same_hook_run']} 章",
                "action": f"改写第 {start + RULES['max_same_hook_run']} 章起的章末，"
                          "轮换钩子类型（见 emotion-and-pacing.md 钩子十二式）",
            })
        i = j + 1
    return issues


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

    issues: list[dict] = []
    issues += check_emotion_rhythm(chs, tags)
    issues += check_hook_rotation(chs)
    issues += check_ai_cliche(chs)
    issues += check_setting_breach(chs, terms)
    # 伏笔声明核对是**批次级**检查，单章模式下无意义（会误报全批次的差异）
    if span is None:
        issues += check_foreshadow_execution(project, chs)

    p0 = [i for i in issues if i["level"] == "P0"]
    p1 = [i for i in issues if i["level"] == "P1"]
    p2 = [i for i in issues if i["level"] == "P2"]

    if json_mode:
        print(json.dumps({
            "project": str(project),
            "chapters": len(chs),
            "totalChars": sum(c["chars"] for c in chs),
            "counts": {"p0": len(p0), "p1": len(p1), "p2": len(p2)},
            "issues": issues,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"质量体检：{project}")
        print(f"扫描 {len(chs)} 章 / {sum(c['chars'] for c in chs)} 字\n")
        if not issues:
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
        print(f"汇总：P0 {len(p0)} / P1 {len(p1)} / P2 {len(p2)}")

    return 1 if (strict and p0) else 0


if __name__ == "__main__":
    sys.exit(main())
