"""Sandbox backends and lifecycle management.

Provides the [`BaseSandbox`][zharness.sandbox.base.BaseSandbox] base implementation,
the [`SandboxWorkspace`][zharness.sandbox.workspace.SandboxWorkspace] virtual path
adapter, and the per-thread managers for each provider.

沙箱后端与生命周期管理。提供 [`BaseSandbox`][zharness.sandbox.base.BaseSandbox]
基类实现、[`SandboxWorkspace`][zharness.sandbox.workspace.SandboxWorkspace]
虚拟路径适配层，以及各提供商的线程级管理器。
"""

from zharness.sandbox.base import BaseSandbox

__all__ = ["BaseSandbox"]
