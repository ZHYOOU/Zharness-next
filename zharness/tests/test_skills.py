"""Tests for the skill system core: parsing, validation, catalog, storage,
deferred discovery, and the sandbox skills mount.

测试技能系统核心：解析、校验、目录、存储、延迟发现以及沙箱技能挂载。
"""

from pathlib import Path

import pytest
from zharness.sandbox.local import (
    LocalSandbox,
    LocalSandboxManager,
    LocalSandboxSettings,
)
from zharness.sandbox.workspace import SandboxWorkspace
from zharness.skills.catalog import SkillCatalog
from zharness.skills.constants import DEFAULT_SKILLS_CONTAINER_PATH, SKILL_MD_FILE
from zharness.skills.describe import (
    build_describe_skill_tool,
    get_skill_index_prompt_section,
)
from zharness.skills.parser import parse_skill_file
from zharness.skills.storage import LocalSkillStorage, skills_root_path
from zharness.skills.types import Skill, SkillCategory
from zharness.skills.validation import _validate_skill_frontmatter, validate_skill_name

VALID_SKILL_MD = (
    "---\n"
    "name: deep-research\n"
    "description: Do systematic web research.\n"
    "license: MIT\n"
    "---\n\n"
    "# Deep Research\n\n"
    "Follow these steps...\n"
)


def _write_skill(
    root: Path, category: str, name: str, content: str | None = None
) -> Path:
    if content is None:
        content = (
            "---\n"
            f"name: {name}\n"
            f"description: Workflow for {name}.\n"
            "license: MIT\n"
            "---\n\n"
            f"# {name}\n\n"
            "Follow these steps...\n"
        )
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / SKILL_MD_FILE
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


# ---------------------------------------------------------------------------
# Parser / 解析器
# ---------------------------------------------------------------------------


def test_parse_valid_skill(tmp_path: Path) -> None:
    skill_file = _write_skill(tmp_path, "public", "deep-research", VALID_SKILL_MD)

    skill = parse_skill_file(skill_file, SkillCategory.PUBLIC, Path("deep-research"))

    assert skill is not None
    assert skill.name == "deep-research"
    assert skill.description == "Do systematic web research."
    assert skill.license == "MIT"
    assert skill.category == SkillCategory.PUBLIC
    assert skill.enabled is True
    assert skill.get_container_file_path() == (
        f"{DEFAULT_SKILLS_CONTAINER_PATH}/public/deep-research/SKILL.md"
    )


@pytest.mark.parametrize(
    "content",
    [
        "no frontmatter here",
        "---\nnot: yaml: [broken\n---\nbody",
        "---\nname: only-a-name\n---\nbody",
        "---\ndescription: only-a-description\n---\nbody",
    ],
)
def test_parse_rejects_invalid_skill(tmp_path: Path, content: str) -> None:
    skill_file = _write_skill(tmp_path, "public", "broken", content)

    assert parse_skill_file(skill_file, SkillCategory.PUBLIC, Path("broken")) is None


def test_parse_allowed_tools_and_required_secrets(tmp_path: Path) -> None:
    content = (
        "---\n"
        "name: guarded\n"
        "description: Uses a restricted toolset.\n"
        "allowed-tools:\n"
        "  - read_file\n"
        "  - execute_command\n"
        "required-secrets:\n"
        "  - API_KEY\n"
        "  - name: OPTIONAL_KEY\n"
        "    optional: true\n"
        "---\n"
        "# Guarded\n"
    )
    skill_file = _write_skill(tmp_path, "user", "guarded", content)

    skill = parse_skill_file(skill_file, SkillCategory.USER, Path("guarded"))

    assert skill is not None
    assert skill.allowed_tools == ("read_file", "execute_command")
    assert [(s.name, s.optional) for s in skill.required_secrets] == [
        ("API_KEY", False),
        ("OPTIONAL_KEY", True),
    ]


# ---------------------------------------------------------------------------
# Validation / 校验
# ---------------------------------------------------------------------------


def test_validate_skill_name() -> None:
    assert validate_skill_name("  deep-research  ") == "deep-research"
    for bad in ("Deep Research", "deep_research", "-bad", "bad--name", "a" * 65):
        with pytest.raises(ValueError):
            validate_skill_name(bad)


def test_validate_skill_frontmatter_rejects_html_in_description(tmp_path: Path) -> None:
    content = (
        "---\n"
        "name: evil\n"
        "description: Contains <script>alert(1)</script>.\n"
        "---\n"
        "# Evil\n"
    )
    _write_skill(tmp_path, "public", "evil", content)

    is_valid, message, _ = _validate_skill_frontmatter(tmp_path / "public" / "evil")

    assert is_valid is False
    assert "angle brackets" in message


