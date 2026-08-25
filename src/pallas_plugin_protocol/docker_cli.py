"""Docker CLI：镜像名解析、stderr 启发式、inspect/rm/stop 共用实现。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DockerCapabilityStatus = Literal[
    "ready",
    "cli_missing",
    "socket_missing",
    "permission_denied",
    "daemon_unreachable",
]


@dataclass(frozen=True, slots=True)
class DockerCapability:
    status: DockerCapabilityStatus
    ready: bool
    message: str
    server_version: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "status": self.status,
            "ready": self.ready,
            "message": self.message,
            "server_version": self.server_version,
        }


async def probe_docker_capability(*, socket_path: Path | None = None) -> DockerCapability:
    if not shutil.which("docker"):
        return DockerCapability("cli_missing", False, "未找到 Docker CLI；请使用官方镜像或在宿主机手动执行")

    local_socket = socket_path or Path("/var/run/docker.sock")
    if not os.environ.get("DOCKER_HOST") and not local_socket.exists():
        return DockerCapability(
            "socket_missing",
            False,
            f"未找到 {local_socket}；容器部署请显式挂载 docker.sock",
        )

    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            "--format",
            "{{json .ServerVersion}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except OSError as err:
        return DockerCapability("daemon_unreachable", False, f"无法连接 Docker daemon：{err}")

    if process.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        if "permission denied" in detail.lower():
            return DockerCapability("permission_denied", False, f"没有访问 Docker daemon 的权限：{detail}")
        return DockerCapability("daemon_unreachable", False, f"无法连接 Docker daemon：{detail or 'docker info 失败'}")

    version = (stdout or b"").decode("utf-8", errors="replace").strip().strip('"')
    return DockerCapability("ready", True, "Docker daemon 可用", server_version=version)


def docker_repository_from_ref(ref: str) -> str:
    s = (ref or "").strip()
    if not s:
        return ""
    if "@" in s:
        s = s.split("@", 1)[0].strip()
    if ":" not in s:
        return s
    i = s.rfind(":")
    rhs = s[i + 1 :]
    if "/" not in rhs:
        return s[:i].strip()
    return s


def docker_tag_from_ref(ref: str) -> str:
    """从 ``repo:tag`` 取 tag；无 tag 或 digest 形态返回 ``latest``。"""
    s = (ref or "").strip()
    if not s:
        return "latest"
    if "@" in s:
        s = s.split("@", 1)[0].strip()
    if ":" not in s:
        return "latest"
    i = s.rfind(":")
    rhs = s[i + 1 :]
    if not rhs or "/" in rhs:
        return "latest"
    return rhs


def docker_stderr_suggests_host_port_bind_conflict(text: str) -> bool:
    t = (text or "").lower()
    return (
        "port is already allocated" in t
        or "ports are not available" in t
        or "address already in use" in t
        or "only one usage of each socket address" in t
    )


def docker_stderr_suggests_container_name_conflict(text: str) -> bool:
    t = (text or "").lower()
    return "already in use" in t and "container" in t


async def docker_inspect_running_async(name: str) -> bool:
    if not shutil.which("docker"):
        return False
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "-f",
        "{{.State.Running}}",
        name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return False
    return b"true" in (out or b"").lower()


def docker_inspect_running_sync(name: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    return "true" in (r.stdout or "").lower()


def docker_container_exists_sync(name: str) -> bool:
    """容器是否已存在（含已停止）；不存在或 Docker 不可用时返回 False。"""
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name=^{name}$"],
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    return bool((r.stdout or "").strip())


async def docker_command_strict_async(*args: str, wait_timeout: int = 120) -> str:
    """执行 Docker 命令并在不可用、超时或失败时保留 stderr 抛错。"""
    capability = await probe_docker_capability()
    if not capability.ready:
        raise RuntimeError(capability.message)
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as err:
        raise RuntimeError(f"启动 Docker 命令失败：{err}") from err
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=wait_timeout)
    except TimeoutError as err:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Docker {' '.join(args)} 超时（{wait_timeout}s）") from err
    if proc.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        suffix = f"：{detail[-1200:]}" if detail else ""
        raise RuntimeError(f"Docker {' '.join(args)} 失败 (exit {proc.returncode}){suffix}")
    return (stdout or b"").decode("utf-8", errors="replace")


async def docker_stop_strict_async(name: str, *, wait_timeout: int = 120) -> None:
    await docker_command_strict_async("stop", name, wait_timeout=wait_timeout)


async def docker_rm_force_strict_async(name: str, *, wait_timeout: int = 120) -> None:
    await docker_command_strict_async("rm", "-f", name, wait_timeout=wait_timeout)


async def docker_rm_force_async(name: str) -> None:
    if not shutil.which("docker"):
        return
    p = await asyncio.create_subprocess_exec("docker", "rm", "-f", name, stderr=asyncio.subprocess.DEVNULL)
    await p.wait()


def docker_rm_force_sync(name: str, *, subprocess_timeout: int = 30) -> None:
    if not shutil.which("docker"):
        return
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            check=False,
            capture_output=True,
            timeout=subprocess_timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


async def docker_stop_async(name: str, *, wait_timeout: int = 60) -> None:
    if not shutil.which("docker"):
        return
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "stop",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=wait_timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()


def docker_stop_sync(name: str, *, subprocess_timeout: int = 60) -> None:
    if not shutil.which("docker"):
        return
    try:
        subprocess.run(
            ["docker", "stop", name],
            check=False,
            capture_output=True,
            timeout=subprocess_timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
