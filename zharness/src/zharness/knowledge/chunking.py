"""Document splitting through langchain-text-splitters. / 通过 langchain-text-splitters 切分文档。"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import (
    Language,
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from zharness.config import KnowledgeChunkingSettings
from zharness.knowledge.lexical import lexicalize
from zharness.knowledge.types import KnowledgeChunk

_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdx"}
_LANGUAGE_BY_SUFFIX = {
    ".c": Language.C,
    ".cc": Language.CPP,
    ".cpp": Language.CPP,
    ".cxx": Language.CPP,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".js": Language.JS,
    ".jsx": Language.JS,
    ".py": Language.PYTHON,
    ".rs": Language.RUST,
    ".ts": Language.TS,
    ".tsx": Language.TS,
}
_TEXT_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ". ",
    "! ",
    "? ",
    "；",
    "; ",
    "，",
    ", ",
    " ",
    "",
]
_MARKDOWN_HEADERS = [("#" * level, f"h{level}") for level in range(1, 7)]


def split_document(
    text: str,
    *,
    thread_id: str,
    document_id: str,
    source_uri: str,
    title: str,
    settings: KnowledgeChunkingSettings,
) -> list[KnowledgeChunk]:
    """Split one document and enrich every chunk with stable source locations.

    切分单个文档，并为每个切片补充稳定的来源位置。
    """
    if not text.strip():
        return []
    suffix = PurePosixPath(source_uri).suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        pieces = _split_markdown(text, settings)
    elif suffix in _LANGUAGE_BY_SUFFIX:
        pieces = _split_code(text, _LANGUAGE_BY_SUFFIX[suffix], settings)
    else:
        pieces = _split_text(text, settings)

    chunks: list[KnowledgeChunk] = []
    search_cursor = 0
    for ordinal, piece in enumerate(
        piece for piece in pieces if piece.page_content.strip()
    ):
        content = piece.page_content.strip()
        start = _absolute_start(text, content, piece.metadata, search_cursor)
        end = min(len(text), start + len(content))
        search_cursor = max(search_cursor, start + 1)
        heading_path = tuple(
            str(piece.metadata[key])
            for key in (f"h{level}" for level in range(1, 7))
            if piece.metadata.get(key)
        )
        locator = {
            "start_char": start,
            "end_char": end,
            "start_line": text.count("\n", 0, start) + 1,
            "end_line": text.count("\n", 0, end) + 1,
        }
        if heading_path:
            locator["heading"] = " / ".join(heading_path)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunk_id = hashlib.sha256(
            f"{document_id}:{ordinal}:{digest}".encode()
        ).hexdigest()
        chunks.append(
            KnowledgeChunk(
                id=chunk_id,
                document_id=document_id,
                thread_id=thread_id,
                ordinal=ordinal,
                content=content,
                lexical_text=lexicalize(f"{title} {source_uri} {content}"),
                title=title,
                source_uri=source_uri,
                heading_path=heading_path,
                locator=locator,
                content_hash=digest,
            )
        )
    return chunks


def _recursive_splitter(
    settings: KnowledgeChunkingSettings,
    *,
    separators: list[str] | None = None,
) -> RecursiveCharacterTextSplitter:
    """Create the configured recursive fallback splitter. / 创建已配置的递归兜底切分器。"""
    kwargs: dict[str, Any] = {
        "chunk_size": settings.size_characters,
        "chunk_overlap": settings.overlap_characters,
        "add_start_index": settings.add_start_index,
    }
    if separators is not None:
        kwargs["separators"] = separators
    return RecursiveCharacterTextSplitter(**kwargs)


def _split_text(
    text: str,
    settings: KnowledgeChunkingSettings,
) -> list[Document]:
    """Split generic multilingual text. / 切分通用多语言文本。"""
    return _recursive_splitter(settings, separators=_TEXT_SEPARATORS).create_documents(
        [text]
    )


def _split_code(
    text: str,
    language: Language,
    settings: KnowledgeChunkingSettings,
) -> list[Document]:
    """Split source code with LangChain language separators. / 使用 LangChain 语言分隔符切分源代码。"""
    splitter = RecursiveCharacterTextSplitter.from_language(
        language,
        chunk_size=settings.size_characters,
        chunk_overlap=settings.overlap_characters,
        add_start_index=settings.add_start_index,
    )
    return splitter.create_documents([text])


def _split_markdown(
    text: str,
    settings: KnowledgeChunkingSettings,
) -> list[Document]:
    """Split Markdown by headings and then by configured length. / 先按标题、再按配置长度切分 Markdown。"""
    sections = MarkdownHeaderTextSplitter(
        headers_to_split_on=_MARKDOWN_HEADERS,
        strip_headers=False,
    ).split_text(text)
    splitter = _recursive_splitter(settings, separators=_TEXT_SEPARATORS)
    pieces: list[Document] = []
    section_cursor = 0
    for section in sections:
        section_start = _find_section_start(text, section.page_content, section_cursor)
        section_cursor = max(section_cursor, section_start + 1)
        for piece in splitter.split_documents([section]):
            relative = int(piece.metadata.get("start_index", 0))
            piece.metadata["absolute_start_index"] = section_start + relative
            pieces.append(piece)
    return pieces


def _find_section_start(text: str, section: str, cursor: int) -> int:
    """Locate a Markdown section monotonically in its source. / 在 Markdown 原文中单调定位章节。"""
    first_line = next(
        (line.strip() for line in section.splitlines() if line.strip()), ""
    )
    position = text.find(first_line, cursor) if first_line else -1
    if position < 0 and first_line:
        position = text.find(first_line)
    return max(0, position if position >= 0 else cursor)


def _absolute_start(
    text: str,
    content: str,
    metadata: dict[str, Any],
    cursor: int,
) -> int:
    """Resolve an absolute chunk offset with a monotonic fallback. / 使用单调兜底逻辑解析切片绝对偏移。"""
    if "absolute_start_index" in metadata:
        return max(0, int(metadata["absolute_start_index"]))
    if "start_index" in metadata:
        return max(0, int(metadata["start_index"]))
    position = text.find(content, cursor)
    if position < 0:
        position = text.find(content)
    return max(0, position if position >= 0 else cursor)
