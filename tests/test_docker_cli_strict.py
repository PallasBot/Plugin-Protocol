from __future__ import annotations

import asyncio

import pytest

from pallas_plugin_protocol import docker_cli


class FailingProcess:
    returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"container is busy"


class HangingProcess:
    returncode = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.Future()

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or -9


@pytest.mark.asyncio
async def test_strict_remove_rejects_docker_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: "/usr/bin/docker")

    async def create_process(*_args, **_kwargs):
        return FailingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="rm -f test-container 失败.*container is busy"):
        await docker_cli.docker_rm_force_strict_async("test-container")


@pytest.mark.asyncio
async def test_strict_remove_rejects_unavailable_docker(monkeypatch) -> None:
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="Docker 不可用"):
        await docker_cli.docker_rm_force_strict_async("test-container")


@pytest.mark.asyncio
async def test_strict_remove_rejects_timeout(monkeypatch) -> None:
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: "/usr/bin/docker")

    async def create_process(*_args, **_kwargs):
        return HangingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="rm -f test-container 超时"):
        await docker_cli.docker_rm_force_strict_async("test-container", wait_timeout=0.01)
