"""NapCat → SnowLuma 账号迁移（不保留 NapCat 数据为默认）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contract import (
    ACCOUNT_PROTOCOL_BACKEND_KEY,
    MANAGED_RUNTIME_TAG_KEY,
    SNOWLUMA_PROTOCOL_BACKEND,
)
from .linux_docker import docker_container_name

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .launch_manager import LaunchManager

NAPCAT_BACKEND = "napcat"

_BACKEND_CLEAR_KEYS = (
    "napcat_linux_docker",
    "snowluma_linux_docker",
    "snowluma_docker_host_onebot_http",
    "snowluma_docker_host_onebot_ws",
    "snowluma_docker_host_novnc_port",
    "snowluma_docker_host_vnc_port",
)


def snowluma_fresh_account_data_dir(instances_root: Path, account_id: str) -> Path:
    return (instances_root / account_id / "snowluma").resolve()


def is_napcat_account(account: dict) -> bool:
    bk = str(account.get(ACCOUNT_PROTOCOL_BACKEND_KEY) or NAPCAT_BACKEND).strip().lower()
    return bk == NAPCAT_BACKEND


def napcat_docker_container_name_for_account(account: dict) -> str:
    return docker_container_name(account)


def prepare_account_for_snowluma_migration(
    account: dict,
    *,
    instances_root: Path,
    preserve_napcat_data: bool = False,
) -> None:
    """就地改写账号 dict：清 NapCat 运行态字段并切换 backend（不触碰磁盘 QQ 数据）。"""
    for key in _BACKEND_CLEAR_KEYS:
        account.pop(key, None)
    account[ACCOUNT_PROTOCOL_BACKEND_KEY] = SNOWLUMA_PROTOCOL_BACKEND
    account["program_dir"] = ""
    account["command"] = ""
    account["args"] = []
    account["working_dir"] = ""
    account.pop(MANAGED_RUNTIME_TAG_KEY, None)
    aid = str(account.get("id", "") or "").strip()
    if not preserve_napcat_data and aid:
        account["account_data_dir"] = str(snowluma_fresh_account_data_dir(instances_root, aid))


def migrate_account_dict_to_snowluma(
    account: dict,
    *,
    launch: LaunchManager,
    resolve_qq: Callable[[dict], str],
    instances_root: Path,
    preserve_napcat_data: bool = False,
) -> None:
    prepare_account_for_snowluma_migration(
        account,
        instances_root=instances_root,
        preserve_napcat_data=preserve_napcat_data,
    )
    launch.apply_defaults(account, resolve_qq)


def list_napcat_account_ids(accounts: dict[str, dict]) -> list[str]:
    return [aid for aid, acc in accounts.items() if is_napcat_account(acc) and bool(acc.get("enabled", True))]
