"""Tests for dynamic skill enable/disable via the persistent state store.

Tests cover the SkillState JSON persistence, its integration into
LocalSkillStorage filtering, and the describe_skill tool.

测试动态技能启停：覆盖 SkillState JSON 持久化、LocalSkillStorage 过滤集成，
以及 describe_skill 工具。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from zharness.skills.constants import SKILL_MD_FILE
from zharness.skills.describe import build_describe_skill_tool
from zharness.skills.effective import (
    effective_skills_root,
    ensure_effective_skills_root,
)
from zharness.skills.state import (
    STATE_FILE_NAME,
    SkillState,
    SkillStateError,
    default_state_path,
    disable_skill,
    enable_skill,
)
from zharness.skills.storage import LocalSkillStorage
from zharness.skills.types import SkillCategory

VALID_SKILL_MD = (
    "---\n"
    "name: deep-research\n"
    "description: Do systematic web research.\n"
    "license: MIT\n"
    "---\n\n"
    "# Deep Research\n"
)


def _write_skill(root: Path, category: str, name: str) -> Path:
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: Workflow for {name}.\n"
        "license: MIT\n"
        "---\n\n"
        f"# {name}\n"
    )
    skill_file = skill_dir / SKILL_MD_FILE
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def _storage(tmp_path: Path, skills_root: Path, state: SkillState) -> LocalSkillStorage:
    return LocalSkillStorage(host_path=skills_root, state=state)


# ---------------------------------------------------------------------------
# SkillState persistence / SkillState 持久化
# ---------------------------------------------------------------------------


def test_default_state_path_under_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path / "home"))

    assert default_state_path() == (tmp_path / "home" / STATE_FILE_NAME).resolve()


def test_missing_state_means_enabled(tmp_path: Path) -> None:
    state = SkillState(tmp_path / "missing" / STATE_FILE_NAME)

    assert state.load() == {}
    assert state.is_enabled("deep-research") is True


def test_set_enabled_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / STATE_FILE_NAME
    SkillState(path).set_enabled("deep-research", False)

    reloaded = SkillState(path)
    assert reloaded.is_enabled("deep-research") is False
    assert reloaded.is_enabled("data-analysis") is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["skills"] == {"deep-research": False}


def test_set_enabled_then_enable(tmp_path: Path) -> None:
    path = tmp_path / STATE_FILE_NAME
    state = SkillState(path)

    state.set_enabled("deep-research", False)
    assert state.is_enabled("deep-research") is False

    state.set_enabled("deep-research", True)
    assert state.is_enabled("deep-research") is True


def test_set_enabled_rejects_invalid_name(tmp_path: Path) -> None:
    state = SkillState(tmp_path / STATE_FILE_NAME)

    with pytest.raises(ValueError, match="hyphen-case"):
        state.set_enabled("Deep Research", False)


def test_corrupt_state_ignored(tmp_path: Path) -> None:
    path = tmp_path / STATE_FILE_NAME
    path.write_text("not json", encoding="utf-8")
    state = SkillState(path)

    assert state.load() == {}
    assert state.is_enabled("deep-research") is True


def test_state_write_failure_raises(tmp_path: Path) -> None:
    path = tmp_path / "blocked" / STATE_FILE_NAME
    (tmp_path / "blocked").mkdir()
    (tmp_path / "blocked").chmod(0o500)
    state = SkillState(path)
    try:
        with pytest.raises(SkillStateError):
            state.set_enabled("deep-research", False)
    finally:
        (tmp_path / "blocked").chmod(0o700)


def test_module_level_enable_disable_helpers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path / "home"))
    disable_skill("deep-research")
    assert SkillState().is_enabled("deep-research") is False
    enable_skill("deep-research")
    assert SkillState().is_enabled("deep-research") is True


# ---------------------------------------------------------------------------
# Storage integration / 存储集成
# ---------------------------------------------------------------------------


def test_load_skills_flags_disabled_skill(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "data-analysis")
    _write_skill(skills_root, "public", "deep-research")
    state = SkillState(tmp_path / STATE_FILE_NAME)
    state.set_enabled("deep-research", False)
    storage = _storage(tmp_path, skills_root, state)

    skills = storage.load_skills()

    by_name = {s.name: s for s in skills}
    assert by_name["data-analysis"].enabled is True
    assert by_name["deep-research"].enabled is False
    assert by_name["deep-research"].category == SkillCategory.PUBLIC


def test_enabled_only_excludes_disabled(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "data-analysis")
    _write_skill(skills_root, "public", "deep-research")
    state = SkillState(tmp_path / STATE_FILE_NAME)
    state.set_enabled("deep-research", False)
    storage = _storage(tmp_path, skills_root, state)

    assert [s.name for s in storage.load_skills(enabled_only=True)] == ["data-analysis"]


def test_reenabled_skill_is_discoverable(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "deep-research")
    state = SkillState(tmp_path / STATE_FILE_NAME)
    storage = _storage(tmp_path, skills_root, state)

    state.set_enabled("deep-research", False)
    assert storage.load_skills(enabled_only=True) == []

    state.set_enabled("deep-research", True)
    assert [s.name for s in storage.load_skills(enabled_only=True)] == ["deep-research"]


def test_describe_skill_tool_hides_disabled(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "data-analysis")
    _write_skill(skills_root, "public", "deep-research")
    state = SkillState(tmp_path / STATE_FILE_NAME)
    state.set_enabled("deep-research", False)
    tool = build_describe_skill_tool(_storage(tmp_path, skills_root, state))

    assert "## Skill: data-analysis" in tool.invoke({"name": "data-analysis"})
    assert "No skills matched: deep-research" in tool.invoke({"name": "deep-research"})


# ---------------------------------------------------------------------------
# Effective skills root / 有效技能根目录
# ---------------------------------------------------------------------------


def test_effective_skills_root_includes_only_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "data-analysis")
    _write_skill(skills_root, "public", "deep-research")
    state = SkillState(tmp_path / STATE_FILE_NAME)
    state.set_enabled("deep-research", False)

    effective = effective_skills_root(skills_root, state)

    assert (effective / "public" / "data-analysis" / SKILL_MD_FILE).is_file()
    assert not (effective / "public" / "deep-research").exists()


def test_effective_skills_root_signature_changes_on_toggle(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "deep-research")
    state = SkillState(tmp_path / STATE_FILE_NAME)

    enabled_root = effective_skills_root(skills_root, state)

    state.set_enabled("deep-research", False)
    disabled_root = effective_skills_root(skills_root, state)

    assert enabled_root != disabled_root
    assert not enabled_root.exists()
    assert not (disabled_root / "public" / "deep-research").exists()


def test_effective_skills_root_reuses_existing_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "data-analysis")
    state = SkillState(tmp_path / STATE_FILE_NAME)

    first = effective_skills_root(skills_root, state)
    second = effective_skills_root(skills_root, state)

    assert first == second
    assert (first / "public" / "data-analysis" / SKILL_MD_FILE).is_file()


def test_ensure_effective_skills_root_none_without_source(tmp_path: Path) -> None:
    assert ensure_effective_skills_root(tmp_path / "missing") is None
