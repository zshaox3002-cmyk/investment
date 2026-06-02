"""Unit tests for knowledge_notes module (边用边学)."""
import pytest
from pathlib import Path
from datetime import date


class TestKnowledgeNotes:
    """Tests for read_notes, append_concept, and search_notes."""

    @pytest.fixture(autouse=True)
    def _patch_paths(self, monkeypatch, tmp_path):
        """Redirect knowledge paths to temp directory for each test."""
        notes_path = tmp_path / "my_investment_notes.md"
        knowledge_dir = tmp_path
        monkeypatch.setattr(
            "investment.agent_tools.knowledge_notes.KNOWLEDGE_DIR",
            knowledge_dir,
        )
        monkeypatch.setattr(
            "investment.agent_tools.knowledge_notes.KNOWLEDGE_NOTES_PATH",
            notes_path,
        )

    # ---- read_notes ----

    def test_read_notes_creates_file_with_header_if_missing(self):
        from investment.agent_tools.knowledge_notes import read_notes

        content = read_notes()

        assert "我的投资学习笔记" in content
        assert "边用边学" in content
        assert date.today().isoformat() in content

    def test_read_notes_returns_existing_content(self):
        from investment.agent_tools.knowledge_notes import (
            KNOWLEDGE_NOTES_PATH,
            read_notes,
        )

        KNOWLEDGE_NOTES_PATH.write_text("# 测试笔记\n\n已有内容。", encoding="utf-8")
        content = read_notes()

        assert "测试笔记" in content
        assert "已有内容" in content

    # ---- append_concept ----

    def test_append_concept_adds_new_entry(self):
        from investment.agent_tools.knowledge_notes import (
            append_concept,
            read_notes,
        )

        success, msg = append_concept(
            concept="风险回报比",
            explanation="冒1块钱风险，预期能赚多少。",
            example="南山铝业上行25%，下行12%，比值为2.08:1。",
            summary="比值越高越好，>2:1才值得考虑。",
        )

        assert success is True
        assert "已记录" in msg
        assert "风险回报比" in msg

        content = read_notes()
        assert "## 风险回报比" in content
        assert "冒1块钱风险" in content
        assert "南山铝业" in content
        assert "比值越高越好" in content

    def test_append_concept_dedup_same_name(self):
        from investment.agent_tools.knowledge_notes import (
            append_concept,
            read_notes,
        )

        # First append
        success1, _ = append_concept(
            concept="PE估值",
            explanation="市盈率。",
            example="茅台PE 30倍。",
        )
        assert success1 is True

        # Second append with same concept name
        success2, msg2 = append_concept(
            concept="PE估值",
            explanation="换个说法。",
            example="另一个例子。",
        )
        assert success2 is False
        assert "已存在" in msg2

        # Content should only have one entry
        content = read_notes()
        assert content.count("## PE估值") == 1

    def test_append_concept_dedup_case_insensitive(self):
        from investment.agent_tools.knowledge_notes import append_concept

        append_concept(concept="DCF估值", explanation="折现现金流。", example="...")
        success, msg = append_concept(
            concept="dcf估值", explanation="换个写法。", example="..."
        )

        assert success is False
        assert "已存在" in msg

    def test_append_concept_dedup_whitespace_normalized(self):
        from investment.agent_tools.knowledge_notes import append_concept

        append_concept(concept="风险 回报  比", explanation="...", example="...")
        success, msg = append_concept(
            concept="风险回报比", explanation="...", example="..."
        )

        assert success is False

    def test_multiple_appends_maintain_format(self):
        from investment.agent_tools.knowledge_notes import (
            append_concept,
            read_notes,
        )

        concepts = [
            ("PE", "市盈率", "茅台30倍", "越低越好"),
            ("PB", "市净率", "银行0.5倍", "<1破净"),
            ("ROE", "净资产收益率", "茅台30%", ">15%优秀"),
        ]
        for c in concepts:
            append_concept(concept=c[0], explanation=c[1], example=c[2], summary=c[3])

        content = read_notes()

        # Each concept should appear exactly once as a heading
        assert content.count("## PE\n") == 1
        assert content.count("## PB\n") == 1
        assert content.count("## ROE\n") == 1

        # Headings should be in order
        pe_pos = content.index("## PE")
        pb_pos = content.index("## PB")
        roe_pos = content.index("## ROE")
        assert pe_pos < pb_pos < roe_pos

    # ---- search_notes ----

    def test_search_notes_finds_matching_concept(self):
        from investment.agent_tools.knowledge_notes import (
            append_concept,
            search_notes,
        )

        append_concept(
            concept="风险回报比",
            explanation="冒1块钱风险，预期能赚多少。",
            example="南山铝业案例。",
        )
        append_concept(
            concept="DCF估值",
            explanation="未来现金流折现。",
            example="茅台案例。",
        )

        result = search_notes("风险")
        assert "风险回报比" in result
        assert "南山铝业案例" in result

    def test_search_notes_no_match_shows_available(self):
        from investment.agent_tools.knowledge_notes import (
            append_concept,
            search_notes,
        )

        append_concept(concept="PE", explanation="市盈率", example="...")

        result = search_notes("ROE")
        assert "没有找到" in result
        assert "PE" in result  # should list available concepts

    def test_search_notes_empty_file(self):
        from investment.agent_tools.knowledge_notes import search_notes

        result = search_notes("任何概念")
        assert "还没有任何概念" in result
