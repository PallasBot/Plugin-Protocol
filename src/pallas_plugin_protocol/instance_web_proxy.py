"""协议实例原生页面的受控本地反代目标。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .contract import SNOWLUMA_PROTOCOL_BACKEND

InstanceWebSurface = Literal["novnc"]


@dataclass(frozen=True)
class InstanceProxyTarget:
    origin: str
    base_path: str


def resolve_instance_proxy_target(
    account: dict[str, Any],
    *,
    surface: InstanceWebSurface,
    config: Any,
) -> InstanceProxyTarget:
    """仅从账号登记端口解析可反代的环回目标。"""
    if surface != "novnc":
        raise ValueError("原生 WebUI 不支持通过控制台反代")
    backend = str(account.get("protocol_backend") or "").strip().lower()

    if backend != SNOWLUMA_PROTOCOL_BACKEND:
        raise ValueError("noVNC 仅支持 SnowLuma 协议实例")
    if not account.get("snowluma_linux_docker"):
        raise ValueError("当前 SnowLuma 实例未启用 Docker noVNC")

    from .snowluma_docker import snowluma_docker_effective_host_novnc_port

    port = resolve_instance_proxy_port(snowluma_docker_effective_host_novnc_port(account, config), label="noVNC")
    return InstanceProxyTarget(origin=f"http://127.0.0.1:{port}", base_path="/")


def resolve_instance_proxy_port(value: object, *, label: str) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label} 端口未配置") from e
    if not 1 <= port <= 65535:
        raise ValueError(f"{label} 端口未配置")
    return port
