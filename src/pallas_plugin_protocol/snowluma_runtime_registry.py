"""SnowLuma Runtime 注册表：一个进程/容器挂多个 QQ。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contract import (
    ACCOUNT_PROTOCOL_BACKEND_KEY,
    DEFAULT_PROTOCOL_BACKEND,
    SNOWLUMA_PROTOCOL_BACKEND,
    SNOWLUMA_RUNTIME_ID_KEY,
)


def is_snowluma_account(account: dict) -> bool:
    bk = (
        str(account.get(ACCOUNT_PROTOCOL_BACKEND_KEY) or DEFAULT_PROTOCOL_BACKEND).strip().lower()
        or DEFAULT_PROTOCOL_BACKEND
    )
    return bk == SNOWLUMA_PROTOCOL_BACKEND or bool(account.get("snowluma_linux_docker"))


def resolve_runtime_data_dir(runtime: dict) -> Path:
    raw = str(runtime.get("data_dir", "") or "").strip()
    if not raw:
        raise ValueError("Runtime 缺少 data_dir")
    return Path(raw).resolve()


def new_snowluma_runtime_id() -> str:
    return f"sl-rt-{uuid.uuid4().hex[:12]}"


class SnowLumaRuntimeRegistry:
    def __init__(self, data_dir: Path, instances_root: Path) -> None:
        self._data_dir = data_dir
        self._instances_root = instances_root
        self._path = data_dir / "snowluma_runtimes.json"
        self._items: dict[str, dict] = {}

    def default_data_dir(self, runtime_id: str) -> Path:
        rid = (runtime_id or "").strip() or "x"
        return (self._instances_root / "runtimes" / rid).resolve()

    def load(self) -> None:
        if not self._path.exists():
            self._items = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._items = {}
            return
        if not isinstance(raw, dict):
            self._items = {}
            return
        out: dict[str, dict] = {}
        for key, val in raw.items():
            if isinstance(val, dict):
                out[str(key)] = val
        self._items = out

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_runtimes(self) -> list[dict]:
        return [dict(v) for v in self._items.values()]

    def get(self, runtime_id: str) -> dict | None:
        rid = str(runtime_id or "").strip()
        item = self._items.get(rid)
        return dict(item) if item else None

    def has(self, runtime_id: str) -> bool:
        return str(runtime_id or "").strip() in self._items

    def create(self, payload: dict) -> dict:
        rid = str(payload.get("id", "") or "").strip() or new_snowluma_runtime_id()
        if rid in self._items:
            raise ValueError(f"Runtime 已存在: {rid}")
        data_dir = str(payload.get("data_dir", "") or "").strip()
        if not data_dir:
            data_dir = str(self.default_data_dir(rid))
        now = datetime.now(UTC).isoformat()
        item: dict[str, Any] = {
            "id": rid,
            "display_name": str(payload.get("display_name", "") or "").strip() or rid,
            "data_dir": data_dir,
            "created_at": now,
            "updated_at": now,
        }
        for key in (
            "webui_port",
            "snowluma_docker_host_onebot_http",
            "snowluma_docker_host_onebot_ws",
            "snowluma_docker_host_novnc_port",
            "snowluma_docker_host_vnc_port",
            "snowluma_managed_webui_password",
            "legacy_container_account_id",
            "program_dir",
            "snowluma_docker_image",
            "last_ws_connected_at",
            "ws_disconnected_since_at",
        ):
            if key not in payload:
                continue
            val = payload[key]
            if val is None or (isinstance(val, str) and not str(val).strip()):
                continue
            item[key] = val
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "config").mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "cache").mkdir(parents=True, exist_ok=True)
        self._items[rid] = item
        self.save()
        return dict(item)

    def update(self, runtime_id: str, payload: dict) -> dict:
        rid = str(runtime_id or "").strip()
        item = self._items.get(rid)
        if not item:
            raise KeyError("Runtime 不存在")
        editable = (
            "display_name",
            "data_dir",
            "webui_port",
            "snowluma_docker_host_onebot_http",
            "snowluma_docker_host_onebot_ws",
            "snowluma_docker_host_novnc_port",
            "snowluma_docker_host_vnc_port",
            "snowluma_managed_webui_password",
            "legacy_container_account_id",
            "program_dir",
            "snowluma_docker_image",
            "last_ws_connected_at",
            "ws_disconnected_since_at",
        )
        for key in editable:
            if key not in payload:
                continue
            val = payload[key]
            if val is None or (isinstance(val, str) and not str(val).strip()):
                if key in (
                    "snowluma_docker_host_novnc_port",
                    "snowluma_docker_host_vnc_port",
                    "legacy_container_account_id",
                    "snowluma_managed_webui_password",
                    "snowluma_docker_image",
                    "last_ws_connected_at",
                    "ws_disconnected_since_at",
                ):
                    item.pop(key, None)
                continue
            item[key] = val
        item["updated_at"] = datetime.now(UTC).isoformat()
        self.save()
        return dict(item)

    def delete(self, runtime_id: str) -> None:
        rid = str(runtime_id or "").strip()
        if rid not in self._items:
            raise KeyError("Runtime 不存在")
        self._items.pop(rid, None)
        self.save()

    def migrate_legacy_accounts(self, accounts: dict[str, dict]) -> bool:
        """为缺少 snowluma_runtime_id 的 SnowLuma 账号各建一个 Runtime。"""
        changed = False
        for account_id, account in list(accounts.items()):
            if not is_snowluma_account(account):
                continue
            if str(account.get(SNOWLUMA_RUNTIME_ID_KEY, "") or "").strip():
                continue
            ad = str(account.get("account_data_dir", "") or "").strip()
            payload: dict[str, Any] = {
                "display_name": str(account.get("display_name") or account_id).strip() or account_id,
                "legacy_container_account_id": account_id,
            }
            if ad:
                payload["data_dir"] = ad
            for key in (
                "webui_port",
                "snowluma_docker_host_onebot_http",
                "snowluma_docker_host_onebot_ws",
                "snowluma_docker_host_novnc_port",
                "snowluma_docker_host_vnc_port",
                "snowluma_managed_webui_password",
                "program_dir",
            ):
                if key in account and account[key] is not None:
                    payload[key] = account[key]
            runtime = self.create(payload)
            account[SNOWLUMA_RUNTIME_ID_KEY] = runtime["id"]
            if not ad:
                account["account_data_dir"] = str(runtime["data_dir"])
            else:
                account["account_data_dir"] = ad
            changed = True
        return changed


__all__ = [
    "SnowLumaRuntimeRegistry",
    "is_snowluma_account",
    "new_snowluma_runtime_id",
    "resolve_runtime_data_dir",
]
