from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol" / "docker_cli.py"
_SPEC = importlib.util.spec_from_file_location("pallas_plugin_protocol_docker_capability_test", _MODULE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
docker_cli = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = docker_cli
_SPEC.loader.exec_module(docker_cli)


class FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def process_factory(process: FakeProcess):
    async def create_process(*_args, **_kwargs) -> FakeProcess:
        return process

    return create_process


@pytest.mark.asyncio
async def test_probe_reports_missing_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: None)

    result = await docker_cli.probe_docker_capability(socket_path=tmp_path / "docker.sock")

    assert result.status == "cli_missing"
    assert result.ready is False
    assert "Docker CLI" in result.message


@pytest.mark.asyncio
async def test_probe_reports_missing_local_socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.delenv("DOCKER_HOST", raising=False)

    result = await docker_cli.probe_docker_capability(socket_path=tmp_path / "docker.sock")

    assert result.status == "socket_missing"
    assert "docker.sock" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "status", "message_part"),
    [
        (b"permission denied while trying to connect", "permission_denied", "权限"),
        (b"Cannot connect to the Docker daemon", "daemon_unreachable", "daemon"),
    ],
)
async def test_probe_classifies_daemon_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stderr: bytes,
    status: str,
    message_part: str,
) -> None:
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        docker_cli.asyncio,
        "create_subprocess_exec",
        process_factory(FakeProcess(1, stderr=stderr)),
    )

    result = await docker_cli.probe_docker_capability(socket_path=socket_path)

    assert result.status == status
    assert message_part in result.message


@pytest.mark.asyncio
async def test_probe_reports_ready_daemon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        docker_cli.asyncio,
        "create_subprocess_exec",
        process_factory(FakeProcess(0, stdout=b'"28.3.3"\n')),
    )

    result = await docker_cli.probe_docker_capability(socket_path=socket_path)

    assert result.status == "ready"
    assert result.ready is True
    assert result.server_version == "28.3.3"
