# -*- coding: utf-8 -*-
"""章节文件的读写口径 —— 全 skill 的**唯一实现**。

## 为什么单独抽出来

`references/flows/shared-infrastructure.md` 记过一次实测事故：同一种清洗逻辑在
`split_chapters` / 合并稿脚本 / 终检脚本里**各写了一遍**，写得还不一样，
结果终检把**正确的成品判成「不一致 ❌」**。那种"假失败"比漏检更费时间——
人会去改本来没问题的东西。

2026-10-07 又踩了同一类坑的第二形态：章节文件按 `chapter-template.md` 的要求以
`<!-- 元信息块 -->` 开头，而 `split_chapters` / `delivery_check` 都用

    re.match(r"^#\\s*(.+?)\\s*\\n(.*)$", text, re.S)

抓标题。`re.match` 只从**字符串最开头**匹配，文件第一行是 `<!--`，于是永远匹配不上：
整个元信息块被当成正文写进成品 txt、`# 第N章 标题` 这行也一起漏了出去、章节名只剩「第一章」。

**所以本模块把「头部识别」也收成一个实现。** 任何需要读章节文件的脚本都 import 它，
不要再各写一份正则。参见 `manuscript_check.py`（体检）与 `split_chapters.py`（导出）。

## 章节文件的规范形态（与 `references/templates/chapter-template.md` 一致）

    第 1 行起   <!--            ← 元信息块必须是文件第一行
                章号： 0011
                标题： 赛程表
                卷/批次： 第 1 卷 / 第 1 批次
                情绪标签： 爽
                伏笔操作： payoff:F06, advance:F07
                字数： 1939
                -->
                （空行）
                # 第0011章 赛程表   ← H1 必须是规范形式，标题与元信息块一致
                （空行）
                正文……

**向后兼容**：没有元信息块的老文件（短故事形态常见）也能解析——
此时以第一个 `# ` 行为标题，之前的内容当正文。
"""
from __future__ import annotations

import re

# 元信息块：文件**开头**的 HTML 注释（允许前面有空白/BOM，已在 parse 里剥掉）
META_HEAD_RE = re.compile(r"^[ \t]*<!--(.*?)-->[ \t]*\n?", re.S)
H1_RE = re.compile(r"^\s*#\s*(.+?)[ \t]*\n(.*)$", re.S)
CHAPTER_NUM_RE = re.compile(r"第\s*([0-9〇零一二三四五六七八九十百千]+)\s*章")

#: 元信息块必须齐的六个键（缺任何一个都算头部损坏）
META_KEYS = ("章号", "标题", "卷/批次", "情绪标签", "伏笔操作", "字数")

# ---------------------------------------------------------------------------
# 字数口径 —— **全 skill 唯一实现**
# ---------------------------------------------------------------------------
#: 汉字（含扩展 A 区与兼容区）
CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
#: 中文标点（含全角形式、弯引号、省略号、破折号、间隔号）
CN_PUNCT = re.compile(r"[\u3000-\u303f\uff00-\uffef\u2018\u2019\u201c\u201d\u2026\u2014\u00b7]")
#: HTML 注释（元信息块），不计入字数
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

_CN_DIGITS = "零一二三四五六七八九十"
_ARABIC = re.compile(r"^\d+$")


def count_cjk(text: str) -> int:
    """只数汉字（去注释）。用于"汉字数"这一列。"""
    return len(CJK.findall(COMMENT.sub("", text)))


def count_chars(text: str) -> int:
    """**平台计字口径**：CJK 汉字 + 中文标点，去空白，HTML 注释不计。

    ⚠️ 传参是**章节文件全文**（含 `# 第N章 标题` 行），标题行**计入**。
    这一点必须全 skill 统一，否则各处会数出不同的总字数——实测踩过：

    | 调用方 | 口径 | 同一批 12 章的总数 |
    | --- | --- | --- |
    | `check_wordcount.py` | CJK + 中文标点（含 H1） | 22334 |
    | `split_chapters.py`（旧） | 全文**所有**非空白字符 | 22449 |

    差 115 = 正文里 ASCII 数字/字母/半角符号（旧口径数了，平台不数）+ 标题行。
    **`chapter_io.count_chars` 现在只有一个实现，三处调用它必须永远相等。**

    > 口径本身的取舍：平台后台不计章节名，这里把 H1 计进来是有意的——
    > ① 与历史记录（`计划.json` / `写作计划.json` 的 `wordCount`）保持一致，避免全项目重算；
    > ② H1 一旦被吞（第 7 章那次头部损坏），字数会立刻偏小，反而成了损伤信号。
    > **差额 = 标题字数（约 5-7 字/章），不要为此去改已记录的数字。**
    """
    body = COMMENT.sub("", text)
    return len(CJK.findall(body)) + len(CN_PUNCT.findall(body))


