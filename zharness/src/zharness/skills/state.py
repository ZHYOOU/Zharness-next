"""Persistent enabled/disabled state for skills.

Skill enablement is dynamic runtime state, stored separately from the
``SKILL.md`` files so toggling a skill never rewrites read-only skill packages.
The state lives in a small JSON file under ZHarness home and is keyed by skill
name; a missing entry defaults to enabled, so existing skills keep working
until explicitly disabled.

技能启停状态。技能的启用/禁用是运行时动态状态，独立于 ``SKILL.md`` 文件存储，
因此切换技能状态不会改写只读的技能包。状态存放在 ZHarness home 下的小型 JSON 文件中，
以技能名称为键；缺失条目默认启用，因此已有技能在显式禁用之前保持可用。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Final

from zharness.host.paths import zharness_home
from zharness.skills.validation import validate_skill_name

logger = logging.getLogger(__name__)

STATE_FILE_NAME: Final = "skills_state.json"
"""File name of the skill enablement state under ZHarness home. / ZHarness home 下技能启停状态的文件名。"""

STATE_VERSION: Final = 1
"""Schema version written into the state file. / 写入状态文件的模式版本号。"""


def default_state_path() -> Path:
    """Return the default JSON file that stores skill enablement state. / 返回默认的技能启停状态 JSON 文件路径。"""
    return zharness_home() / STATE_FILE_NAME


class SkillStateError(RuntimeError):
    """Raised when skill enablement state cannot be written. / 当技能启停状态无法写入时抛出。"""


class SkillState:
    """Persistent on/off state for skills, keyed by skill name.

    An absent entry means enabled; callers disable a skill by storing ``False``
    and re-enable it by storing ``True``. Reads never create the file, so
    merely scanning skills leaves no footprint.

    以技能名称为键的持久化启停状态。条目缺失表示启用；调用方存入 ``False`` 禁用技能，
    存入 ``True`` 重新启用。读取不会创建文件，因此仅扫描技能不会留下任何痕迹。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = (
            Path(path).expanduser().resolve(strict=False)
            if path is not None
            else default_state_path()
        )

    @property
    def path(self) -> Path:
        """Path to the state JSON file. / 状态 JSON 文件的路径。"""
        return self._path

    def load(self) -> dict[str, bool]:
        """Read the state mapping ``{name: enabled}``.

        A missing or corrupt file yields an empty mapping (everything enabled).
        A corrupt file is logged and treated as empty rather than failing the
        skill scan.

        读取 ``{name: enabled}`` 状态映射。文件缺失或损坏时返回空映射（全部启用）。
        损坏的文件只记日志并按空映射处理，不会导致技能扫描失败。
        """
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Could not read skill state %s: %s", self._path, exc)
            return {}
        skills = raw.get("skills") if isinstance(raw, dict) else None
        if not isinstance(skills, dict):
            logger.warning("Malformed skill state in %s; ignoring", self._path)
            return {}
        return {
            str(name): bool(enabled)
            for name, enabled in skills.items()
            if isinstance(name, str)
        }

    def is_enabled(self, name: str) -> bool:
        """Return whether *name* is enabled, defaulting to enabled. / 返回 *name* 是否启用，默认启用。"""
        return self.load().get(name, True)

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Persist the enablement of *name*.

        Raises :class:`ValueError` for invalid skill names and
        :class:`SkillStateError` when the file cannot be written.

        持久化 *name* 的启停状态。技能名非法时抛出 :class:`ValueError`；
        文件写入失败时抛出 :class:`SkillStateError`。
        """
        normalized = validate_skill_name(name)
        state = self.load()
        state[normalized] = bool(enabled)
        self._write(state)

    def _write(self, state: dict[str, bool]) -> None:
        payload = {"version": STATE_VERSION, "skills": state}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_name, self._path)
        except OSError as exc:
            raise SkillStateError(
                f"Could not write skill state {self._path}"
            ) from exc


def enable_skill(name: str) -> None:
    """Enable a skill by name. / 按名称启用技能。"""
    SkillState().set_enabled(name, True)


def disable_skill(name: str) -> None:
    """Disable a skill by name. / 按名称禁用技能。"""
    SkillState().set_enabled(name, False)