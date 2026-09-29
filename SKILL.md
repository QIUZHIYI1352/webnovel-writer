---
name: webnovel-writer
description: |
  网文连载创作引擎：面向单章 2000 字左右、可扩展至数百上千章的超长篇连载写作。
  内置黄金三章开局设计、爽燃泪情绪曲线调度、三级规划（总纲-卷-批次）、
  分层记忆系统（卷摘要/章节摘要/伏笔账本/主线状态卡/设定词典）、力量体系自洽校验、
  连载模式（"继续更新"从断点续写下一批次）、修订回退与可选并行写作。
  当用户要求：写网文、写小说、写长篇、连载更新、继续更新、写爽文、黄金三章、章节创作时使用。
metadata:
  trigger: 写网文 / 写小说 / 连载 / 继续更新 / 长篇 / 爽文 / 章节创作
agent_created: true
---

# webnovel-writer: 网文连载创作引擎

## 四大铁律

1. **黄金三章** — 前三章必须完成：冲突抛出、金手指亮相、第一个爽点兑现。开局即战场。
2. **爽点前置** — 每章 300 字内进入冲突或钩子，静态描写开场不超过 100 字。
3. **主线导航** — 读者在任何一章都能回答三问：主角要什么？阻碍是什么？下一步是什么？
4. **伏笔必收** — 每条伏笔登记入账本，最多跨 2 卷必须回收。烂尾是网文原罪。

## 质量门禁（第五铁律）

**凡能确定性判定的，一律交给脚本，禁止用模型自评代替。**

每章写完立即跑 `scripts/quality_check.py`，P0 问题**必须在本章内修掉**再进下一章。
批次末跑 Phase 4 全量校验，用脚本结论驱动修复。检查项：情绪节奏、钩子轮换、
AI 腔密度、设定越界、伏笔声明执行率。

> 这条铁律的由来：早期版本除字数外全靠自评，`一部实战验证作品` 实测出现
> 「第 1-6 章连续 6 章无兑现」「钩子同形状连用 5 章」「细纲声明执行率 50%」
> 三类 P0 问题，全部安静躺到第 39 章才被人肉审稿发现。见 `phase4-validation.md` 文末。

## 规模定位

- 单章**目标 2000 字**（写作区间 1800-2200，达标区间 1700-2400），节奏紧凑、不注水
- 千章规模 = **卷（30-80 章）→ 批次（默认 10 章）→ 单章** 三级规划
- 任何时刻只为当前批次写细纲，绝不为 500 章之后写细纲
- 状态文件分两层：根索引 `01-写作计划.json`（书级，永不增长）+ 每卷 `vol-XX/计划.json`（卷级）
- 上下文只保留：总纲 + 当前批次细纲 + 分层记忆（约 15k 字），**严禁重读全书旧章节全文**

## 核心流程

进入每个阶段前，先读取对应流程文档获取详细执行指令。

### Phase 0 手稿接入（既有作品挂载）
用户给出一个非本引擎结构的手稿目录并要求"接着写/继续更新"时启用：清点 → 脚本实测字数 →
只增不改地补建标准档案（总纲/状态机/伏笔账本/文风基准/分层记忆）→ 反推伏笔入账 → 跑体检 → 再开写。
绝不覆盖、移动或重命名用户原有文件。
→ 详见 [phase0-migration.md](references/flows/phase0-migration.md)

### Phase 1 创作立项
偏好加载 / 断点检测 / 递进问答 / 总纲与力量体系 / 黄金三章设计 / 建项。
→ 详见 [phase1-intake.md](references/flows/phase1-intake.md)

### Phase 2 卷内细纲
为当前卷生成批次细纲（7 列规划 + 情绪标签 + 伏笔操作 + 情绪曲线），排入账本到期伏笔；
批次完成后归档压缩，卷完成后卷末归档。
→ 详见 [phase2-arc-planning.md](references/flows/phase2-arc-planning.md)

### Phase 3 逐章创作
进入后全自动，逐章执行：写前分析 → 撰写（1800-2200字）→ 润色去AI味 → **双脚本校验（字数 + 质量门禁）**
→ 回写记忆。批次写完自动校验并细化下一批次，卷写完执行归档。
质量门禁报 P0 的章节必须当场修复，不得顺延。
→ 详见 [phase3-writing.md](references/flows/phase3-writing.md)

### Phase 4 校验修复
每批次完成后自动校验：**先跑双脚本拿客观结论，再决定改什么**。检查七项：
字数 / 情绪节奏 / 钩子轮换 / AI 腔密度 / 黄金三章 / 设定越界 / 伏笔声明执行率，
不合格自动重写（最多 2 轮）。原则：脚本判定，模型修复。
→ 详见 [phase4-validation.md](references/flows/phase4-validation.md)

### 连载模式
用户说"继续更新"或再次触发本 skill 时，断点检测自动发现未完成项目，按 `nextAction`
（continue / arc-planning / volume-archive）续写下一批次，无需重新问答。
→ 断点检测与修订回退规则见 [shared-infrastructure.md](references/flows/shared-infrastructure.md)

### Phase 5 审稿模式（对既有稿件体检）
用户说"评价一下这本""审一下这卷"时启用：只读不改，跑客观数据（字数/波动）、情绪序列、
钩子形状轮换、伏笔入账核对、规则自洽、人物档案规格六项检查，产出 `06-评审.md`。
→ 详见 [phase5-review.md](references/flows/phase5-review.md)

## 共享机制

分层记忆系统（L1-L7 与读取预算）、两层状态机 JSON、伏笔账本、主线状态卡、设定词典、
字数检查脚本 + 质量体检脚本（绝对路径调用）、修订与回退流程、用户偏好。
→ 详见 [shared-infrastructure.md](references/flows/shared-infrastructure.md)

## 写作指南（按需加载）

| 文件 | 内容 | 何时读 |
|------|------|--------|
| [golden-three-chapters.md](references/guides/golden-three-chapters.md) | 黄金三章技法、开局死法清单、分赛道变体 | 立项与写第 1-3 章前 |
| [emotion-and-pacing.md](references/guides/emotion-and-pacing.md) | 爽燃泪情绪配方、2000字微节奏、钩子十二式 | 每批次细纲前必读 |
| [characters-and-world.md](references/guides/characters-and-world.md) | 人物弧光、配角记忆点、反派逻辑、力量体系自洽 | 立项与卷归档时 |
| [style-guide.md](references/guides/style-guide.md) | 去AI味、文笔辨识度、反套路、水字检测 | 每章润色步骤参照 |
| [chapter-template.md](references/templates/chapter-template.md) | 章节文件模板、写作纪律、AI腔清单 | 每章创建文件时 |
