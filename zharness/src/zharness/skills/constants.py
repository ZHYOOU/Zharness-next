"""Shared constants for the skill system. / 技能系统的共享常量。"""

SKILL_MD_FILE = "SKILL.md"

DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"
"""Path where skills are mounted inside the sandbox.

Matches the reference ZHarness convention, so existing skill packages that
reference ``/mnt/skills/public/<name>/scripts/...`` keep working.

技能在沙箱中的挂载路径。与参考 ZHarness 约定一致，现有引用
``/mnt/skills/public/<name>/scripts/...`` 的技能包无需修改即可继续使用。
"""

ZHARNESS_SKILLS_PATH_ENV = "ZHARNESS_SKILLS_PATH"
"""Environment variable that overrides the skills directory location. / 覆盖技能目录位置的环境变量。"""