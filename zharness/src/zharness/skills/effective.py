"""Build an effective skills root that only exposes enabled skills.

The sandbox mounts a filtered copy of the skills directory so disabled skills
are not merely hidden from the prompt index but are physically absent from
``/mnt/skills``. The filtered tree lives under ``<ZHARNESS_HOME>/skills_effective``
keyed by a content signature: any change to the enabled set or to skill files
yields a new directory path, which automatically invalidates existing sandboxes
whose mounts point at an older path.

构建只暴露启用技能的有效技能根目录。沙箱挂载的是技能目录的过滤副本，使禁用技能不仅从
提示词索引中隐藏，而且在 ``/mnt/skills`` 中物理不存在。过滤后的目录树存放在
``<ZHARNESS_HOME>/skills_effective`` 下，以内容签名为键：启用集合或技能文件发生变化时
会产生新的目录路径，并自动使挂载指向旧路径的现有沙箱失效。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from pathlib import Path

from zharness.host.paths import zharness_home
from zharness.skills.constants import SKILL_MD_FILE
from zharness.skills.parser import parse_skill_file
from zharness.skills.state import SkillState
from zharness.skills.storage import skills_root_path
from zharness.skills.types import SkillCategory

logger = logging.getLogger(__name__)

EFFECTIVE_ROOT_DIR_NAME = "skills_effective"
"""Name of the filtered skills root under ZHarness home. / ZHarness home 下过滤技能根目录的名称。"""

_BUILD_LOCK = threading.Lock()


def skills_signature(real_root: Path, state: SkillState) -> str:
    """Hash the enabled set and the skill files so the mount path tracks changes.

    The state file content and every ``SKILL.md`` package's category, relative
    path, and directory mtime contribute to the signature; a change in any of
    them produces a new signature and therefore a new mount path.

    对启用集合与技能文件求哈希，使挂载路径跟随变化。状态文件内容以及每个
    ``SKILL.md`` 包的类别、相对路径与目录 mtime 共同参与签名；其中任何一项变化都会
    产生新签名，从而产生新的挂载路径。
    """
    digest = hashlib.sha256()
    digest.update(json.dumps(state.load(), sort_keys=True).encode("utf-8"))
    if real_root is not None and real_root.is_dir():
        for category in SkillCategory:
            category_path = real_root / category.value
            if not category_path.is_dir() or category_path.is_symlink():
                continue
            for current_root, dir_names, file_names in os.walk(category_path):
                dir_names[:] = sorted(
                    name for name in dir_names if not name.startswith(".")
                )
                if SKILL_MD_FILE not in file_names:
                    continue
                dir_names.clear()
                rel = Path(current_root).relative_to(category_path).as_posix()
                try:
                    mtime = Path(current_root).stat().st_mtime_ns
                except OSError:
                    mtime = 0
                digest.update(
                    f"{category.value}|{rel}|{mtime}\n".encode("utf-8")
                )
    return digest.hexdigest()[:24]


def effective_skills_root(real_root: Path, state: SkillState) -> Path:
    """Return the filtered skills root for the given source root and state.

    The directory is materialised on first use and cached by signature;
    subsequent calls with an unchanged signature return the existing path.

    为给定源根目录与状态返回过滤后的技能根目录。首次使用时物化该目录并按签名缓存；
    签名不变时后续调用直接返回已有路径。
    """
    signature = skills_signature(real_root, state)
    base = zharness_home() / EFFECTIVE_ROOT_DIR_NAME
    target = base / signature
    if target.is_dir():
        return target
    with _BUILD_LOCK:
        if not target.is_dir():
            try:
                _build_effective_tree(target, real_root, state)
            except OSError:
                logger.exception("Failed to build effective skills root")
                raise
            _prune_stale(base, keep=signature)
    return target


def ensure_effective_skills_root(
    real_root: str | Path | None = None,
    *,
    state: SkillState | None = None,
) -> str | None:
    """Return the filtered skills root path, or ``None`` when none is available.

    ``real_root`` defaults to the configured skills directory; ``state``
    defaults to the global skill enablement state.

    返回过滤后的技能根目录路径；不可用时返回 ``None``。``real_root`` 默认为配置的
    技能目录；``state`` 默认为全局技能启停状态。
    """
    root = Path(real_root) if real_root is not None else skills_root_path()
    if not root.is_dir():
        return None
    return str(effective_skills_root(root, state or SkillState()))


def _build_effective_tree(target: Path, real_root: Path, state: SkillState) -> None:
    """Copy every enabled skill package under ``target``. / 将所有启用技能包复制到 ``target`` 下。"""
    enabled_map = state.load()
    target.mkdir(parents=True, exist_ok=True)
    for category in SkillCategory:
        category_path = real_root / category.value
        if not category_path.is_dir() or category_path.is_symlink():
            continue
        for current_root, dir_names, file_names in os.walk(category_path):
            dir_names[:] = sorted(
                name for name in dir_names if not name.startswith(".")
            )
            if SKILL_MD_FILE not in file_names:
                continue
            dir_names.clear()
            rel = Path(current_root).relative_to(category_path)
            skill = parse_skill_file(
                Path(current_root) / SKILL_MD_FILE,
                category=category,
                relative_path=rel,
            )
            if skill is None or not enabled_map.get(skill.name, True):
                continue
            destination = target / category.value / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(Path(current_root), destination, dirs_exist_ok=True)


def _prune_stale(base: Path, *, keep: str) -> None:
    """Remove previously materialised signatures, keeping only the current one. / 移除先前物化的签名目录，只保留当前签名。"""
    if not base.is_dir():
        return
    for entry in base.iterdir():
        if entry.name == keep:
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            logger.warning(
                "Could not prune stale effective skills root %s", entry
            )