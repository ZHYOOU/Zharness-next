"""Opt-in tests that require a real local Docker daemon."""

import os
import time
from pathlib import Path

import pytest
from zharness.sandbox.manager import DockerSandboxManager, DockerSandboxSettings
from zharness.sandbox.workspace import SandboxWorkspace

import docker


@pytest.mark.skipif(
    os.environ.get("ZHARNESS_RUN_DOCKER_TESTS") != "1",
    reason="set ZHARNESS_RUN_DOCKER_TESTS=1 to run Docker integration tests",
)
def test_real_docker_sandbox(tmp_path: Path, monkeypatch) -> None:
    thread_id = "docker-integration"
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    client = docker.from_env()
    manager = DockerSandboxManager(
        client=client,
        settings=DockerSandboxSettings(image="zharness-sandbox:latest"),
    )

    try:
        sandbox = manager.for_thread(thread_id)
        reused = manager.for_thread(thread_id)
        assert reused.id == sandbox.id

        hello = sandbox.execute("printf 'hello from container'")
        assert hello.exit_code == 0
        assert hello.output == "hello from container"

        workspace = tmp_path / "workspaces" / thread_id
        (workspace / "from-host.txt").write_text("host", encoding="utf-8")
        assert sandbox.execute("cat from-host.txt").output == "host"

        created = sandbox.execute("printf container > from-container.txt")
        assert created.exit_code == 0
        assert (workspace / "from-container.txt").read_text(encoding="utf-8") == (
            "container"
        )

        upload = sandbox.upload_files([("/workspace/nested/upload.txt", b"uploaded")])
        assert upload[0].error is None
        assert sandbox.execute("printf '+editable' >> nested/upload.txt").exit_code == 0
        download = sandbox.download_files(["/workspace/nested/upload.txt"])
        assert download[0].error is None
        assert download[0].content == b"uploaded+editable"

        files = SandboxWorkspace(sandbox)
        assert files.write("/docs/guide.txt", "alpha\nbeta") == "/docs/guide.txt"
        assert files.read("/docs/guide.txt", offset=1, limit=1) == "beta"
        assert files.edit("/docs/guide.txt", "beta", "needle") == 1
        assert files.grep("needle", path="/docs") == [
            {"path": "/docs/guide.txt", "line": 2, "text": "needle"}
        ]
        assert files.glob("*.txt", path="/docs") == ["/docs/guide.txt"]
        assert [entry["path"] for entry in files.ls("/docs")] == ["/docs/guide.txt"]
        assert files.delete("/docs") == "/docs"
        assert not (workspace / "docs").exists()

        started = time.monotonic()
        timed_out = sandbox.execute("sleep 10", timeout=1)
        elapsed = time.monotonic() - started
        assert timed_out.exit_code == 124
        assert elapsed < 4

        container = client.containers.get(sandbox.id)
        container.reload()
        host_config = container.attrs["HostConfig"]
        assert host_config["ReadonlyRootfs"] is True
        assert host_config["NetworkMode"] == "none"
        assert set(host_config["CapDrop"]) == {"ALL"}
        assert any(
            "no-new-privileges" in option for option in host_config["SecurityOpt"]
        )
        assert host_config["Memory"] == 512 * 1024 * 1024
        assert host_config["NanoCpus"] == 1_000_000_000
        assert host_config["PidsLimit"] == 128
    finally:
        manager.remove_for_thread(thread_id)

    assert manager.remove_for_thread(thread_id) is False
