"""describe_skill — deferred skill metadata retrieval at runtime.

Builds the ``describe_skill`` tool as a closure over a :class:`LocalSkillStorage`.
The tool returns structured metadata (description, allowed tools, file location)
so the LLM can decide whether to ``read_file`` the full SKILL.md.

describe_skill——运行时的延迟技能元数据获取。将 ``describe_skill`` 工具构建为
:class:`LocalSkillStorage` 上的闭包。工具返回结构化元数据（描述、允许的工具、文件位置），
供 LLM 决定是否通过 ``read_file`` 读取完整 SKILL.md。
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from langchain.tools import BaseTool

    from zharness.skills.storage import LocalSkillStorage
from zharness.skills.catalog import SkillCatalog
from zharness.skills.constants import DEFAULT_SKILLS_CONTAINER_PATH
from zharness.skills.types import Skill, SkillCategory


def build_describe_skill_tool(
    storage: LocalSkillStorage,
    *,
    container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
) -> BaseTool:
    """Build the ``describe_skill`` tool as a closure over *storage*.

    The catalog is rebuilt on every call so newly installed or edited skills
    are reflected immediately.

    将 ``describe_skill`` 工具构建为 *storage* 上的闭包。每次调用都会重建目录，
    以便新安装或编辑的技能立即生效。
    """

    @tool
    def describe_skill(name: str) -> str:
        """Fetch usage metadata for installed skills so you can decide whether to load them.

        Skills appear by name in <skill_index> in the system prompt. Until
        fetched, only the name is known. This tool matches a query against
        installed skills and returns their full metadata — description, allowed
        tools, and file location — so you can decide whether to load the
        SKILL.md via read_file.

        Query forms:
          - "select:data-analysis,deep-research" -- fetch these exact skills (no cap)
          - "chart visualization" -- keyword search, best matches (up to 5)
          - "+podcast gen" -- require "podcast" in the name, rank by remaining terms (up to 5)

        获取已安装技能的使用元数据，以便决定是否加载它们。技能按名称出现在系统提示词的
        <skill_index> 中。在获取之前只知道名称。本工具将查询与已安装技能匹配，并返回它们的
        完整元数据——描述、允许的工具和文件位置——以便你决定是否通过 read_file 加载 SKILL.md。

        查询形式：
          - "select:data-analysis,deep-research" — 获取这些精确技能（无上限）。
          - "chart visualization" — 关键词搜索，最佳匹配（至多 5 个）。
          - "+podcast gen" — 名称必须包含 "podcast"，按剩余词排序（至多 5 个）。
        """
        catalog = SkillCatalog(tuple(storage.load_skills()))
        matched = catalog.search(name)
        if not matched:
            return f"No skills matched: {name}"
        return _render_skill_metadata(matched, container_base_path)

    return describe_skill


def get_skill_index_prompt_section(
    *,
    skill_names: frozenset[str] = frozenset(),
    container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
) -> str:
    """Generate ``<skill_system>`` with a name-only ``<skill_index>``.

    The agent knows what exists and can use ``describe_skill`` to load metadata.

    Returns an empty string when there are no skills.

    生成仅含技能名的 ``<skill_index>`` 的 ``<skill_system>`` 段落。Agent 知道有哪些技能，
    并可用 ``describe_skill`` 加载元数据。没有技能时返回空字符串。
    """
    if not skill_names:
        return ""

    names = ", ".join(html.escape(name, quote=False) for name in sorted(skill_names))

    return f"""<skill_system>
You have access to skills that provide optimized workflows for specific tasks.

**Skill Discovery:**
1. Check <skill_index> for a skill name that matches your task
2. Call describe_skill(name) to fetch its description and capabilities
3. If the skill matches, call read_file on the returned location to load full instructions
4. Follow the skill's instructions precisely

<skill_index>
{names}
</skill_index>

Skills are located at: {container_base_path}
</skill_system>"""


def _render_skill_metadata(skills: list[Skill], container_base_path: str) -> str:
    """Render structured metadata for a list of matched skills. / 渲染一组匹配技能的结构化元数据。"""
    blocks: list[str] = []
    for s in skills:
        mutability = (
            "[user, editable]" if s.category == SkillCategory.USER else "[built-in]"
        )
        tools_line = (
            "(all)"
            if s.allowed_tools is None
            else ", ".join(s.allowed_tools) or "(none)"
        )
        location = s.get_container_file_path(container_base_path)
        # name/description/allowed-tools come from untrusted frontmatter; escape
        # so a value cannot forge a framework tag in the describe_skill output.
        #
        # name/description/allowed-tools 来自不可信的 frontmatter；转义后，值就无法
        # 在 describe_skill 输出中伪造框架标签。
        name = html.escape(s.name, quote=False)
        description = html.escape(s.description, quote=False)
        tools = html.escape(tools_line, quote=False)
        loc = html.escape(location, quote=False)
        blocks.append(
            f"## Skill: {name}\n"
            f"- Description: {description} {mutability}\n"
            f"- Allowed tools: {tools}\n"
            f"- Location: {loc}"
        )
    return "\n\n".join(blocks)
