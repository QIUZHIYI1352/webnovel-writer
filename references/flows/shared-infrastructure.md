# 共享机制

## 项目目录结构

```
webnovel/
├── user-preferences.json                 # 用户偏好（跨书共享）
└── {timestamp}-{书名}/
    ├── 01-写作计划.json                  # 【书级索引】书名/卷列表/总进度/writingMode（永远小于 5KB）
    ├── 00-总纲.md                        # 一句话故事/主线/卷级规划表(活文档)/力量体系规则/价值观底线
    ├── 02-主线状态卡.md                  # 主角当前状态快照（每章回写）
    ├── 03-伏笔账本.md                    # 全部伏笔登记与回收跟踪
    ├── 04-设定词典.md                    # 设定名词/力量等级/首现章/读者已知
    ├── 05-文风基准.md                    # 第 1 章定稿后锚定的文风样例
    └── vol-01/                           # 每卷一个目录
        ├── 计划.json                     # 【卷级状态机】本卷章级状态（只读本卷，绝不全量读取）
        ├── 卷细纲.md                     # 当前批次 7 列细纲 + 批次摘要区
        ├── 章节摘要.md                   # 逐章摘要追加（150-250字/章），L2 只读末尾 3 条
        ├── 卷摘要.md                     # 卷归档时生成（300-400字）
        ├── 第0001章-标题.md
        └── ...
```

章节文件名统一 4 位数字：`第0001章-xxx.md` … `第1000章-xxx.md`（`第%04d章-{标题}.md`），保证排序正确。
字数脚本同时兼容其它常见命名（`第9章.md`、`第9章-标题.md`），但本 skill 新建项目统一用 4 位格式。

> **为什么必须分两层 JSON**：单文件在千章规模下会膨胀到 200KB 以上，而 Phase 3 每章都要读它——读取成本随章数线性增长，必然爆上下文。根索引只存卷级统计（永不增长），章级状态按卷拆分，每次只读当前卷。这是千章规模的第一硬约束。

## 分层记忆系统（千章规模的生存法则）

写第 N 章时，上下文中**只允许**包含以下记忆，严禁读取更早章节全文：

| 层 | 内容 | 来源 | 作用 |
|----|------|------|------|
| L1 | 上一章末 500 字原文 | `vol-XX/第上一章.md` 尾部 | 衔接语态、情绪落点 |
| L2 | 最近 3 章摘要 | `vol-XX/章节摘要.md` 末尾 3 条 | 近期情节连续性 |
| L3 | 当前批次 7 列细纲 | `vol-XX/卷细纲.md` | 本章任务与钩子设计 |
| L4 | 近 2 卷卷摘要 | `vol-XX/卷摘要.md` ×2 | 中期脉络 |
| L5 | 主线状态卡 | `02-主线状态卡.md` | 主角当前实力/资源/关系/目标 |
| L6 | 伏笔账本相关条目 | `03-伏笔账本.md` | 伏笔埋/推/收 |
| L7 | 设定词典 + 总纲 | `04-设定词典.md`、`00-总纲.md` | 设定一致性与主线方向 |

**读取预算**：单章创作前的记忆读取总量控制在 ~15k 字以内（L1 0.5k + L2 0.8k + L3 2k + L4 0.8k + L5 0.5k + L6 0.5k + L7 视词典大小而定）。设定词典超过 5k 字时，只读本章相关条目。

> 大坝原理：千章的连贯性不靠"记住全部"，靠"每章回写 + 分层压缩 + 只读所需层"。Phase 3 步骤 4 的回写一步不可省，漏写一次，第 N+10 章就会开始崩。

## 01-写作计划.json（书级索引 Schema）

```json
{
  "bookTitle": "书名",
  "writingMode": "serial",
  "createdAt": "2026-09-16",
  "volumes": [
    { "volume": 1, "name": "卷名", "range": [1, 40], "status": "in_progress", "completedChapters": 12 }
  ],
  "lastCompletedChapter": 12,
  "totalCompletedChapters": 12,
  "nextAction": "continue"
}
```

`nextAction` 取值：`continue`（续写下一章）/ `arc-planning`（需细化新批次）/ `volume-archive`（需执行卷归档）/ `done`（本书完结）。

## vol-XX/计划.json（卷级状态机 Schema）

