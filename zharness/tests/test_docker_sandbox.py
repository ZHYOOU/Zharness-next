import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

from docker.errors import APIError, NotFound
from zharness.sandbox.docker import DockerSandbox
from zharness.sandbox.manager import (
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
    def __init__(self, api: FakeAPI | None = None) -> None:
        self.id = "container-one"
        self.client = SimpleNamespace(api=api or FakeAPI())
        self.archives: list[tuple[str, bytes]] = []
        self.attrs = {"Config": {"User": "1000:1000"}}

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

    result = sandbox.execute("echo hello", timeout=12)

    assert result.output == "abcd"
    assert result.exit_code == 7
    assert result.truncated is True
    assert api.created is not None
    assert api.created["workdir"] == "/workspace"
    assert api.created["cmd"] == [
        "timeout",
        "--signal=TERM",
        "--kill-after=1s",
        "12s",
        "/bin/sh",
        "-lc",
        "echo hello",
    ]


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


def test_manager_creates_hardened_thread_container(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    containers = FakeContainers()
    client = SimpleNamespace(containers=containers)
    settings = DockerSandboxSettings(user="1000:1000")
    manager = DockerSandboxManager(client=client, settings=settings)

    sandbox = manager.for_thread("thread-one")

    assert sandbox.id == "container-one"
    options = containers.run_options
    assert options is not None
    workspace = str(tmp_path / "workspaces" / "thread-one")
    assert options["volumes"] == {workspace: {"bind": "/workspace", "mode": "rw"}}
    assert options["network_mode"] == "bridge"
    assert options["read_only"] is True
    assert options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges"]
    assert options["user"] == "1000:1000"
    assert options["labels"] == {
        SANDBOX_LABEL: "true",
        THREAD_LABEL: "thread-one",
    }


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