def test_validate_skill_frontmatter_rejects_unexpected_keys(tmp_path: Path) -> None:
    content = "---\nname: rogue\ndescription: Fine.\nmalicious-key: 1\n---\n# Rogue\n"
    _write_skill(tmp_path, "public", "rogue", content)

    is_valid, message, _ = _validate_skill_frontmatter(tmp_path / "public" / "rogue")

    assert is_valid is False
    assert "Unexpected key" in message


def test_validate_skill_frontmatter_rejects_empty_description(tmp_path: Path) -> None:
    content = '---\nname: empty-description\ndescription: ""\n---\n# Empty\n'
    _write_skill(tmp_path, "public", "empty-description", content)

    is_valid, message, _ = _validate_skill_frontmatter(
        tmp_path / "public" / "empty-description"
    )

    assert is_valid is False
    assert "cannot be empty" in message


# ---------------------------------------------------------------------------
# Catalog / 目录
# ---------------------------------------------------------------------------


def _catalog_with_skills() -> SkillCatalog:
    base = Path("/skills")
    skills = [
        Skill(
            name="data-analysis",
            description="Analyze Excel and CSV files.",
            license=None,
            skill_dir=base / "public" / "data-analysis",
            skill_file=base / "public" / "data-analysis" / SKILL_MD_FILE,
            relative_path=Path("data-analysis"),
            category=SkillCategory.PUBLIC,
        ),
        Skill(
            name="deep-research",
            description="Research any topic across the web.",
            license=None,
            skill_dir=base / "public" / "deep-research",
            skill_file=base / "public" / "deep-research" / SKILL_MD_FILE,
            relative_path=Path("deep-research"),
            category=SkillCategory.PUBLIC,
        ),
    ]
    return SkillCatalog(tuple(skills))


def test_catalog_exact_selection() -> None:
    catalog = _catalog_with_skills()

    assert [s.name for s in catalog.search("select:deep-research")] == ["deep-research"]
    assert [s.name for s in catalog.search("select:data-analysis,deep-research")] == [
        "data-analysis",
        "deep-research",
    ]
    assert catalog.search("select:unknown") == []


def test_catalog_required_prefix_search() -> None:
    catalog = _catalog_with_skills()

    assert [s.name for s in catalog.search("+data")] == ["data-analysis"]
    assert [s.name for s in catalog.search("+deep web")] == ["deep-research"]


def test_catalog_free_text_search_ranks_name_over_description() -> None:
    base = Path("/skills")
    skills = [
        Skill(
            name="deep-research",
            description="Research any topic across the web.",
            license=None,
            skill_dir=base / "public" / "deep-research",
            skill_file=base / "public" / "deep-research" / SKILL_MD_FILE,
            relative_path=Path("deep-research"),
            category=SkillCategory.PUBLIC,
        ),
        Skill(
            name="data-analysis",
            description="Research and analyze Excel and CSV files.",
            license=None,
            skill_dir=base / "public" / "data-analysis",
            skill_file=base / "public" / "data-analysis" / SKILL_MD_FILE,
            relative_path=Path("data-analysis"),
            category=SkillCategory.PUBLIC,
        ),
    ]
    catalog = SkillCatalog(tuple(skills))

    results = catalog.search("research")
    # Name match (deep-research) ranks above description-only match. / 名称匹配（deep-research）的排名高于仅描述匹配。
    assert [s.name for s in results] == ["deep-research", "data-analysis"]


def test_catalog_empty_query() -> None:
    assert _catalog_with_skills().search("  ") == []


# ---------------------------------------------------------------------------
# Storage / 存储
# ---------------------------------------------------------------------------


def test_load_skills_discovers_public_and_user(tmp_path: Path) -> None:
    _write_skill(tmp_path, "public", "deep-research")
    _write_skill(tmp_path, "public", "data-analysis")
    _write_skill(tmp_path, "user", "my-helper")
    storage = LocalSkillStorage(host_path=tmp_path)

    skills = storage.load_skills()

    assert [s.name for s in skills] == ["data-analysis", "deep-research", "my-helper"]
    assert skills[0].category == SkillCategory.PUBLIC
    assert skills[2].category == SkillCategory.USER


