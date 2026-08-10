"""账号协议端原子切换的服务层测试。"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"
_PACKAGE = types.ModuleType("pallas_plugin_protocol")
_PACKAGE.__path__ = [str(_ROOT)]
sys.modules["pallas_plugin_protocol"] = _PACKAGE

_PALLAS = types.ModuleType("pallas")
_PALLAS.__path__ = []
_PALLAS_API = types.ModuleType("pallas.api")
_PALLAS_API.__path__ = []
_PALLAS_PATHS = types.ModuleType("pallas.api.paths")
_PALLAS_PATHS.resource_dir = lambda: Path("/tmp")
_PALLAS_CONFIG = types.ModuleType("pallas.api.config")
_PALLAS_CONFIG.field_help = lambda title, detail: f"{title}: {detail}"
_PALLAS_CONFIG.install_hot_reload_config = lambda *args, **kwargs: types.SimpleNamespace(get=lambda: None)
_PALLAS_CONFIG.plugin_config_proxy = lambda *args, **kwargs: None
_PALLAS_UTILS = types.ModuleType("pallas.api.utils")
_PALLAS_UTILS.fetch_github_releases = lambda *args, **kwargs: []
_PALLAS_UTILS.github_auth_headers = lambda *args, **kwargs: {}
_PALLAS_UTILS.github_release_api_url = lambda *args, **kwargs: ""
_PALLAS_UTILS.github_release_asset_url = lambda *args, **kwargs: ""
_PALLAS_UTILS.StreamDownloadProgress = dict
_PALLAS_UTILS.format_download_byte_size = lambda value: str(value)
_PALLAS_UTILS.sync_stream_download_to_file = lambda *args, **kwargs: None
sys.modules.update({
    "pallas": _PALLAS,
    "pallas.api": _PALLAS_API,
    "pallas.api.paths": _PALLAS_PATHS,
    "pallas.api.config": _PALLAS_CONFIG,
    "pallas.api.utils": _PALLAS_UTILS,
})

import pallas_plugin_protocol.service as service_module  # noqa: E402
from pallas_plugin_protocol.contract import (  # noqa: E402
    DEFAULT_PROTOCOL_BACKEND,
    SNOWLUMA_PROTOCOL_BACKEND,
)
from pallas_plugin_protocol.service import PallasProtocolService  # noqa: E402


class RuntimeRegistry:
    def __init__(self, runtime: dict | None = None) -> None:
        self.runtime = runtime
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def get(self, runtime_id: str) -> dict | None:
        if self.runtime and runtime_id == self.runtime["id"]:
            return dict(self.runtime)
        return None

    def create(self, payload: dict) -> dict:
        runtime = {
            "id": "sl-rt-new",
            "display_name": payload["display_name"],
            "data_dir": "/tmp/snowluma-runtime",
            "webui_port": 6200,
        }
        image = str(payload.get("snowluma_docker_image", "") or "").strip()
        if image:
            runtime["snowluma_docker_image"] = image
        self.runtime = runtime
        self.created.append(payload)
        return dict(runtime)

    def update(self, runtime_id: str, payload: dict) -> dict:
        if not self.runtime or runtime_id != self.runtime["id"]:
            raise KeyError("Runtime 不存在")
        for key, val in payload.items():
            if val is None or (isinstance(val, str) and not str(val).strip()):
                self.runtime.pop(key, None)
            else:
                self.runtime[key] = val
        return dict(self.runtime)

    def list_runtimes(self) -> list[dict]:
        return [dict(self.runtime)] if self.runtime else []

    def delete(self, runtime_id: str) -> None:
        self.deleted.append(runtime_id)
        if self.runtime and self.runtime["id"] == runtime_id:
            self.runtime = None


class Backend:
    def __init__(self, calls: list[str], *, allocate_ports: bool = False) -> None:
        self.calls = calls
        self.allocate_ports = allocate_ports

    def apply_defaults(self, account: dict, resolve_qq: object) -> None:
        self.calls.append("defaults")
        if self.allocate_ports:
            account["snowluma_linux_docker"] = True
            account["snowluma_docker_host_onebot_http"] = 17100
            account["snowluma_docker_host_onebot_ws"] = 17101
            if "webui_port" not in account:
                account["webui_port"] = 6200

    def prepare_dirs(self, account: dict) -> None:
        self.calls.append("prepare")

    def sync_all_configs(self, account: dict, resolve_qq: object) -> None:
        self.calls.append("sync")


async def record_stop(service: PallasProtocolService, account_id: str) -> None:
    service.calls.append("stop")


async def record_remove(service: PallasProtocolService, account: dict) -> None:
    service.calls.append("remove-containers")


async def record_stop_runtime(service: PallasProtocolService, runtime_id: str) -> None:
    service.calls.append(f"stop-runtime:{runtime_id}")


async def record_delete_runtime(service: PallasProtocolService, runtime_id: str, *, force: bool = False) -> None:
    service.calls.append(f"delete-runtime:{runtime_id}")
    try:
        service._sl_runtime_registry.delete(runtime_id)
    except KeyError:
        pass


async def record_start(service: PallasProtocolService, account_id: str) -> None:
    service.started.append(account_id)


async def record_restart(service: PallasProtocolService, account_id: str) -> None:
    service.restarted.append(account_id)


def make_service(
    runtime: dict | None = None,
    *,
    allocate_ports: bool = False,
) -> tuple[PallasProtocolService, dict]:
    account = {
        "id": "10001",
        "display_name": "测试号",
        "qq": "10001",
        "protocol_backend": DEFAULT_PROTOCOL_BACKEND,
        "account_data_dir": "/tmp/napcat-data",
        "napcat_linux_docker": True,
    }
    service = PallasProtocolService.__new__(PallasProtocolService)
    service._accounts = {"10001": account}
    service._instances_root = Path("/tmp/instances")
    service._sl_runtime_registry = RuntimeRegistry(runtime)
    service.calls = []
    service.started = []
    service.restarted = []
    service._resolve_qq = lambda item: str(item["qq"])
    service._protocol_runtime_backend = lambda item: Backend(service.calls, allocate_ports=allocate_ports)
    service._refresh_linux_docker_run_argv = lambda item: service.calls.append("docker-argv")
    service._merge_onebot_ws_from_env = lambda item: False
    service._next_free_webui_port = lambda: 6200
    service._compose_account_state = lambda account_id, item: dict(item)
    service._compose_snowluma_runtime_state = lambda item: {
        **item,
        "member_account_ids": ["10001"],
    }
    service.stop_account = MethodType(record_stop, service)
    service.stop_snowluma_runtime = MethodType(record_stop_runtime, service)
    service.delete_snowluma_runtime = MethodType(record_delete_runtime, service)
    service._remove_both_linux_docker_container_names_for_account = MethodType(record_remove, service)
    service._save_accounts = MethodType(lambda self: self.calls.append("save"), service)
    service.start_account = MethodType(record_start, service)
    service.restart_account = MethodType(record_restart, service)
    return service, account


@pytest.mark.asyncio
async def test_update_account_display_name_only_skips_runtime_and_config_work() -> None:
    service, account = make_service()
    service._napcat_core_running = lambda account_id, item=None: service.calls.append("running-check") or False

    result = await service.update_account("10001", {"display_name": "新昵称"}, restart=False)

    assert account["display_name"] == "新昵称"
    assert result["account"]["display_name"] == "新昵称"
    assert result["restarted"] is False
    assert result["needs_restart"] is False
    assert result["hot_reload"] is None
    assert service.calls == ["save"]


@pytest.mark.asyncio
async def test_update_snowluma_ws_settings_hot_reloads_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, account = make_service()
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_linux_docker": True,
        "webui_port": 6500,
        "account_data_dir": str(tmp_path),
    })
    config_path = tmp_path / "docker" / "snowluma" / "snowluma-data" / "config" / "onebot_10001.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"mode": "snapshot", "networks": {"wsClients": []}}),
        encoding="utf-8",
    )
    service._config = SimpleNamespace(pallas_protocol_bind_host="127.0.0.1")
    service._configs = SimpleNamespace(safe_read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")))
    service._napcat_core_running = lambda account_id, item=None: True
    service.tail_logs = lambda account_id, limit: []
    applied: dict[str, object] = {}
    fetched: list[tuple[str, str]] = []

    async def fake_session(client, base, item, logs):
        assert base == "http://127.0.0.1:6500"
        return {"Authorization": "Bearer token"}, False

    async def fake_apply(client, base, headers, uin, config):
        applied.update({"base": base, "headers": headers, "uin": uin, "config": config})
        return {"success": True, "saved": True, "online": True, "applied": True, "reloaded": True}

    async def fake_fetch(client, base, headers, uin):
        fetched.append((base, uin))
        return {
            "message": {"reportSelfMessage": False},
            "networks": {"wsClients": [{"name": "other", "url": "ws://other/ws"}]},
        }

    monkeypatch.setattr(service_module, "snowluma_ensure_webui_session", fake_session)
    monkeypatch.setattr(service_module, "snowluma_fetch_onebot_config", fake_fetch, raising=False)
    monkeypatch.setattr(service_module, "snowluma_apply_onebot_config", fake_apply)

    result = await service.update_account(
        "10001",
        {"ws_url": "ws://172.17.0.1:7999/onebot/v11/ws"},
        restart=True,
    )

    assert service.restarted == []
    assert result["restarted"] is False
    assert result["hot_reload"]["reloaded"] is True
    assert applied["uin"] == "10001"
    assert fetched == [("http://127.0.0.1:6500", "10001")]
    assert applied["config"]["message"] == {"reportSelfMessage": False}
    assert {client["name"] for client in applied["config"]["networks"]["wsClients"]} == {"other", "pallas"}


@pytest.mark.asyncio
async def test_restart_shared_snowluma_runtime_restarts_container() -> None:
    runtime = {"id": "sl-rt-shared", "data_dir": "/tmp/shared", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
        "account_data_dir": runtime["data_dir"],
    })
    service._accounts["10002"] = {
        "id": "10002",
        "qq": "10002",
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
        "account_data_dir": runtime["data_dir"],
    }
    service.restart_account = PallasProtocolService.restart_account.__get__(service)

    await service.restart_account("10001")

    assert service.calls == [f"stop-runtime:{runtime['id']}"]
    assert service.started == ["10001"]


@pytest.mark.asyncio
async def test_switch_account_to_existing_snowluma_runtime_binds_and_restarts() -> None:
    runtime = {"id": "sl-rt-existing", "data_dir": "/tmp/shared", "webui_port": 6200}
    service, account = make_service(runtime)

    result = await service.switch_account_runtime(
        "10001",
        {
            "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
            "runtime_mode": "existing",
            "runtime_id": runtime["id"],
        },
    )

    assert result["account"]["protocol_backend"] == SNOWLUMA_PROTOCOL_BACKEND
    assert account["snowluma_runtime_id"] == runtime["id"]
    assert account["account_data_dir"] == runtime["data_dir"]
    assert account["napcat_account_data_dir"] == "/tmp/napcat-data"
    assert service.started == ["10001"]
    assert service.calls == [
        "stop",
        "remove-containers",
        "defaults",
        "prepare",
        "sync",
        "docker-argv",
        "save",
    ]


@pytest.mark.asyncio
async def test_switch_account_to_existing_snowluma_ignores_payload_docker_image() -> None:
    runtime = {
        "id": "sl-rt-existing",
        "data_dir": "/tmp/shared",
        "webui_port": 6200,
        "snowluma_docker_image": "pallas/snowluma-auto-login:v1.12.8",
    }
    service, account = make_service(runtime)

    await service.switch_account_runtime(
        "10001",
        {
            "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
            "runtime_mode": "existing",
            "runtime_id": runtime["id"],
            "snowluma_docker_image": "pallas/snowluma-auto-login:v1.12.9",
        },
    )

    assert account["snowluma_docker_image"] == "pallas/snowluma-auto-login:v1.12.8"
    assert service._sl_runtime_registry.runtime["snowluma_docker_image"] == ("pallas/snowluma-auto-login:v1.12.8")


@pytest.mark.asyncio
async def test_switch_account_to_new_snowluma_applies_docker_image() -> None:
    service, account = make_service()

    await service.switch_account_runtime(
        "10001",
        {
            "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
            "runtime_mode": "new",
            "snowluma_docker_image": "pallas/snowluma-auto-login:v1.12.9",
        },
    )

    assert account["snowluma_docker_image"] == "pallas/snowluma-auto-login:v1.12.9"
    assert service._sl_runtime_registry.runtime["snowluma_docker_image"] == ("pallas/snowluma-auto-login:v1.12.9")


@pytest.mark.asyncio
async def test_switch_account_to_new_snowluma_runtime_creates_and_binds() -> None:
    service, account = make_service()

    result = await service.switch_account_runtime(
        "10001", {"protocol_backend": SNOWLUMA_PROTOCOL_BACKEND, "runtime_mode": "new"}
    )

    assert result["runtime"]["id"] == account["snowluma_runtime_id"]
    assert result["runtime"]["member_account_ids"] == ["10001"]
    assert account["napcat_account_data_dir"] == "/tmp/napcat-data"


@pytest.mark.asyncio
async def test_switch_account_does_not_store_a_snowluma_directory_as_napcat_data() -> None:
    service, account = make_service()
    account["account_data_dir"] = "/tmp/runtimes/sl-rt-stale"

    await service.switch_account_runtime(
        "10001", {"protocol_backend": SNOWLUMA_PROTOCOL_BACKEND, "runtime_mode": "new"}
    )

    assert "napcat_account_data_dir" not in account


def test_default_protocol_backend_reads_plugin_config() -> None:
    service, _ = make_service()
    service._config = SimpleNamespace(pallas_protocol_default_backend=SNOWLUMA_PROTOCOL_BACKEND)

    assert service.default_protocol_backend() == SNOWLUMA_PROTOCOL_BACKEND


@pytest.mark.asyncio
async def test_switch_account_to_new_snowluma_runtime_syncs_docker_host_ports() -> None:
    service, account = make_service(allocate_ports=True)

    result = await service.switch_account_runtime(
        "10001", {"protocol_backend": SNOWLUMA_PROTOCOL_BACKEND, "runtime_mode": "new"}
    )

    runtime = service._sl_runtime_registry.runtime
    assert runtime is not None
    assert runtime["snowluma_docker_host_onebot_http"] == 17100
    assert runtime["snowluma_docker_host_onebot_ws"] == 17101
    assert account["snowluma_docker_host_onebot_http"] == 17100
    assert result["runtime"]["snowluma_docker_host_onebot_http"] == 17100


@pytest.mark.asyncio
async def test_switch_account_rejects_unknown_snowluma_runtime() -> None:
    service, _ = make_service()

    with pytest.raises(ValueError, match="Runtime 不存在"):
        await service.switch_account_runtime(
            "10001",
            {
                "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
                "runtime_mode": "existing",
                "runtime_id": "missing",
            },
        )


@pytest.mark.asyncio
async def test_switch_account_to_napcat_uses_supplied_docker_image() -> None:
    service, account = make_service()
    account["protocol_backend"] = SNOWLUMA_PROTOCOL_BACKEND
    account["snowluma_runtime_id"] = "sl-rt-existing"
    account["snowluma_linux_docker"] = True

    await service.switch_account_runtime(
        "10001",
        {"protocol_backend": DEFAULT_PROTOCOL_BACKEND, "docker_image": "napcat:test"},
    )

    assert account["protocol_backend"] == DEFAULT_PROTOCOL_BACKEND
    assert account["docker_image"] == "napcat:test"
    assert "snowluma_runtime_id" not in account


def test_docker_runtime_display_prefers_account_docker_image() -> None:
    service = object.__new__(PallasProtocolService)
    account = {
        "protocol_backend": DEFAULT_PROTOCOL_BACKEND,
        "napcat_linux_docker": True,
        "docker_image": "mlikiowa/napcat-docker:v4.4.20",
        "program_dir": "docker:mlikiowa/napcat-docker:v4.18.7",
    }

    assert service._resolve_account_runtime_version(account) == "v4.4.20"
    assert "v4.4.20" in service._resolve_account_runtime_source(account)


@pytest.mark.asyncio
async def test_switch_account_to_napcat_uses_payload_image_in_docker_argv() -> None:
    service, account = make_service()
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": "sl-rt-existing",
        "snowluma_linux_docker": True,
    })
    service._config = types.SimpleNamespace(
        pallas_protocol_docker_image="default:latest",
        pallas_protocol_docker_internal_webui_port=6099,
    )
    service._refresh_linux_docker_run_argv = MethodType(PallasProtocolService._refresh_linux_docker_run_argv, service)

    await service.switch_account_runtime(
        "10001",
        {"protocol_backend": DEFAULT_PROTOCOL_BACKEND, "docker_image": "napcat:test"},
    )

    assert account["args"][-1] == "napcat:test"


@pytest.mark.asyncio
async def test_switch_account_from_shared_snowluma_runtime_keeps_shared_container() -> None:
    runtime = {"id": "sl-rt-existing", "data_dir": "/tmp/shared", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
        "account_data_dir": runtime["data_dir"],
        "napcat_account_data_dir": "/tmp/napcat-data",
    })
    service._accounts["10002"] = {
        "id": "10002",
        "qq": "10002",
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
        "account_data_dir": runtime["data_dir"],
    }

    async def shared_container_must_not_be_removed(self: PallasProtocolService, item: dict) -> None:
        raise AssertionError("不应解析或移除共享 SnowLuma Runtime 容器")

    service._remove_both_linux_docker_container_names_for_account = MethodType(
        shared_container_must_not_be_removed, service
    )

    result = await service.switch_account_runtime("10001", {"protocol_backend": DEFAULT_PROTOCOL_BACKEND})

    assert result["account"]["protocol_backend"] == DEFAULT_PROTOCOL_BACKEND
    assert account["account_data_dir"] == "/tmp/napcat-data"
    assert service.started == ["10001"]
    assert not any(call.startswith("delete-runtime:") for call in service.calls)
    assert not any(call.startswith("stop-runtime:") for call in service.calls)
    assert service._sl_runtime_registry.runtime is not None


@pytest.mark.asyncio
async def test_switching_between_snowluma_runtimes_does_not_capture_snow_data_as_napcat() -> None:
    service, account = make_service({"id": "sl-rt-old", "data_dir": "/tmp/snow-old", "webui_port": 6200})
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": "sl-rt-old",
        "account_data_dir": "/tmp/snow-old",
    })

    await service.switch_account_runtime(
        "10001", {"protocol_backend": SNOWLUMA_PROTOCOL_BACKEND, "runtime_mode": "new"}
    )
    assert "delete-runtime:sl-rt-old" in service.calls
    assert service._sl_runtime_registry.deleted == ["sl-rt-old"]

    await service.switch_account_runtime("10001", {"protocol_backend": DEFAULT_PROTOCOL_BACKEND})

    assert "napcat_account_data_dir" not in account
    assert account["account_data_dir"] == ""


@pytest.mark.asyncio
async def test_switch_from_exclusive_snowluma_stops_and_removes_old_container() -> None:
    runtime = {"id": "sl-rt-old", "data_dir": "/tmp/snow-old", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
        "account_data_dir": runtime["data_dir"],
    })

    await service.switch_account_runtime("10001", {"protocol_backend": DEFAULT_PROTOCOL_BACKEND})

    assert service.calls[:4] == [
        "stop",
        "stop-runtime:sl-rt-old",
        "remove-containers",
        "defaults",
    ]
    assert "delete-runtime:sl-rt-old" in service.calls
    assert service._sl_runtime_registry.deleted == ["sl-rt-old"]


@pytest.mark.asyncio
async def test_switch_rolls_back_account_and_new_runtime_when_config_sync_fails() -> None:
    service, account = make_service()
    snapshot = dict(account)

    class FailingBackend(Backend):
        def sync_all_configs(self, account: dict, resolve_qq: object) -> None:
            super().sync_all_configs(account, resolve_qq)
            raise RuntimeError("sync failed")

    service._protocol_runtime_backend = lambda item: FailingBackend(service.calls)

    with pytest.raises(RuntimeError, match="sync failed"):
        await service.switch_account_runtime(
            "10001",
            {"protocol_backend": SNOWLUMA_PROTOCOL_BACKEND, "runtime_mode": "new"},
        )

    assert account == snapshot
    assert service._sl_runtime_registry.deleted == ["sl-rt-new"]
    assert service.started == ["10001"]


@pytest.mark.asyncio
async def test_switch_rolls_back_account_and_new_runtime_when_start_fails() -> None:
    service, account = make_service()
    snapshot = dict(account)
    attempts = 0

    async def fail_then_restore_start(self: PallasProtocolService, account_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("start failed")
        self.started.append(account_id)

    service.start_account = MethodType(fail_then_restore_start, service)

    with pytest.raises(RuntimeError, match="start failed"):
        await service.switch_account_runtime(
            "10001",
            {"protocol_backend": SNOWLUMA_PROTOCOL_BACKEND, "runtime_mode": "new"},
        )

    assert account == snapshot
    assert service._sl_runtime_registry.deleted == ["sl-rt-new"]
    assert service.started == ["10001"]
