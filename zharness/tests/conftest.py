"""Shared test fixtures. / 共享测试夹具。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_zharness_home(tmp_path, monkeypatch) -> None:
    """Point ZHARNESS_HOME at a temp dir so tests never read runtime state.

    Skill enablement state and the effective skills root live under ZHarness
    home; isolating it keeps tests independent of the developer's real state
    and of any skill toggles made against the running server.

    将 ZHARNESS_HOME 指向临时目录，使测试不读取运行时状态。技能启停状态与有效技能
    根目录位于 ZHarness home 下；隔离它可让测试不受开发者真实状态及针对运行中服务
    所做的技能开关影响。
    """
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path / "zharness-home"))
