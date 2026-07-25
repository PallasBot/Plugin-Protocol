"""SnowLuma 账号运行态与健康摘要（容器存活 ≠ 已登录）。"""

from __future__ import annotations

from typing import Any

from .contract import ACCOUNT_PROTOCOL_BACKEND_KEY, SNOWLUMA_PROTOCOL_BACKEND
from .snowluma_host_deps import audit_snowluma_qr_capture_host_deps


def is_snowluma_account(account: dict) -> bool:
    bk = str(account.get(ACCOUNT_PROTOCOL_BACKEND_KEY) or "").strip().lower()
    return bk == SNOWLUMA_PROTOCOL_BACKEND


def assess_snowluma_account_health(
    account: dict,
    *,
    container_running: bool,
    bot_connected: bool,
    launch_issues: list[str] | None = None,
    include_host_deps: bool = False,
) -> dict[str, object]:
    """汇总 SnowLuma 账号健康态，供协议页与 API 展示。"""
    issues = list(launch_issues or [])
    operational_warnings: list[str] = []

    if include_host_deps and account.get("snowluma_linux_docker"):
        operational_warnings.extend(audit_snowluma_qr_capture_host_deps())

    login_required = bool(container_running and not bot_connected)
    if login_required:
        operational_warnings.append("容器已运行但未连接牛牛，通常需扫码登录")

    if not container_running and not bot_connected:
        status = "stopped"
    elif login_required:
        status = "needs_login"
    elif bot_connected:
        status = "ok"
    elif container_running:
        status = "degraded"
    else:
        status = "unknown"

    return {
        "health_status": status,
        "login_required": login_required,
        "operational_warnings": operational_warnings,
        "health_issues": issues,
    }


def merge_health_into_account_state(
    account_state: dict[str, Any],
    health: dict[str, object],
) -> dict[str, Any]:
    account_state.update(health)
    return account_state
