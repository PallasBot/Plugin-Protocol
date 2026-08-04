"""SnowLuma Runtime 镜像批量切换服务测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import MethodType

import pytest
from test_account_runtime_switch import SNOWLUMA_PROTOCOL_BACKEND, make_service

from pallas_plugin_protocol.snowluma_image_switch_jobs import SnowLumaImageSwitchCoordinator


async def record_start_runtime(service, runtime_id: str) -> dict:
    service.calls.append(f"start-runtime:{runtime_id}")
    return {}


async def record_stop_runtime(service, runtime_id: str) -> dict:
    service.calls.append(f"stop-runtime:{runtime_id}")
    return {}


@pytest.mark.asyncio
async def test_rebuild_image_switch_deduplicates_shared_runtime_and_continues_errors(monkeypatch) -> None:
    runtime = {"id": "sl-rt-shared", "data_dir": "/tmp/shared", "webui_port": 6200}
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
    service._snowluma_image_switch = SnowLumaImageSwitchCoordinator()
    service._snowluma_runtime_process_running = lambda *_args, **_kwargs: True
    service.start_snowluma_runtime_for_image_switch = MethodType(record_start_runtime, service)
    service.stop_snowluma_runtime = MethodType(record_stop_runtime, service)

    docker = type(sys)("pallas_plugin_protocol.snowluma_docker")
    docker.resolve_snowluma_run_image = lambda **_kwargs: "pallas/snowluma-auto-login:test"
    docker.snowluma_docker_image_exists = lambda _image: (True, "")
    docker.snowluma_docker_container_name_for_runtime = lambda item: f"snowluma-{item['id']}"
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.snowluma_docker", docker)

    docker_cli = type(sys)("pallas_plugin_protocol.docker_cli")

    async def stop(name: str) -> None:
        service.calls.append(f"stop:{name}")

    async def remove(name: str) -> None:
        service.calls.append(f"remove:{name}")

    docker_cli.docker_stop_strict_async = stop
    docker_cli.docker_rm_force_strict_async = remove
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.docker_cli", docker_cli)

    job_id = await service.start_snowluma_runtime_image_switch("snowluma:test", "rebuild_all")
    task = service._snowluma_image_switch._tasks[job_id]
    await task
    job = service.snowluma_image_switch_coordinator().get_job(job_id)

    assert job is not None
    assert job.status == "completed"
    assert job.results == [
        {
            "id": "sl-rt-shared",
            "was_running": True,
            "config_saved": True,
            "stopped": True,
            "removed": True,
            "started": True,
            "final_state": "running",
        }
    ]
    assert service._sl_runtime_registry.runtime["snowluma_docker_image"] == "snowluma:test"
    assert service.calls.count("stop-runtime:sl-rt-shared") == 1
    assert service.calls.count("start-runtime:sl-rt-shared") == 1


@pytest.mark.asyncio
async def test_next_start_image_switch_skips_empty_runtime() -> None:
    runtime = {"id": "sl-rt-empty", "data_dir": str(Path("/tmp/empty")), "webui_port": 6200}
    service, _ = make_service(runtime)
    service._accounts = {}
    service._snowluma_image_switch = SnowLumaImageSwitchCoordinator()

    job_id = await service.start_snowluma_runtime_image_switch("snowluma:test", "next_start")
    await service._snowluma_image_switch._tasks[job_id]
    job = service.snowluma_image_switch_coordinator().get_job(job_id)

    assert job is not None
    assert job.results[0]["final_state"] == "configured_empty"
    assert job.results[0]["config_saved"] is True
    assert service._sl_runtime_registry.runtime["snowluma_docker_image"] == "snowluma:test"


@pytest.mark.asyncio
async def test_rebuild_rejects_missing_local_image_without_stopping_runtime(monkeypatch) -> None:
    runtime = {"id": "sl-rt-one", "data_dir": "/tmp/one", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._snowluma_image_switch = SnowLumaImageSwitchCoordinator()
    service.stop_snowluma_runtime = MethodType(record_stop_runtime, service)
    docker = type(sys)("pallas_plugin_protocol.snowluma_docker")
    docker.resolve_snowluma_run_image = lambda **_kwargs: "pallas/snowluma-auto-login:missing"
    docker.snowluma_docker_container_name_for_runtime = lambda item: f"snowluma-{item['id']}"
    docker.snowluma_docker_remove_force = lambda _name: __import__("asyncio").sleep(0)
    docker.snowluma_docker_image_exists = lambda _image: (False, "请先拉取或构建镜像")
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.snowluma_docker", docker)

    job_id = await service.start_snowluma_runtime_image_switch("snowluma:missing", "rebuild_all")
    await service._snowluma_image_switch._tasks[job_id]
    job = service.snowluma_image_switch_coordinator().get_job(job_id)

    assert job is not None
    assert job.status == "failed"
    assert "请先拉取或构建镜像" in job.message
    assert service.calls == []
    assert "snowluma_docker_image" not in service._sl_runtime_registry.runtime


@pytest.mark.asyncio
async def test_rebuild_start_failure_reports_ready_next_start(monkeypatch) -> None:
    runtime = {"id": "sl-rt-one", "data_dir": "/tmp/one", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._snowluma_image_switch = SnowLumaImageSwitchCoordinator()
    service._snowluma_runtime_process_running = lambda *_args, **_kwargs: True
    service.stop_snowluma_runtime = MethodType(record_stop_runtime, service)

    async def fail_start(_service, _runtime_id: str) -> dict:
        raise RuntimeError("docker build must not run")

    service.start_snowluma_runtime_for_image_switch = MethodType(fail_start, service)
    docker = type(sys)("pallas_plugin_protocol.snowluma_docker")
    docker.resolve_snowluma_run_image = lambda **_kwargs: "pallas/snowluma-auto-login:test"
    docker.snowluma_docker_image_exists = lambda _image: (True, "")
    docker.snowluma_docker_container_name_for_runtime = lambda item: f"snowluma-{item['id']}"
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.snowluma_docker", docker)
    docker_cli = type(sys)("pallas_plugin_protocol.docker_cli")
    docker_cli.docker_stop_strict_async = lambda _name: __import__("asyncio").sleep(0)
    docker_cli.docker_rm_force_strict_async = lambda _name: __import__("asyncio").sleep(0)
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.docker_cli", docker_cli)

    job_id = await service.start_snowluma_runtime_image_switch("snowluma:test", "rebuild_all")
    await service._snowluma_image_switch._tasks[job_id]
    job = service.snowluma_image_switch_coordinator().get_job(job_id)

    assert job is not None
    assert job.status == "completed_with_errors"
    assert job.results[0]["stopped"] is True
    assert job.results[0]["removed"] is True
    assert job.results[0]["started"] is False
    assert job.results[0]["final_state"] == "failed_after_container_removed"
    assert job.results[0]["error_stage"] == "start"
    assert "启动 Runtime 失败" in job.results[0]["error"]


@pytest.mark.asyncio
async def test_rebuild_stop_failure_reports_container_may_still_run(monkeypatch) -> None:
    runtime = {"id": "sl-rt-one", "data_dir": "/tmp/one", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._snowluma_image_switch = SnowLumaImageSwitchCoordinator()
    service._snowluma_runtime_process_running = lambda *_args, **_kwargs: True

    service.stop_snowluma_runtime = MethodType(record_stop_runtime, service)
    docker = type(sys)("pallas_plugin_protocol.snowluma_docker")
    docker.resolve_snowluma_run_image = lambda **_kwargs: "pallas/snowluma-auto-login:test"
    docker.snowluma_docker_image_exists = lambda _image: (True, "")
    docker.snowluma_docker_container_name_for_runtime = lambda item: f"snowluma-{item['id']}"
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.snowluma_docker", docker)
    docker_cli = type(sys)("pallas_plugin_protocol.docker_cli")

    async def strict_stop(_name: str) -> None:
        raise RuntimeError("Docker stop 失败 (exit 1)：daemon denied")

    docker_cli.docker_stop_strict_async = strict_stop
    docker_cli.docker_rm_force_strict_async = lambda _name: __import__("asyncio").sleep(0)
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.docker_cli", docker_cli)

    job_id = await service.start_snowluma_runtime_image_switch("snowluma:test", "rebuild_all")
    await service._snowluma_image_switch._tasks[job_id]
    job = service.snowluma_image_switch_coordinator().get_job(job_id)

    assert job is not None
    assert job.status == "completed_with_errors"
    assert job.results[0]["config_saved"] is True
    assert job.results[0]["stopped"] is False
    assert job.results[0]["removed"] is False
    assert job.results[0]["started"] is False
    assert job.results[0]["final_state"] == "failed_container_may_still_run"
    assert job.results[0]["error_stage"] == "stop"
    assert "daemon denied" in job.results[0]["error"]


@pytest.mark.asyncio
async def test_rebuild_remove_failure_reports_unknown_container_state(monkeypatch) -> None:
    runtime = {"id": "sl-rt-one", "data_dir": "/tmp/one", "webui_port": 6200}
    service, account = make_service(runtime)
    account.update({
        "protocol_backend": SNOWLUMA_PROTOCOL_BACKEND,
        "snowluma_runtime_id": runtime["id"],
        "snowluma_linux_docker": True,
    })
    service._snowluma_image_switch = SnowLumaImageSwitchCoordinator()
    service._snowluma_runtime_process_running = lambda *_args, **_kwargs: True
    service.stop_snowluma_runtime = MethodType(record_stop_runtime, service)
    docker = type(sys)("pallas_plugin_protocol.snowluma_docker")
    docker.resolve_snowluma_run_image = lambda **_kwargs: "pallas/snowluma-auto-login:test"
    docker.snowluma_docker_image_exists = lambda _image: (True, "")
    docker.snowluma_docker_container_name_for_runtime = lambda item: f"snowluma-{item['id']}"
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.snowluma_docker", docker)
    docker_cli = type(sys)("pallas_plugin_protocol.docker_cli")
    docker_cli.docker_stop_strict_async = lambda _name: __import__("asyncio").sleep(0)

    async def fail_remove(_name: str) -> None:
        raise RuntimeError("Docker rm -f 超时（120s）")

    docker_cli.docker_rm_force_strict_async = fail_remove
    monkeypatch.setitem(sys.modules, "pallas_plugin_protocol.docker_cli", docker_cli)

    job_id = await service.start_snowluma_runtime_image_switch("snowluma:test", "rebuild_all")
    await service._snowluma_image_switch._tasks[job_id]
    job = service.snowluma_image_switch_coordinator().get_job(job_id)

    assert job is not None
    assert job.status == "completed_with_errors"
    assert job.results[0]["config_saved"] is True
    assert job.results[0]["stopped"] is True
    assert job.results[0]["removed"] is False
    assert job.results[0]["started"] is False
    assert job.results[0]["final_state"] == "failed_after_remove_attempt"
    assert job.results[0]["error_stage"] == "remove"
    assert "超时" in job.results[0]["error"]


@pytest.mark.asyncio
async def test_image_switch_coordinator_rejects_concurrent_job() -> None:
    coordinator = SnowLumaImageSwitchCoordinator()
    hold = __import__("asyncio").Event()

    async def wait_for_release(_job) -> None:
        await hold.wait()

    results = await __import__("asyncio").gather(
        coordinator.start_job(image="snowluma:test", apply_mode="next_start", run_fn=wait_for_release),
        coordinator.start_job(image="snowluma:other", apply_mode="next_start", run_fn=wait_for_release),
        return_exceptions=True,
    )

    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    hold.set()
    await next(iter(coordinator._tasks.values()))
