"""Unit tests for migration/utils.py — parse_frontmatter."""
import pytest
from investment.migration.utils import parse_frontmatter


def test_parse_frontmatter_basic():
    text = "---\ncode: 600219\nname: 南山铝业\n---\n正文内容"
    result = parse_frontmatter(text)
    assert result["code"] == 600219
    assert result["name"] == "南山铝业"


def test_parse_frontmatter_no_frontmatter():
    text = "# 标题\n正文内容"
    result = parse_frontmatter(text)
    assert result == {}


def test_parse_frontmatter_empty_block():
    text = "---\n---\n正文"
    result = parse_frontmatter(text)
    assert result == {}


def test_parse_frontmatter_multiline():
    text = "---\ntags:\n  - 新能源\n  - A股\nscore: 3.5\n---\n"
    result = parse_frontmatter(text)
    assert result["tags"] == ["新能源", "A股"]
    assert result["score"] == pytest.approx(3.5)


def test_parse_frontmatter_invalid_yaml():
    text = "---\n: invalid: yaml: [\n---\n正文"
    result = parse_frontmatter(text)
    assert result == {}
