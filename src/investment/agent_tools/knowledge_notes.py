"""Knowledge notes utility for learn-as-you-use capability.

极简实现：提供 read_notes() 和 append_concept() 两个函数，
用于管理 knowledge/my_investment_notes.md 学习笔记文件。
"""
from __future__ import annotations
from pathlib import Path
from datetime import date

from investment.core.settings import KNOWLEDGE_DIR, KNOWLEDGE_NOTES_PATH

_HEADER = """# 我的投资学习笔记

> 边用边学，每遇到一个新概念就记一笔。不用背，回头查就行。
> 最后更新：{date}

---
"""

_ENTRY_TEMPLATE = """
## {concept}

- **学习日期**：{learning_date}
- **通俗解释**：{explanation}
- **实际案例**：{example}
- **一句话总结**：{summary}
"""


def _ensure_file() -> Path:
    """确保知识笔记文件存在，不存在则创建带模板头的空文件。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    if not KNOWLEDGE_NOTES_PATH.exists():
        KNOWLEDGE_NOTES_PATH.write_text(
            _HEADER.format(date=date.today().isoformat()), encoding="utf-8"
        )
    return KNOWLEDGE_NOTES_PATH


def read_notes() -> str:
    """读取完整的学习笔记文件内容。"""
    _ensure_file()
    return KNOWLEDGE_NOTES_PATH.read_text(encoding="utf-8")


def append_concept(
    concept: str,
    explanation: str,
    example: str,
    summary: str = "",
    learning_date: str | None = None,
) -> tuple[bool, str]:
    """追加一个新概念条目到笔记文件。

    自动去重：如果同名概念（大小写不敏感、空白归一化）已存在，
    返回 (False, message) 而不追加。

    Returns:
        (success, message) — success=True 表示已追加，False 表示已存在。
    """
    _ensure_file()
    content = KNOWLEDGE_NOTES_PATH.read_text(encoding="utf-8")

    # 去重检查：查找已存在的同名 ## heading
    normalized_new = _normalize(concept)
    for line in content.splitlines():
        if line.startswith("## ") and _normalize(line[3:]) == normalized_new:
            return False, f"概念「{concept}」已存在于学习笔记中，跳过重复记录。"

    if learning_date is None:
        learning_date = date.today().isoformat()

    entry = _ENTRY_TEMPLATE.format(
        concept=concept,
        learning_date=learning_date,
        explanation=explanation,
        example=example,
        summary=summary,
    )

    with KNOWLEDGE_NOTES_PATH.open("a", encoding="utf-8") as f:
        f.write(entry)

    return True, f"已记录概念「{concept}」到学习笔记。"


def search_notes(query: str) -> str:
    """在笔记中搜索匹配的概念条目。

    简单实现：查找 ## heading 包含 query 的条目，返回该条目全文。
    如果没有匹配，返回提示信息。
    """
    _ensure_file()
    content = KNOWLEDGE_NOTES_PATH.read_text(encoding="utf-8")

    normalized_query = _normalize(query)
    sections = content.split("\n## ")

    # 第一个 section 是文件头（# 标题部分），跳过
    if len(sections) <= 1:
        return f"学习笔记中还没有任何概念记录。试着在分析报告里追问一个术语吧！"

    results = []
    for section in sections[1:]:
        # section 格式: "概念名\n- **学习日期**..."
        heading_line = section.split("\n")[0].strip()
        if normalized_query in _normalize(heading_line):
            results.append("## " + section.strip())

    if not results:
        return f"在笔记中没有找到与「{query}」相关的概念。已记录的概念有：{_list_all_concepts(content)}"

    return "\n\n".join(results)


def _normalize(text: str) -> str:
    """去空白 + 小写，用于去重比较（中文不依赖空格分词）。"""
    return "".join(text.lower().split())


def _list_all_concepts(content: str) -> str:
    """列出笔记中所有概念名称。"""
    concepts = []
    for line in content.splitlines():
        if line.startswith("## ") and not line.startswith("## "):
            # 排除文件标题（以 # 开头而非 ## 开头）
            pass
        if line.startswith("## "):
            name = line[3:].strip()
            if name:
                concepts.append(name)
    return "、".join(concepts) if concepts else "（暂无）"
