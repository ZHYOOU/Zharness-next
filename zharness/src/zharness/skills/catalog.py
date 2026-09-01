"""Skill catalog — deferred skill discovery at runtime.

An immutable, searchable catalog that lets the LLM discover skill metadata on
demand rather than having every skill's full description baked into the system
prompt.

The agent sees skill names in ``<skill_index>`` but cannot read their metadata
until it calls ``describe_skill``.  This keeps the system prompt compact and
prefix-cache friendly while still giving the model autonomous skill discovery.

技能目录——运行时的延迟技能发现。一个不可变的、可搜索的目录，让 LLM 按需发现技能元数据，
而不是把所有技能的完整描述都硬编码进系统提示词。

Agent 在 ``<skill_index>`` 中只能看到技能名，直到调用 ``describe_skill`` 才能读取元数据。
这保持了系统提示词的紧凑与前缀缓存友好，同时仍让模型具备自主技能发现能力。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zharness.skills.types import Skill

MAX_RESULTS = 5


def _compile_catalog_regex(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern`` case-insensitively, falling back to literal match. / 不区分大小写地编译 ``pattern``，失败时退化为字面匹配。"""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


@dataclass(frozen=True)
class SkillCatalog:
    """Immutable catalog of skills. Pure search, no mutation.

    Query forms:

    - ``"select:data-analysis,deep-research"`` — exact match by name.
    - ``"+podcast gen"`` — require *podcast* in the name, rank by *gen*.
    - ``"chart visualization"`` — regex match on name + description.

    不可变的技能目录。只做搜索，不做修改。

    查询形式：

    - ``"select:data-analysis,deep-research"`` — 按名称精确匹配。
    - ``"+podcast gen"`` — 名称中必须包含 *podcast*，按 *gen* 排序。
    - ``"chart visualization"`` — 对名称加描述做正则匹配。
    """

    skills: tuple[Skill, ...]

    def search(self, query: str) -> list[Skill]:
        """Match *query* against skill names and descriptions.

        Returns at most :data:`MAX_RESULTS` skills, ranked by relevance.

        用 *query* 匹配技能名称与描述。至多返回 :data:`MAX_RESULTS` 个技能，按相关度排序。
        """
        query = query.strip()
        if not query:
            return []

        # ── Exact selection: match by exact name. / 精确选择：按名称精确匹配。
        if query.startswith("select:"):
            wanted = {n.strip() for n in query[7:].split(",")}
            return [s for s in self.skills if s.name in wanted]

        # ── Required-prefix search: require a token in the name. / 必需前缀搜索：名称中必须包含指定词。
        if query.startswith("+"):
            parts = query[1:].split(None, 1)
            if not parts:
                return []  # bare "+" with no required token / 单独的 "+" 没有必需词
            required = parts[0].lower()
            candidates = [s for s in self.skills if required in s.name.lower()]
            if len(parts) > 1:
                pattern = _compile_catalog_regex(parts[1])
                candidates.sort(
                    key=lambda s: _catalog_regex_score(pattern, s),
                    reverse=True,
                )
            return candidates[:MAX_RESULTS]

        # ── Free-text regex search. / 全文正则搜索。
        regex = _compile_catalog_regex(query)
        scored: list[tuple[int, Skill]] = []
        for s in self.skills:
            searchable = f"{s.name} {s.description or ''}"
            if regex.search(searchable):
                # Name match scores higher than description-only match. / 名称匹配的得分高于仅描述匹配。
                scored.append((2 if regex.search(s.name) else 1, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored][:MAX_RESULTS]


def _catalog_regex_score(pattern: re.Pattern[str], s: Skill) -> int:
    """Count regex hits across name + description for ranking. / 统计名称与描述上的正则命中次数用于排序。"""
    return len(pattern.findall(f"{s.name} {s.description or ''}"))