```json
{
  "volume": 1,
  "range": [1, 40],
  "chapters": [
    {
      "number": 1,
      "title": "章节标题",
      "emotionTag": "爽+燃",
      "status": "pending",
      "wordCount": 0,
      "foreshadowOps": ["plant:F-001", "payoff:F-002"],
      "batch": 1
    }
  ]
}
```

状态流转：`pending → in_progress → completed / failed`。
损坏处理：卷级 JSON 解析失败时，从 `章节摘要.md`、章节文件存在性 + 字数脚本重建状态，并修复 JSON；根索引失败时从各卷 JSON 汇总重建。

## 连载断点检测（触发 skill 时第一步）

1. 读根 `01-写作计划.json` → 拿 `nextAction` 与 `lastCompletedChapter`。
2. 读当前卷 `vol-XX/计划.json`，找第一个 `in_progress`（半成品章，重写）/ `failed`（重写）/ `pending`（新写）。
3. 全部 `completed` 且用户说"继续更新" → 按 `nextAction` 决定：补细纲 / 卷归档 / 开新卷；续写量默认 10 章（用户可指定，上限 20 章）。
4. 旧版项目缺 `05-文风基准.md`、缺分卷 JSON → 自动迁移：从已有章节提取最佳段落补基准；按卷拆分旧 JSON 并回填。

## 修订与回退流程

用户任何时候说"重写第 X 章""把主角名字改成 Y""这段剧情改掉"：

1. **定位影响面**：查 `03-伏笔账本.md`（该章埋/收的伏笔）与 `04-设定词典.md`（该章首现的设定）、后续章节摘要（谁引用了被改内容）。
2. **判断级联范围**，向用户说明将受影响的章节清单，确认后执行。
3. **重写类型**：
   - 局部润色（不动情节）→ 直接改，无需级联
   - 情节修改 → 重写该章 + 检查后续 3 章衔接，更新该章摘要、伏笔账本、主线状态卡
   - 设定变更（改名/改规则）→ 全项目检索替换 + 更新设定词典 + 检查前文是否与新设定冲突
4. **回退**：将相关章 `status` 置回 `pending`，根索引 `nextAction` 置 `continue`，`lastCompletedChapter` 回退到改动章前一章。
5. **改完必跑 Phase 4 校验**（至少字数与衔接两项）。

## 字数检查脚本

脚本位于 skill 安装目录（用户级默认 `~/.workbuddy/skills/webnovel-writer/`），**必须用绝对路径调用**，因为执行时的工作目录是用户的小说项目目录：

```bash
# 单章（默认达标区间 1700-2400）
python "<SKILL_DIR>/scripts/check_wordcount.py" "<项目>/vol-01/第0001章-标题.md"

# 整卷（递归扫描该卷下所有章节文件）
python "<SKILL_DIR>/scripts/check_wordcount.py" --all "<项目>/vol-01/"

# 整本书（递归扫描项目下所有卷）
python "<SKILL_DIR>/scripts/check_wordcount.py" --all "<项目>/"

# 自定义区间 / 机器可读输出
python "<SKILL_DIR>/scripts/check_wordcount.py" <文件> 1600 2600
python "<SKILL_DIR>/scripts/check_wordcount.py" --all "<项目>/" --json
```

Windows 下 `python` 不可用时用托管解释器：`C:/Users/<user>/.workbuddy/binaries/python/versions/3.13.12/python.exe`。
口径：CJK 汉字 + 中文标点（网文平台计字），HTML 注释（章节元信息头）不计入。
输出：`PASS/FAIL` 单章行 + 汇总行；`--json` 输出结构化结果（键名 ASCII，适合被脚本或程序消费）；元信息头里的 `字数:` 字段与实际偏差超过 50 字会给出提示，回写时顺手更新它。
注意：部分 Windows 终端可能缺少 `mkdir`/`head` 等 shell 工具，创建目录、读写文件优先用 Python 或专用文件工具完成，不要依赖 shell 内建命令。

## 用户偏好系统

`user-preferences.json`（`webnovel/` 根目录，跨书共享），字段：favoriteGenres / protagonistStyle / emotionMix / toneStyle / typicalVolumeSize / typicalBatchSize / dislikes / creationHistory。
更新时机：每完成一次立项问答静默同步；用户说"记住/忘记/重置偏好"时按指令操作；一本书完结时追加 creationHistory。
偏好用途：问答选项排序与"你的常用"标注、随机生成范围、卷/批次规模的默认值。
