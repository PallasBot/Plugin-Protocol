"""SnowLuma Runtime 无 WS 容器回收测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_account_runtime_switch import SNOWLUMA_PROTOCOL_BACKEND, make_service


@pytest.mark.asyncio
async def test_cleanup_removes_container_after_three_days_without_ws(monkeypatch) -> None:
    runtime = {
        "id": "sl-rt-stale",
        "data_dir": "/tmp/stale",
        "webui_port": 6200,
        "last_ws_connected_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
    }
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._is_bot_connected = lambda _account: False
    removed: list[str] = []
    names: list[str] = []

    def exists(name: str) -> bool:
        names.append(name)
        return True

    async def remove(name: str) -> None:
        removed.append(name)

    monkeypatch.setattr(
        "pallas_plugin_protocol.snowluma_docker.snowluma_docker_remove_force",
        remove,
    )
    monkeypatch.setattr(
        "pallas_plugin_protocol.snowluma_docker.snowluma_docker_container_exists_sync",
        exists,
    )

    assert await service.cleanup_stale_snowluma_runtime_containers() == [runtime["id"]]
    assert service._sl_runtime_registry.get(runtime["id"]) is not None
    assert service._accounts[account["id"]] is account
    assert removed == ["pallas-proto-sl-rt-sl-rt-stale"]
    assert names == ["pallas-proto-sl-rt-sl-rt-stale"]


@pytest.mark.asyncio
async def test_cleanup_skips_already_removed_container(monkeypatch) -> None:
    runtime = {
        "id": "sl-rt-gone",
        "data_dir": "/tmp/gone",
        "webui_port": 6200,
        "last_ws_connected_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
    }
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._is_bot_connected = lambda _account: False
    removed: list[str] = []

    async def remove(name: str) -> None:
        removed.append(name)

    monkeypatch.setattr(
        "pallas_plugin_protocol.snowluma_docker.snowluma_docker_remove_force",
        remove,
    )
    monkeypatch.setattr(
        "pallas_plugin_protocol.snowluma_docker.snowluma_docker_container_exists_sync",
        lambda name: False,
    )

    assert await service.cleanup_stale_snowluma_runtime_containers() == []
    assert removed == []
    assert service._sl_runtime_registry.get(runtime["id"]) is not None
    assert service._accounts[account["id"]] is account


@pytest.mark.asyncio
async def test_cleanup_keeps_shared_runtime_when_any_member_has_ws(monkeypatch) -> None:
    last_connected = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    runtime = {
        "id": "sl-rt-shared",
        "data_dir": "/tmp/shared",
        "webui_port": 6200,
        "last_ws_connected_at": last_connected,
    }
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._accounts["10002"] = {
        "id": "10002",
        "qq": "10002",
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    }
    service._is_bot_connected = lambda member: member is service._accounts["10002"]
    removed: list[str] = []

    async def remove(name: str) -> None:
        removed.append(name)

    monkeypatch.setattr(
        "pallas_plugin_protocol.snowluma_docker.snowluma_docker_remove_force",
        remove,
    )

    assert await service.cleanup_stale_snowluma_runtime_containers() == []
    assert service._sl_runtime_registry.get(runtime["id"])["last_ws_connected_at"] != last_connected
    assert removed == []


@pytest.mark.asyncio
async def test_stale_cleanup_scheduler_runs_once_then_can_be_cancelled(monkeypatch) -> None:
    runtime = {"id": "sl-rt-scheduled", "data_dir": "/tmp/scheduled", "webui_port": 6200}
    service, _ = make_service(runtime)
    completed = asyncio.Event()
    calls: list[None] = []

    async def cleanup() -> list[str]:
        calls.append(None)
        completed.set()
        return []

    monkeypatch.setattr(service, "cleanup_stale_snowluma_runtime_containers", cleanup)

    service.schedule_snowluma_stale_cleanup()
    await asyncio.wait_for(completed.wait(), timeout=1)
    service.cancel_snowluma_stale_cleanup()

    assert calls == [None]
