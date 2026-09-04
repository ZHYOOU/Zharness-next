import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

from docker.errors import APIError, NotFound
from zharness.sandbox.docker import DockerSandbox
from zharness.sandbox.manager import (
    POLICY_LABEL,
    SANDBOX_LABEL,
    THREAD_LABEL,
    DockerSandboxManager,
    DockerSandboxSettings,
)


class FakeAPI:
    def __init__(self, chunks: list[bytes] | None = None, exit_code: int = 0) -> None:
        self.chunks = chunks or []
        self.exit_code = exit_code
        self.created: dict | None = None

    def exec_create(self, container_id, cmd, **kwargs):
        self.created = {"container_id": container_id, "cmd": cmd, **kwargs}
        return {"Id": "exec-one"}

    def exec_start(self, exec_id, *, stream, demux):
        assert exec_id == "exec-one"
        assert stream is True
        assert demux is False
        return iter(self.chunks)

    def exec_inspect(self, exec_id):
        assert exec_id == "exec-one"
        return {"ExitCode": self.exit_code}


class FakeContainer:
    def __init__(
        self, api: FakeAPI | None = None, *, attrs: dict | None = None
    ) -> None:
        self.id = "container-one"
        self.client = SimpleNamespace(api=api or FakeAPI())
        self.archives: list[tuple[str, bytes]] = []
        self.attrs = attrs or {"Config": {"User": "1000:1000"}}
        self.status = "running"
        self.removed = False

    def reload(self) -> None:
        pass

    def start(self) -> None:
        self.status = "running"

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True

    def put_archive(self, path: str, data: bytes) -> bool:
        self.archives.append((path, data))
        return True