def test_load_skills_skips_nested_skill_packages_and_hidden_dirs(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "public", "data-analysis")
    skill_dir = tmp_path / "public" / "data-analysis"
    # A nested SKILL.md under a package belongs to that package's fixtures. / 技能包下嵌套的 SKILL.md 属于该包的测试夹具。
    nested = skill_dir / "fixtures" / "sample"
    nested.mkdir(parents=True)
    (nested / SKILL_MD_FILE).write_text(VALID_SKILL_MD, encoding="utf-8")
    # Hidden directories are never scanned. / 永远不扫描隐藏目录。
    _write_skill(tmp_path, "public", ".hidden-skill")

    storage = LocalSkillStorage(host_path=tmp_path)

    assert [s.name for s in storage.load_skills()] == ["data-analysis"]


def test_load_skills_without_root_is_empty(tmp_path: Path) -> None:
    storage = LocalSkillStorage(host_path=tmp_path / "missing")

    assert storage.load_skills() == []


def test_load_skills_does_not_follow_cyclic_directory_symlinks(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "user", "my-helper")
    cycle = tmp_path / "user" / "cycle"
    cycle.symlink_to(tmp_path / "user", target_is_directory=True)

    skills = LocalSkillStorage(host_path=tmp_path).load_skills()

    assert [skill.name for skill in skills] == ["my-helper"]


def test_load_skills_does_not_follow_symlinked_category(tmp_path: Path) -> None:
    external = tmp_path / "external"
    _write_skill(external, "user", "external-helper")
    root = tmp_path / "root"
    root.mkdir()
    (root / "user").symlink_to(external / "user", target_is_directory=True)

    assert LocalSkillStorage(host_path=root).load_skills() == []


def test_skills_root_path_prefers_env(monkeypatch, tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    configured.mkdir()
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(configured))

    assert skills_root_path() == configured.resolve()


# ---------------------------------------------------------------------------
# Deferred discovery: describe_skill + prompt section / 延迟发现：describe_skill 与提示词段落
# ---------------------------------------------------------------------------


def test_describe_skill_tool_returns_metadata(tmp_path: Path) -> None:
    _write_skill(tmp_path, "public", "data-analysis")
    storage = LocalSkillStorage(host_path=tmp_path)
    tool = build_describe_skill_tool(storage)

    result = tool.invoke({"name": "data-analysis"})

    assert isinstance(result, str)
    assert "## Skill: data-analysis" in result
    assert "[built-in]" in result
    assert "Allowed tools: (all)" in result
    assert (
        f"Location: {DEFAULT_SKILLS_CONTAINER_PATH}/public/data-analysis/SKILL.md"
        in result
    )


def test_describe_skill_tool_escapes_metadata(tmp_path: Path) -> None:
    content = (
        "---\n"
        "name: sneaky\n"
        "description: A <b>description</b> with markup.\n"
        "---\n"
        "# Sneaky\n"
    )
    _write_skill(tmp_path, "public", "sneaky", content)
    tool = build_describe_skill_tool(LocalSkillStorage(host_path=tmp_path))

    result = tool.invoke({"name": "sneaky"})

    assert "<b>description</b>" not in result
    assert "&lt;b&gt;description&lt;/b&gt;" in result


def test_describe_skill_tool_no_match(tmp_path: Path) -> None:
    _write_skill(tmp_path, "public", "data-analysis")
    tool = build_describe_skill_tool(LocalSkillStorage(host_path=tmp_path))

    assert "No skills matched: nope" in tool.invoke({"name": "nope"})


def test_describe_skill_tool_preserves_explicit_no_tools(tmp_path: Path) -> None:
    content = (
        "---\n"
        "name: no-tools\n"
        "description: Uses no tools.\n"
        "allowed-tools: []\n"
        "---\n"
        "# No tools\n"
    )
    _write_skill(tmp_path, "public", "no-tools", content)
    tool = build_describe_skill_tool(LocalSkillStorage(host_path=tmp_path))

    result = tool.invoke({"name": "no-tools"})

    assert "Allowed tools: (none)" in result
    assert "Allowed tools: (all)" not in result


def test_skill_index_prompt_section_lists_names() -> None:
    section = get_skill_index_prompt_section(
        skill_names=frozenset({"data-analysis", "deep-research"})
    )

    assert "<skill_index>" in section
    assert "data-analysis, deep-research" in section
    assert "describe_skill" in section
    assert DEFAULT_SKILLS_CONTAINER_PATH in section


def test_skill_index_prompt_section_empty_without_skills() -> None:
    assert get_skill_index_prompt_section(skill_names=frozenset()) == ""


# ---------------------------------------------------------------------------
# Sandbox skills mount / 沙箱技能挂载
# ---------------------------------------------------------------------------


