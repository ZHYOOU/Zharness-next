"""Tests for knowledge lexical preparation and splitting. / 知识词法预处理与切分测试。"""

from zharness.config import KnowledgeChunkingSettings
from zharness.knowledge.chunking import split_document
from zharness.knowledge.lexical import lexicalize


def test_lexicalize_expands_chinese_and_code_identifiers() -> None:
    value = lexicalize("混合检索 HTTPServer user_id")

    assert "混" in value
    assert "混合" in value
    assert "httpserver" in value
    assert "http" in value
    assert "server" in value
    assert "user_id" in value
    assert "user" in value


def test_markdown_split_preserves_heading_and_location() -> None:
    text = "# 架构\n\n第一段知识。\n\n## 检索\n\n第二段混合检索知识。"

    chunks = split_document(
        text,
        thread_id="thread-a",
        document_id="doc-a",
        source_uri="/workspace/design.md",
        title="design.md",
        settings=KnowledgeChunkingSettings(
            size_characters=24,
            overlap_characters=4,
        ),
    )

    assert chunks
    assert all(chunk.thread_id == "thread-a" for chunk in chunks)
    assert all(chunk.source_uri == "/workspace/design.md" for chunk in chunks)
    assert any(chunk.heading_path for chunk in chunks)
    assert all(chunk.locator["start_line"] >= 1 for chunk in chunks)
    assert len({chunk.id for chunk in chunks}) == len(chunks)


def test_python_split_uses_language_aware_splitter() -> None:
    text = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"

    chunks = split_document(
        text,
        thread_id="thread-a",
        document_id="doc-code",
        source_uri="/workspace/example.py",
        title="example.py",
        settings=KnowledgeChunkingSettings(
            size_characters=32,
            overlap_characters=4,
        ),
    )

    assert len(chunks) >= 2
    assert any("alpha" in chunk.lexical_text for chunk in chunks)
    assert any("beta" in chunk.lexical_text for chunk in chunks)