def _tar_file(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _tar_directory(name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mode = 0o700
        archive.addfile(info)
    return buffer.getvalue()


def test_execute_uses_timeout_workdir_and_output_cap() -> None:
    api = FakeAPI([b"abc", b"def"], exit_code=7)
    sandbox = DockerSandbox(FakeContainer(api), max_output_bytes=4)

    result = sandbox.execute("echo hello", timeout=12, cwd="/workspace/reports/daily")

    assert result.output == "abcd"
    assert result.exit_code == 7
    assert result.truncated is True
    assert api.created is not None
    assert api.created["workdir"] == "/workspace/reports/daily"
    assert api.created["cmd"] == [
        "timeout",
        "--signal=TERM",
        "--kill-after=1s",
        "12s",
        "/bin/sh",
        "-lc",
        "echo hello",
    ]


def test_execute_rejects_workdir_outside_workspace() -> None:
    api = FakeAPI()
    sandbox = DockerSandbox(FakeContainer(api))

    result = sandbox.execute("pwd", cwd="/tmp")

    assert result.exit_code == 2
    assert result.output == "Error: command cwd escapes the workspace"
    assert api.created is None


def test_execute_reports_operation_activity() -> None:
    events: list[str] = []
    sandbox = DockerSandbox(
        FakeContainer(),
        on_operation_start=lambda: events.append("start"),
        on_operation_end=lambda: events.append("end"),
    )

    sandbox.execute("true")

    assert events == ["start", "end"]


def test_upload_builds_single_safe_archive(monkeypatch) -> None:
    container = FakeContainer()
    sandbox = DockerSandbox(container)
    monkeypatch.setattr(
        sandbox,
        "execute",
        lambda command: SimpleNamespace(exit_code=0, output=""),
    )

    response = sandbox.upload_files([("/workspace/notes/a.txt", b"hello")])

    assert response[0].error is None
    parent, archive_bytes = container.archives[0]
    assert parent == "/workspace/notes"
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        member = archive.getmembers()[0]
        assert member.name == "a.txt"
        assert (member.uid, member.gid) == (1000, 1000)
        assert archive.extractfile(member).read() == b"hello"


def test_download_rejects_directories_and_returns_file() -> None:
    container = FakeContainer()
    sandbox = DockerSandbox(container)
    archive = _tar_file("answer.txt", b"42")
    container.get_archive = lambda path: (  # type: ignore[attr-defined]
        iter([archive]),
        {"mode": 0o600, "size": 2},
    )

    result = sandbox.download_files(["/workspace/answer.txt"])

    assert result[0].content == b"42"
    assert result[0].error is None

    container.get_archive = lambda path: (  # type: ignore[attr-defined]
        iter([_tar_directory("folder")]),
        {"mode": 0o700, "size": 0},
    )
    directory = sandbox.download_files(["/workspace/folder"])
    assert directory[0].error == "is_directory"


def test_invalid_transfer_paths_are_reported_per_file() -> None:
    sandbox = DockerSandbox(FakeContainer())

    uploads = sandbox.upload_files([("relative.txt", b"x"), ("/", b"x")])
    downloads = sandbox.download_files(["../secret", "/"])

    assert all(response.error.startswith("invalid_path") for response in uploads)
    assert all(response.error.startswith("invalid_path") for response in downloads)


def test_transfer_errors_do_not_abort_the_batch(monkeypatch) -> None:
    container = FakeContainer()
    sandbox = DockerSandbox(container, max_transfer_bytes=1)
    monkeypatch.setattr(
        sandbox,
        "execute",
        lambda command: SimpleNamespace(exit_code=0, output=""),
    )

    uploads = sandbox.upload_files(
        [("/workspace/large", b"xx"), ("/workspace/small", b"x")]
    )

    assert uploads[0].error == "file exceeds 1-byte transfer limit"
    assert uploads[1].error is None


class FakeContainers:
    def __init__(self, container: FakeContainer | None = None) -> None:
        self.container = container
        self.run_options: dict | None = None

    def get(self, name: str):
        if self.container is None:
            raise NotFound("missing")
        return self.container

    def run(self, **options):
        self.run_options = options
        self.container = FakeContainer()
        return self.container

    def list(self, **kwargs):
        return [] if self.container is None else [self.container]


class FakeImages:
    def __init__(self, image_id: str = "sha256:image-one") -> None:
        self.image_id = image_id

    def get(self, image: str):
        assert image == "zharness-sandbox:latest"
        return SimpleNamespace(id=self.image_id)


def _fake_client(containers: FakeContainers):
    return SimpleNamespace(containers=containers, images=FakeImages())


def _valid_container_attrs(
    manager: DockerSandboxManager,
    workspace: str,
    *,
    image_id: str = "sha256:image-one",
) -> dict:
    return {
        "Image": image_id,
        "Config": {
            "Labels": {
                SANDBOX_LABEL: "true",
                THREAD_LABEL: "thread-one",
                POLICY_LABEL: manager._policy_fingerprint(
                    image_id, skills_root=manager._effective_skills_root()
                ),
            },
            "User": "1000:1000",
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": "bridge",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": 512 * 1024 * 1024,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": 128,
            "Init": True,
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=64m"},
        },
        "Mounts": [{"Destination": "/workspace", "Source": workspace, "RW": True}],
    }


def test_manager_creates_hardened_thread_container(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    skills = tmp_path / "skills"
    keep = skills / "public" / "data-analysis"
    keep.mkdir(parents=True)
    (keep / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: Analyze data.\n---\n# Data\n",
        encoding="utf-8",
    )
    drop = skills / "public" / "deep-research"
    drop.mkdir(parents=True)
    (drop / "SKILL.md").write_text(
        "---\nname: deep-research\ndescription: Research.\n---\n# Research\n",
        encoding="utf-8",
    )
    from zharness.skills.state import SkillState

    SkillState(tmp_path / "skills_state.json").set_enabled("deep-research", False)
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(skills))
    containers = FakeContainers()
    client = _fake_client(containers)
    settings = DockerSandboxSettings(user="1000:1000", skills_root=str(skills))
    manager = DockerSandboxManager(client=client, settings=settings)

    sandbox = manager.for_thread("thread-one")

    assert sandbox.id == "container-one"
    options = containers.run_options
    assert options is not None
    workspace = str(tmp_path / "workspaces" / "thread-one")
    effective_root = manager._effective_skills_root()
    assert effective_root is not None
    assert options["volumes"] == {
        workspace: {"bind": "/workspace", "mode": "rw"},
        effective_root: {"bind": "/mnt/skills", "mode": "ro"},
    }
    assert (Path(effective_root) / "public" / "data-analysis" / "SKILL.md").is_file()
    assert not (Path(effective_root) / "public" / "deep-research").exists()
    assert options["network_mode"] == "bridge"
    assert options["read_only"] is True
    assert options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges"]
    assert options["user"] == "1000:1000"
    assert options["labels"] == {
        SANDBOX_LABEL: "true",
        THREAD_LABEL: "thread-one",
        POLICY_LABEL: manager._policy_fingerprint(
            "sha256:image-one", skills_root=manager._effective_skills_root()
        ),
    }


def test_network_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ZHARNESS_SANDBOX_NETWORK", raising=False)

    settings = DockerSandboxSettings.from_env()

    assert settings.network_enabled is True


def test_network_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    monkeypatch.setenv("ZHARNESS_SANDBOX_NETWORK", "false")
    containers = FakeContainers()
    manager = DockerSandboxManager(
        client=_fake_client(containers),
        settings=DockerSandboxSettings.from_env(),
    )

    manager.for_thread("thread-one")

    assert containers.run_options is not None
    assert containers.run_options["network_mode"] == "none"


def test_manager_creates_container_without_skills_mount(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "missing-skills"))
    containers = FakeContainers()
    manager = DockerSandboxManager(
        client=_fake_client(containers),
        settings=DockerSandboxSettings(user="1000:1000", skills_root=None),
    )

    manager.for_thread("thread-one")

    assert containers.run_options is not None
    assert containers.run_options["volumes"] == {
        str(tmp_path / "workspaces" / "thread-one"): {
            "bind": "/workspace",
            "mode": "rw",
        }
    }


def test_manager_rebuilds_container_with_stale_security_policy(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    workspace = str(tmp_path / "workspaces" / "thread-one")
    manager = DockerSandboxManager(
        client=SimpleNamespace(),
        settings=DockerSandboxSettings(user="1000:1000"),
    )
    stale = FakeContainer(
        attrs=_valid_container_attrs(
            manager,
            workspace,
            image_id="sha256:old-image",
        )
    )
    containers = FakeContainers(stale)
    manager._client = _fake_client(containers)

    rebuilt = manager.for_thread("thread-one")

    assert stale.removed is True
    assert rebuilt is not stale
    assert containers.run_options is not None


def test_manager_reuses_container_with_current_security_policy(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    workspace = str(tmp_path / "workspaces" / "thread-one")
    manager = DockerSandboxManager(
        client=SimpleNamespace(),
        settings=DockerSandboxSettings(user="1000:1000"),
    )
    current = FakeContainer(attrs=_valid_container_attrs(manager, workspace))
    containers = FakeContainers(current)
    manager._client = _fake_client(containers)

    reused = manager.for_thread("thread-one")

    assert reused.container is current
    assert current.removed is False
    assert containers.run_options is None


def test_remove_for_thread_removes_container_and_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    workspace = tmp_path / "workspaces" / "thread-one"
    workspace.mkdir(parents=True)
    (workspace / "result.txt").write_text("data", encoding="utf-8")
    manager = DockerSandboxManager(
        client=SimpleNamespace(),
        settings=DockerSandboxSettings(user="1000:1000"),
    )
    container = FakeContainer(attrs=_valid_container_attrs(manager, str(workspace)))
    manager._client = _fake_client(FakeContainers(container))

    assert manager.remove_for_thread("thread-one") is True
    assert container.removed is True
    assert not workspace.exists()


class PrunableContainer:
    def __init__(self, container_id: str) -> None:
        self.id = container_id
        self.attrs = {}
        self.removed = False

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


def _pruning_manager(
    containers: list[PrunableContainer],
    *,
    idle_ttl_seconds: int,
    max_containers: int,
) -> DockerSandboxManager:
    collection = SimpleNamespace(list=lambda **kwargs: containers)
    return DockerSandboxManager(
        client=SimpleNamespace(containers=collection),
        settings=DockerSandboxSettings(
            idle_ttl_seconds=idle_ttl_seconds,
            max_containers=max_containers,
            cleanup_interval_seconds=1,
        ),
    )


def test_prune_removes_idle_container_but_skips_active_operation() -> None:
    idle = PrunableContainer("idle")
    active = PrunableContainer("active")
    manager = _pruning_manager(
        [idle, active],
        idle_ttl_seconds=10,
        max_containers=0,
    )
    manager._last_used = {"idle": 80.0, "active": 80.0}
    manager._operation_started("active")

    removed = manager.prune(now=100.0)

    assert removed == ["idle"]
    assert idle.removed is True
    assert active.removed is False


def test_prune_enforces_configured_maximum_by_oldest_activity() -> None:
    oldest = PrunableContainer("oldest")
    middle = PrunableContainer("middle")
    newest = PrunableContainer("newest")
    manager = _pruning_manager(
        [newest, oldest, middle],
        idle_ttl_seconds=0,
        max_containers=2,
    )
    manager._last_used = {"oldest": 10.0, "middle": 20.0, "newest": 30.0}

    removed = manager.prune(now=100.0)

    assert removed == ["oldest"]
    assert oldest.removed is True
    assert middle.removed is False
    assert newest.removed is False


def test_shutdown_all_removes_running_and_stopped_containers() -> None:
    first = PrunableContainer("first")
    second = PrunableContainer("second")
    manager = _pruning_manager(
        [first, second],
        idle_ttl_seconds=0,
        max_containers=0,
    )

    assert manager.shutdown_all() == ["first", "second"]
    assert first.removed is True
    assert second.removed is True


class StoppingContainer:
    def __init__(self, container_id: str, error: Exception | None = None) -> None:
        self.id = container_id
        self.error = error
        self.timeouts: list[int] = []

    def stop(self, *, timeout: int) -> None:
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error


def test_manager_stops_all_running_sandboxes() -> None:
    stopped = StoppingContainer("stopped")
    disappeared = StoppingContainer("missing", NotFound("missing"))
    failed = StoppingContainer("failed", APIError("daemon error"))
    containers = SimpleNamespace(list=lambda **kwargs: [stopped, disappeared, failed])
    manager = DockerSandboxManager(client=SimpleNamespace(containers=containers))

    result = manager.stop_all(timeout=3)

    assert result == ["stopped"]
    assert stopped.timeouts == [3]
    assert disappeared.timeouts == [3]
    assert failed.timeouts == [3]