def _local_sandbox_with_skills(tmp_path: Path) -> tuple[LocalSandbox, Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = tmp_path / "skills"
    _write_skill(skills, "public", "data-analysis")
    return LocalSandbox(workspace, skills_root=skills), workspace, skills


def test_workspace_reads_skill_through_skills_mount(tmp_path: Path) -> None:
    sandbox, _, _ = _local_sandbox_with_skills(tmp_path)
    workspace = SandboxWorkspace(sandbox)

    content = workspace.read(f"/mnt/skills/public/data-analysis/{SKILL_MD_FILE}")

    assert "data-analysis" in content


def test_workspace_lists_and_searches_skills_mount(tmp_path: Path) -> None:
    sandbox, _, _ = _local_sandbox_with_skills(tmp_path)
    workspace = SandboxWorkspace(sandbox)

    entries = workspace.ls("/mnt/skills/public")
    assert [entry["path"] for entry in entries] == ["/mnt/skills/public/data-analysis/"]

    assert workspace.glob(f"**/{SKILL_MD_FILE}", path="/mnt/skills") == [
        "/mnt/skills/public/data-analysis/SKILL.md"
    ]
    assert workspace.grep("data-analysis", path="/mnt/skills") != []


def test_workspace_rejects_writes_to_skills_mount(tmp_path: Path) -> None:
    sandbox, _, _ = _local_sandbox_with_skills(tmp_path)
    target = f"/mnt/skills/public/data-analysis/{SKILL_MD_FILE}"

    assert sandbox.write(target, "pwned").error == "The skills mount is read-only"
    assert sandbox.edit(target, "Workflow for data-analysis", "pwned").error == (
        "The skills mount is read-only"
    )
    assert sandbox.delete("/mnt/skills/public").error == "The skills mount is read-only"
    assert "read-only" in sandbox.upload_files([(target, b"pwned")])[0].error


def test_workspace_maps_workspace_and_skills_paths_independently(
    tmp_path: Path,
) -> None:
    sandbox, workspace_root, _ = _local_sandbox_with_skills(tmp_path)
    workspace = SandboxWorkspace(sandbox)

    workspace.write("/workspace/notes/a.txt", "hi")
    skill = workspace.read(f"/mnt/skills/public/data-analysis/{SKILL_MD_FILE}")
    user_file = workspace.read("/workspace/notes/a.txt")

    assert workspace_root.is_dir()
    assert skill != user_file


def test_skills_mount_rejects_escape_through_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = tmp_path / "skills"
    skill_dir = skills / "public" / "data-analysis"
    skill_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (skill_dir / "escape").symlink_to(outside, target_is_directory=True)
    sandbox = LocalSandbox(workspace, skills_root=skills)

    with pytest.raises(Exception, match="escapes"):
        sandbox.resolve_path("/mnt/skills/public/data-analysis/escape/secret.txt")


def test_local_manager_mounts_only_enabled_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    skills = tmp_path / "skills"
    _write_skill(skills, "public", "data-analysis")
    _write_skill(skills, "public", "deep-research")
    from zharness.skills.state import SkillState

    SkillState(tmp_path / "skills_state.json").set_enabled("deep-research", False)
    manager = LocalSandboxManager(
        settings=LocalSandboxSettings(root=str(tmp_path), skills_root=str(skills))
    )

    sandbox = manager.for_thread("thread-a")

    assert sandbox.skills_root is not None
    assert (sandbox.skills_root / "public" / "data-analysis" / SKILL_MD_FILE).is_file()
    assert not (sandbox.skills_root / "public" / "deep-research").exists()


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_local_execute_maps_advertised_skills_path(tmp_path: Path, quote: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = tmp_path / "skills with spaces"
    skill_file = _write_skill(skills, "public", "data-analysis")
    sandbox = LocalSandbox(workspace, allow_host_bash=True, skills_root=skills)

    result = sandbox.execute(
        f"test -f {quote}{DEFAULT_SKILLS_CONTAINER_PATH}/public/data-analysis/{SKILL_MD_FILE}{quote}"
    )

    assert skill_file.is_file()
    assert result.exit_code == 0


def test_local_execute_cannot_modify_host_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = tmp_path / "skills"
    skill_file = _write_skill(skills, "public", "data-analysis")
    original = skill_file.read_text(encoding="utf-8")
    sandbox = LocalSandbox(workspace, allow_host_bash=True, skills_root=skills)

    result = sandbox.execute(
        f"printf pwned > {DEFAULT_SKILLS_CONTAINER_PATH}/public/data-analysis/{SKILL_MD_FILE}"
    )

    assert result.exit_code == 0
    assert skill_file.read_text(encoding="utf-8") == original
