"""SnowLuma Runtime 编排：挂到 PallasProtocolService 的 mixin。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contract import (
    SNOWLUMA_RUNTIME_ID_KEY,
)
from .snowluma_runtime_registry import (
    SnowLumaRuntimeRegistry,
)

if TYPE_CHECKING:
    from .service import NapCatRuntime, PallasProtocolService


def snowluma_process_track_key(runtime_id: str) -> str:
    return f"slrt:{str(runtime_id or '').strip()}"


class SnowLumaRuntimeOpsMixin:
    """由 PallasProtocolService 继承；依赖 self._accounts / self._runtimes 等。"""

    _sl_runtime_registry: SnowLumaRuntimeRegistry

    def _init_snowluma_runtime_registry(self: PallasProtocolService) -> None:
        self._sl_runtime_registry = SnowLumaRuntimeRegistry(self._data_dir, self._instances_root)
        self._sl_runtime_registry.load()

    def _migrate_snowluma_runtimes_on_load(self: PallasProtocolService) -> None:
        if self._sl_runtime_registry.migrate_legacy_accounts(self._accounts):
            self._save_accounts()

    def snowluma_runtime_members(self: PallasProtocolService, runtime_id: str) -> list[str]:
        rid = str(runtime_id or "").strip()
        out: list[str] = []
        for aid, acc in self._accounts.items():
            if str(acc.get(SNOWLUMA_RUNTIME_ID_KEY, "") or "").strip() == rid:
                out.append(aid)
        return out

    def snowluma_runtime_member_uins(self: PallasProtocolService, runtime_id: str) -> list[str]:
        uins: list[str] = []
        seen: set[str] = set()
        for aid in self.snowluma_runtime_members(runtime_id):
            acc = self._accounts.get(aid) or {}
            qq = str(self._resolve_qq(acc) or "").strip()
            if qq.isdigit() and qq not in seen:
                seen.add(qq)
                uins.append(qq)
        return uins

    def snowluma_runtime_primary_uin(self: PallasProtocolService, runtime_id: str) -> str:
        runtime = self._sl_runtime_registry.get(runtime_id) or {}
        legacy = str(runtime.get("legacy_container_account_id") or "").strip()
        if legacy:
            acc = self._accounts.get(legacy)
            if acc:
                qq = str(self._resolve_qq(acc) or "").strip()
                if qq.isdigit():
                    return qq
        members = self.snowluma_runtime_members(runtime_id)
        if members:
            acc = self._accounts.get(members[0]) or {}
            qq = str(self._resolve_qq(acc) or "").strip()
            if qq.isdigit():
                return qq
        return ""

    def annotate_account_snowluma_multi_qq(self: PallasProtocolService, account: dict) -> None:
        """写入 ephemeral 多 QQ 上下文，供 docker argv / 截屏按 UIN 选窗。"""
        rid = str(account.get(SNOWLUMA_RUNTIME_ID_KEY, "") or "").strip()
        qq = str(self._resolve_qq(account) or "").strip()
        if rid:
            members = self.snowluma_runtime_member_uins(rid)
            primary = self.snowluma_runtime_primary_uin(rid)
        else:
            members = [qq] if qq.isdigit() else []
            primary = qq if qq.isdigit() else ""
        if qq.isdigit() and qq not in members:
            members = [*members, qq]
        account["snowluma_member_uins"] = members
        account["snowluma_primary_uin"] = primary or (members[0] if members else "")

    async def ensure_snowluma_qq_process_for_account(
        self: PallasProtocolService,
        account_id: str,
        account: dict | None = None,
    ) -> tuple[bool, int | None, str]:
        acc = account or self._accounts.get(account_id)
        if not acc or not acc.get("snowluma_linux_docker"):
            return False, None, "非 SnowLuma Docker 账号"
        self.annotate_account_snowluma_multi_qq(acc)
        from .snowluma_multi_qq import ensure_snowluma_qq_process_for_uin
        from .snowluma_qr_capture import resolve_snowluma_docker_container_name, snowluma_qr_capture_display

        name = resolve_snowluma_docker_container_name(acc)
        qq = str(self._resolve_qq(acc) or "").strip()
        ok, pid, err = await asyncio.to_thread(
            ensure_snowluma_qq_process_for_uin,
            name,
            qq,
            member_uins=list(acc.get("snowluma_member_uins") or []),
            primary_uin=str(acc.get("snowluma_primary_uin") or "").strip() or None,
            display=snowluma_qr_capture_display(self._config),
        )
        return ok, pid, err

    async def stop_snowluma_qq_process_for_account(
        self: PallasProtocolService,
        account_id: str,
        account: dict | None = None,
    ) -> tuple[bool, int | None, str]:
        """仅停该账号对应 QQ 主进程，不拆共享 Runtime 容器。"""
        acc = account or self._accounts.get(account_id)
        if not acc or not acc.get("snowluma_linux_docker"):
            return False, None, "非 SnowLuma Docker 账号"
        self.annotate_account_snowluma_multi_qq(acc)
        from .snowluma_multi_qq import stop_snowluma_qq_process_for_uin
        from .snowluma_qr_capture import resolve_snowluma_docker_container_name, snowluma_qr_capture_display

        name = resolve_snowluma_docker_container_name(acc)
        qq = str(self._resolve_qq(acc) or "").strip()
        return await asyncio.to_thread(
            stop_snowluma_qq_process_for_uin,
            name,
            qq,
            member_uins=list(acc.get("snowluma_member_uins") or []),
            primary_uin=str(acc.get("snowluma_primary_uin") or "").strip() or None,
            display=snowluma_qr_capture_display(self._config),
        )

    def resolve_snowluma_qq_pid_sync(
        self: PallasProtocolService,
        account: dict,
        *,
        home_pid_cache: dict[str, dict[str, int]] | None = None,
    ) -> int | None:
        """容器内该账号 QQ 主进程 pid；缓存 ``container -> {HOME: pid}`` 供列表复用。"""
        if not account.get("snowluma_linux_docker"):
            return None
        self.annotate_account_snowluma_multi_qq(account)
        from .snowluma_multi_qq import PRIMARY_QQ_HOME, list_qq_main_pids_by_home, resolve_account_qq_home
        from .snowluma_qr_capture import resolve_snowluma_docker_container_name, snowluma_qr_capture_display

        name = resolve_snowluma_docker_container_name(account)
        qq = str(self._resolve_qq(account) or "").strip()
        if not name or not qq.isdigit():
            return None
        display = snowluma_qr_capture_display(self._config)
        mapping: dict[str, int]
        if home_pid_cache is not None and name in home_pid_cache:
            mapping = home_pid_cache[name]
        else:
            mapping = list_qq_main_pids_by_home(name, display=display)
            if home_pid_cache is not None:
                home_pid_cache[name] = mapping
        home = resolve_account_qq_home(
            qq,
            member_uins=list(account.get("snowluma_member_uins") or []),
            primary_uin=str(account.get("snowluma_primary_uin") or "").strip() or None,
        )
        pid = mapping.get(home)
        if pid:
            return pid
        if home == PRIMARY_QQ_HOME:
            for key, value in mapping.items():
                if key in {PRIMARY_QQ_HOME, "/home/snowluma", ""}:
                    return value
        return None

    def resolve_snowluma_runtime(self: PallasProtocolService, account: dict) -> dict | None:
        rid = str(account.get(SNOWLUMA_RUNTIME_ID_KEY, "") or "").strip()
        if not rid:
            return None
        return self._sl_runtime_registry.get(rid)

    def bind_account_to_snowluma_runtime(self: PallasProtocolService, account: dict, runtime: dict) -> None:
        from .snowluma_webui_client import ensure_snowluma_managed_webui_password

        # 仅同步已有托管口令，不在此凭空生成（避免盖住已落盘的 WebUI 凭据）
        if (
            str(runtime.get("snowluma_managed_webui_password") or "").strip()
            or str(account.get("snowluma_managed_webui_password") or "").strip()
        ):
            ensure_snowluma_managed_webui_password(runtime, account)
        account[SNOWLUMA_RUNTIME_ID_KEY] = runtime["id"]
        account["account_data_dir"] = str(runtime.get("data_dir") or "")
        if runtime.get("webui_port") is not None:
            account["webui_port"] = runtime["webui_port"]
        legacy = str(runtime.get("legacy_container_account_id", "") or "").strip()
        if legacy:
            account["snowluma_runtime_legacy_container_account_id"] = legacy
        else:
            account.pop("snowluma_runtime_legacy_container_account_id", None)
        for key in (
            "snowluma_docker_host_onebot_http",
            "snowluma_docker_host_onebot_ws",
            "snowluma_docker_host_novnc_port",
            "snowluma_docker_host_vnc_port",
            "snowluma_managed_webui_password",
            "program_dir",
        ):
            if key in runtime and runtime[key] is not None:
                account[key] = runtime[key]

    def sync_runtime_ports_from_account(self: PallasProtocolService, account: dict) -> None:
        runtime = self.resolve_snowluma_runtime(account)
        if not runtime:
            return
        patch: dict[str, Any] = {}
        for key in (
            "webui_port",
            "snowluma_docker_host_onebot_http",
            "snowluma_docker_host_onebot_ws",
            "snowluma_docker_host_novnc_port",
            "snowluma_docker_host_vnc_port",
            "snowluma_managed_webui_password",
        ):
            if key in account and account[key] is not None:
                patch[key] = account[key]
        if patch:
            self._sl_runtime_registry.update(runtime["id"], patch)

    def _snowluma_proc_runtime(self: PallasProtocolService, runtime_id: str) -> NapCatRuntime:
        key = snowluma_process_track_key(runtime_id)
        return self._runtime(key)

    def list_snowluma_runtimes(
        self: PallasProtocolService,
        *,
        include_process: bool = True,
    ) -> list[dict]:
        """列出 Runtime。

        include_process=False 时跳过 docker/进程探测（选 Runtime 的 Combobox 用），
        避免每个 Runtime 一次 docker inspect。
        """
        members_by_rid: dict[str, list[str]] = {}
        for aid, acc in self._accounts.items():
            rid = str(acc.get(SNOWLUMA_RUNTIME_ID_KEY, "") or "").strip()
            if rid:
                members_by_rid.setdefault(rid, []).append(aid)

        snow_mode = ""
        if include_process:
            profile = self.runtime_profile()
            snow_mode = str(profile.get("snowluma_runtime_mode") or "").strip().lower()

        out: list[dict] = []
        for item in self._sl_runtime_registry.list_runtimes():
            rid = str(item.get("id", ""))
            members = members_by_rid.get(rid, [])
            process_running = False
            if include_process:
                process_running = self._snowluma_runtime_process_running(
                    item,
                    members=members,
                    snow_mode=snow_mode,
                )
            out.append({
                **item,
                "member_account_ids": members,
                "member_count": len(members),
                "process_running": process_running,
            })
        return out

    def get_snowluma_runtime(self: PallasProtocolService, runtime_id: str) -> dict | None:
        item = self._sl_runtime_registry.get(runtime_id)
        if not item:
            return None
        return self._compose_snowluma_runtime_state(item)

    def _compose_snowluma_runtime_state(self: PallasProtocolService, runtime: dict) -> dict:
        rid = str(runtime.get("id", ""))
        members = self.snowluma_runtime_members(rid)
        process_running = self._snowluma_runtime_process_running(runtime, members=members)
        return {
            **runtime,
            "member_account_ids": members,
            "member_count": len(members),
            "process_running": process_running,
        }

    def _snowluma_runtime_process_running(
        self: PallasProtocolService,
        runtime: dict,
        *,
        members: list[str] | None = None,
        snow_mode: str | None = None,
    ) -> bool:
        from .snowluma_docker import (
            snowluma_docker_container_name_for_runtime,
            snowluma_docker_container_running_sync,
        )

        rid = str(runtime.get("id", ""))
        member_ids = members if members is not None else self.snowluma_runtime_members(rid)
        mode = snow_mode
        if mode is None:
            profile = self.runtime_profile()
            mode = str(profile.get("snowluma_runtime_mode") or "").strip().lower()
        if mode == "docker" or any(
            bool((self._accounts.get(aid) or {}).get("snowluma_linux_docker")) for aid in member_ids
        ):
            name = snowluma_docker_container_name_for_runtime(runtime)
            return snowluma_docker_container_running_sync(name)
        track = self._runtimes.get(snowluma_process_track_key(rid))
        return bool(track and track.process and track.process.returncode is None)

    def create_snowluma_runtime(self: PallasProtocolService, payload: dict) -> dict:
        if "webui_port" not in payload or payload.get("webui_port") in (None, ""):
            payload = {**payload, "webui_port": self._next_free_webui_port()}
        from .snowluma_webui_client import ensure_snowluma_managed_webui_password

        ensure_snowluma_managed_webui_password(payload)
        item = self._sl_runtime_registry.create(payload)
        return self._compose_snowluma_runtime_state(item)

    def update_snowluma_runtime(self: PallasProtocolService, runtime_id: str, payload: dict) -> dict:
        item = self._sl_runtime_registry.update(runtime_id, payload)
        for aid in self.snowluma_runtime_members(runtime_id):
            acc = self._accounts.get(aid)
            if acc:
                self.bind_account_to_snowluma_runtime(acc, item)
        self._save_accounts()
        return self._compose_snowluma_runtime_state(item)

    async def delete_snowluma_runtime(self: PallasProtocolService, runtime_id: str, *, force: bool = False) -> None:
        members = self.snowluma_runtime_members(runtime_id)
        if members and not force:
            raise ValueError(f"Runtime 仍有 {len(members)} 个账号，请先删除账号或传 force=true")
        runtime = self._sl_runtime_registry.get(runtime_id)
        if not runtime:
            raise KeyError("Runtime 不存在")
        await self.stop_snowluma_runtime(runtime_id)
        from .snowluma_docker import (
            snowluma_docker_container_name_for_runtime,
            snowluma_docker_remove_force,
        )

        try:
            await snowluma_docker_remove_force(snowluma_docker_container_name_for_runtime(runtime))
        except Exception:
            pass
        for aid in list(members):
            try:
                await self.delete_account(aid)
            except Exception:
                self._accounts.pop(aid, None)
        data_dir = Path(str(runtime.get("data_dir", "") or "").strip())
        self._sl_runtime_registry.delete(runtime_id)
        self._runtimes.pop(snowluma_process_track_key(runtime_id), None)
        if await asyncio.to_thread(data_dir.is_dir):
            try:
                import shutil

                resolved = await asyncio.to_thread(data_dir.resolve)
                root = await asyncio.to_thread(self._instances_root.resolve)
                if resolved == root or root in resolved.parents:
                    await asyncio.to_thread(shutil.rmtree, resolved, ignore_errors=True)
            except OSError:
                pass
        self._save_accounts()

    async def start_snowluma_runtime(self: PallasProtocolService, runtime_id: str) -> dict:
        runtime = self._sl_runtime_registry.get(runtime_id)
        if not runtime:
            raise KeyError("Runtime 不存在")
        members = self.snowluma_runtime_members(runtime_id)
        if not members:
            raise ValueError("Runtime 没有挂载账号，无法启动")
        seed_id = members[0]
        seed = self._accounts[seed_id]
        self.bind_account_to_snowluma_runtime(seed, runtime)
        self.annotate_account_snowluma_multi_qq(seed)
        await self.start_account(seed_id)
        for aid in members[1:]:
            acc = self._accounts.get(aid)
            if not acc or not acc.get("enabled", True):
                continue
            be = self._protocol_runtime_backend(acc)
            self.bind_account_to_snowluma_runtime(acc, runtime)
            self.annotate_account_snowluma_multi_qq(acc)
            be.apply_defaults(acc, self._resolve_qq)
            be.prepare_dirs(acc)
            be.sync_all_configs(acc, self._resolve_qq)
            try:
                await self.ensure_snowluma_qq_process_for_account(aid, acc)
            except Exception as err:
                from nonebot import logger

                logger.warning(
                    "Pallas-Bot 协议端: 共享 Runtime 副号 {} 拉起 QQ 失败：{}",
                    aid,
                    err,
                )
            # Hook 交给 SnowLuma SNOWLUMA_HOOK_AUTOLOAD，避免 WebUI 登录撞 429
            self.schedule_snowluma_auto_quick_login(aid)
        self._save_accounts()
        return self.get_snowluma_runtime(runtime_id) or {}

    async def stop_snowluma_runtime(self: PallasProtocolService, runtime_id: str) -> dict | None:
        runtime = self._sl_runtime_registry.get(runtime_id)
        if not runtime:
            return None
        members = self.snowluma_runtime_members(runtime_id)
        seed = self._accounts.get(members[0]) if members else None
        track_key = snowluma_process_track_key(runtime_id)
        track = self._runtimes.get(track_key)

        use_docker = bool(seed and seed.get("snowluma_linux_docker"))
        if use_docker and seed:
            from .snowluma_docker import (
                snowluma_docker_container_name_for_runtime,
                snowluma_docker_stop,
            )

            name = snowluma_docker_container_name_for_runtime(runtime)
            for aid in members:
                pending = self._snowluma_auto_login_tasks.pop(aid, None)
                if pending is not None and not pending.done():
                    pending.cancel()
            proc_rt = track or self._runtime(track_key)
            async with proc_rt.lock:
                if proc_rt.drain_task and not proc_rt.drain_task.done():
                    proc_rt.drain_task.cancel()
                proc = proc_rt.process
                if proc and proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except Exception:
                        pass
                proc_rt.process = None
                await snowluma_docker_stop(name)
                proc_rt.docker_container_name = None
        elif track:
            async with track.lock:
                proc = track.process
                if proc and proc.returncode is None:
                    if proc.pid:
                        await asyncio.to_thread(self._launch.kill_process_tree, proc.pid)
                    else:
                        proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=12)
                    except TimeoutError:
                        proc.kill()
                        await proc.wait()
                if track.drain_task and not track.drain_task.done():
                    track.drain_task.cancel()
                track.process = None
        return self.get_snowluma_runtime(runtime_id)

    def ensure_account_snowluma_runtime(
        self: PallasProtocolService, account: dict, payload: dict | None = None
    ) -> dict:
        """创建账号时解析或新建 Runtime，并把账号绑上去。"""
        payload = payload or {}
        rid = str(payload.get(SNOWLUMA_RUNTIME_ID_KEY) or account.get(SNOWLUMA_RUNTIME_ID_KEY) or "").strip()
        if rid:
            runtime = self._sl_runtime_registry.get(rid)
            if not runtime:
                raise ValueError(f"SnowLuma Runtime 不存在: {rid}")
            self.bind_account_to_snowluma_runtime(account, runtime)
            return runtime
        if bool(payload.get("create_runtime", True)):
            rt_payload: dict[str, Any] = {
                "display_name": str(
                    payload.get("runtime_display_name")
                    or account.get("display_name")
                    or account.get("id")
                    or "SnowLuma"
                ).strip(),
            }
            if str(payload.get("account_data_dir", "") or "").strip():
                rt_payload["data_dir"] = str(payload["account_data_dir"]).strip()
            if payload.get("webui_port") is not None:
                rt_payload["webui_port"] = payload["webui_port"]
            runtime = self._sl_runtime_registry.create({
                **rt_payload,
                "webui_port": rt_payload.get("webui_port") or self._next_free_webui_port(),
            })
            self.bind_account_to_snowluma_runtime(account, runtime)
            return runtime
        raise ValueError("SnowLuma 账号需要 snowluma_runtime_id 或 create_runtime")

    def account_shares_snowluma_runtime(self: PallasProtocolService, account: dict) -> bool:
        rid = str(account.get(SNOWLUMA_RUNTIME_ID_KEY, "") or "").strip()
        if not rid:
            return False
        return len(self.snowluma_runtime_members(rid)) > 1

    def linux_docker_container_name_for_account(self: PallasProtocolService, account: dict) -> str:
        if account.get("snowluma_linux_docker"):
            from .snowluma_docker import (
                snowluma_docker_container_name,
                snowluma_docker_container_name_for_runtime,
            )

            runtime = self.resolve_snowluma_runtime(account)
            if runtime:
                return snowluma_docker_container_name_for_runtime(runtime)
            return snowluma_docker_container_name(account)
        from .linux_docker import docker_container_name

        return docker_container_name(account)