def cn_num(n: int) -> str:
    """1 -> 一, 10 -> 十, 11 -> 十一, 21 -> 二十一"""
    if n <= 10:
        return "十" if n == 10 else _CN_DIGITS[n]
    if n < 20:
        return "十" + _CN_DIGITS[n - 10]
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def to_int(s: str) -> int | None:
    """把章号字符串统一成 int。支持 '0011' / '11' / '十一' / '二十一'。"""
    s = (s or "").strip()
    if not s:
        return None
    if _ARABIC.match(s):
        return int(s)
    if not all(ch in _CN_DIGITS for ch in s):
        return None
    total, section, number = 0, 0, 0
    for ch in s:
        if ch == "十":
            section = (section or 1) * 10
            total += section
            section = 0
        else:
            number = _CN_DIGITS.index(ch)
            section = section * 10 + number
            if section >= 10:          # 处理「二一」这类写法
                total += section
                section = 0
    return total + section


def split_meta_kv(raw: str) -> dict:
    """解析元信息块正文，容忍全角「：」与半角「:」。"""
    meta: dict = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        key, sep, val = line.partition("：")
        if not sep:
            key, sep, val = line.partition(":")
        if sep:
            meta[key.strip()] = val.strip()
    return meta


def parse(text: str) -> dict:
    """把一个章节文件的全文解析成结构化结果。

    返回 dict：
        meta      : dict[str, str]  元信息键值（无块时为空 dict）
        has_meta  : bool            是否存在元信息块
        title     : str             H1 里章号之后的部分（如 "赛程表"）
        h1        : str             H1 全文（如 "# 第0011章 赛程表"）
        no        : int | None      H1 里解析出的章号
        body      : str             H1 之后的正文（未清洗）
        rest      : str             元信息块之后、含 H1 的全文
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")

    meta: dict = {}
    has_meta = False
    rest = t
    m = META_HEAD_RE.match(t)
    if m:
        has_meta = True
        meta = split_meta_kv(m.group(1))
        rest = t[m.end():]

    h1, title, no, body = "", "", None, rest
    hm = H1_RE.match(rest)
    if hm:
        h1 = "# " + hm.group(1).strip()
        raw_head = hm.group(1).strip()
        body = hm.group(2)
        nm = CHAPTER_NUM_RE.search(raw_head)
        if nm:
            no = to_int(nm.group(1))
            title = raw_head[nm.end():].strip()
        else:
            title = raw_head

    return {
        "meta": meta,
        "has_meta": has_meta,
        "h1": h1,
        "title": title,
        "no": no,
        "body": body,
        "rest": rest,
    }


def parse_file(path: str) -> dict:
    with open(path, encoding="utf-8") as fp:
        return parse(fp.read())


def clean_body(body: str, keep_md: bool = False) -> str:
    """导出用的正文清洗 —— 与 `split_chapters` 的成品口径一致。

    ⚠️ 星号要**一次清干净**（`re.sub(r"\\*+", "")`，不是 `replace("**","")`）：
    `**加粗**` 和 `*斜体*` 都会在平台编辑器里显示成字面星号。
    实测漏掉单星号斜体（某章 `*犯者三，当归一。*`）会在成品里留下 2 个孤立 `*`。
    """
    if not keep_md:
        body = re.sub(r"\*+", "", body)
        body = body.replace("__", "")
    lines = [ln.rstrip() for ln in body.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "" and out and out[-1] == "":
            continue
        out.append(ln)
    return "\n".join(out).strip("\n")


def norm_for_compare(text: str, keep_md: bool = False) -> str:
    """用于「成品 vs 源文件」逐字比对：先剥头部，再走同一套清洗，最后去空白。

    `delivery_check.py` 与本模块必须永远一致 —— 这也是不要再各写一份的理由。
    """
    p = parse(text)
    return re.sub(r"\s", "", clean_body(p["body"], keep_md))
