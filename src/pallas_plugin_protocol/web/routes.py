"""HTTP 路由注册：与 ``PallasProtocolService`` 通过参数注入耦合，便于测试与替换 UI。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse, urlunparse

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.background import BackgroundTask

if TYPE_CHECKING:
    from ..config import Config
    from ..service import PallasProtocolService


def register_pallas_protocol_routes(
    app: FastAPI,
    *,
    manager: PallasProtocolService,
    plugin_config: Config,
) -> None:
    from ..config import resolve_protocol_webui_base_path

    base = resolve_protocol_webui_base_path(plugin_config)

    def _pallas_console_http_base() -> str:
        try:
            from packages.pb_webui.config import get_pallas_webui_config

            raw = (get_pallas_webui_config().pallas_webui_http_base or "").strip()
        except Exception:
            raw = ""
        if not raw:
            raw = "/pallas"
        if not raw.startswith("/"):
            raw = "/" + raw
        return raw.rstrip("/") or "/pallas"

    def _redirect_to_console_protocol(
        subpath: str = "",
        *,
        query: str = "",
    ) -> RedirectResponse:
        """协议独立 HTML 页已并入 Bot WebUI，旧书签重定向到 ``/pallas/protocol``。"""
        console_base = _pallas_console_http_base()
        dest = f"{console_base}/protocol"
        sub = (subpath or "").strip()
        if sub:
            dest += sub if sub.startswith("/") else f"/{sub}"
        q = (query or "").strip().lstrip("?")
        if q:
            dest = f"{dest}?{q}"
        return RedirectResponse(url=dest, status_code=307)

    from pallas.console.webui.console_login import (
        install_pallas_http_request_context_middleware,
    )

    install_pallas_http_request_context_middleware(app)

    b_norm = base.rstrip("/")
    if b_norm != "/protocol/napcat":

        def _redirect_protocol_napcat_bookmark(request: Request, rest: str) -> RedirectResponse:
            """旧默认 ``/protocol/napcat`` 书签 → 当前协议管理基路径。"""
            parsed = urlparse(str(request.url))
            suffix = ("/" + rest.lstrip("/")) if (rest or "").strip() else "/"
            new_path = b_norm + suffix
            dest = urlunparse((
                parsed.scheme,
                parsed.netloc,
                new_path,
                "",
                parsed.query,
                parsed.fragment,
            ))
            return RedirectResponse(url=dest, status_code=307)

        @app.get("/protocol/napcat")
        async def _legacy_protocol_napcat_root(request: Request) -> RedirectResponse:
            return _redirect_protocol_napcat_bookmark(request, "")

        @app.get("/protocol/napcat/{rest:path}")
        async def _legacy_protocol_napcat_subpath(request: Request, rest: str) -> RedirectResponse:
            return _redirect_protocol_napcat_bookmark(request, rest)

    def _auth(
        h: str | None,
        q: str | None,
        *,
        request: Request | None = None,
    ) -> None:
        from pallas.console.webui.console_login import (
            current_http_request,
            extract_session_from_request,
            is_console_auth_configured,
        )

        req = request if request is not None else current_http_request()
        if req is None:
            raise HTTPException(status_code=500, detail="协议端鉴权缺少请求上下文")
        if extract_session_from_request(
            cookies=dict(req.cookies),
            header_token=h,
            query_token=q,
        ):
            return
        if not is_console_auth_configured():
            raise HTTPException(
                status_code=503,
                detail="统一控制台鉴权未初始化，请检查 data/pallas_console/",
            )
        raise HTTPException(status_code=401, detail="未登录或会话已失效，请重新登录")

    async def _proxy_instance_http(
        request: Request,
        account_id: str,
        surface: str,
        subpath: str,
    ) -> StreamingResponse:
        from ..instance_web_proxy import resolve_instance_proxy_target

        _auth(None, None, request=request)
        account = await asyncio.to_thread(manager.get_account, account_id, brief=True)
        if account is None:
            raise HTTPException(status_code=404, detail="协议账号不存在")
        if not account.get("running"):
            raise HTTPException(status_code=503, detail="协议实例未运行")
        try:
            target = resolve_instance_proxy_target(
                account,
                surface=surface,  # type: ignore[arg-type]
                config=getattr(manager, "_config", plugin_config),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        path = target.base_path.rstrip("/") + "/" + subpath.lstrip("/")
        url = f"{target.origin}{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        client = httpx.AsyncClient(follow_redirects=False, timeout=None)
        upstream = await client.send(
            client.build_request(
                request.method,
                url,
                headers=headers,
                content=await request.body(),
            ),
            stream=True,
        )

        async def close_upstream() -> None:
            await upstream.aclose()
            await client.aclose()

        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in {"connection", "transfer-encoding"}
        }
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(close_upstream),
        )

    console_base = _pallas_console_http_base()

    @app.api_route(
        f"{console_base}/protocol/instances/{{account_id}}/{{surface}}/{{subpath:path}}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def proxy_instance_http(
        request: Request,
        account_id: str,
        surface: str,
        subpath: str,
    ) -> StreamingResponse:
        return await _proxy_instance_http(request, account_id, surface, subpath)

    @app.websocket(f"{console_base}/protocol/instances/{{account_id}}/{{surface}}/{{subpath:path}}")
    async def proxy_instance_websocket(
        websocket: WebSocket,
        account_id: str,
        surface: str,
        subpath: str,
    ) -> None:
        from pallas.console.webui.console_login import extract_session_from_request

        if not extract_session_from_request(
            cookies=dict(websocket.cookies),
            header_token=websocket.headers.get("X-Pallas-Protocol-Token"),
            query_token=websocket.query_params.get("token"),
        ):
            await websocket.close(code=4401)
            return

        from ..instance_web_proxy import resolve_instance_proxy_target

        account = await asyncio.to_thread(manager.get_account, account_id, brief=True)
        if account is None or not account.get("running"):
            await websocket.close(code=1013)
            return
        try:
            target = resolve_instance_proxy_target(
                account,
                surface=surface,  # type: ignore[arg-type]
                config=getattr(manager, "_config", plugin_config),
            )
        except ValueError:
            await websocket.close(code=1008)
            return

        import contextlib

        import websockets

        path = target.base_path.rstrip("/") + "/" + subpath.lstrip("/")
        upstream_url = f"ws://127.0.0.1:{target.origin.rsplit(':', 1)[1]}{path}"
        if websocket.url.query:
            upstream_url = f"{upstream_url}?{websocket.url.query}"
        try:
            async with websockets.connect(upstream_url, proxy=None) as upstream:
                await websocket.accept()

                async def browser_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                        elif message.get("text") is not None:
                            await upstream.send(message["text"])

                async def upstream_to_browser() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                first, second = await asyncio.wait(
                    [
                        asyncio.create_task(browser_to_upstream()),
                        asyncio.create_task(upstream_to_browser()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in second:
                    task.cancel()
                for task in first | second:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        except OSError:
            await websocket.close(code=1011)

    def _redirect_legacy_protocol_page(
        request: Request,
        *,
        target: str = "",
        query: str = "",
    ) -> RedirectResponse:
        return _redirect_to_console_protocol(target, query=query or str(request.url.query or ""))

    @app.get(base)
    @app.get(f"{base}/")
    async def legacy_protocol_dashboard(request: Request) -> RedirectResponse:
        return _redirect_legacy_protocol_page(request)

    @app.get(f"{base}/login")
    @app.post(f"{base}/login")
    @app.post(f"{base}/logout")
    async def legacy_protocol_login(request: Request) -> RedirectResponse:
        return _redirect_legacy_protocol_page(request)

    @app.get(f"{base}/settings")
    async def legacy_protocol_settings(request: Request) -> RedirectResponse:
        return RedirectResponse(url=f"{_pallas_console_http_base()}/preferences", status_code=307)

    @app.get(f"{base}/new")
    async def legacy_protocol_new_account(request: Request) -> RedirectResponse:
        return _redirect_legacy_protocol_page(request, target="/create")

    @app.get(f"{base}/import")
    async def legacy_protocol_import(request: Request) -> RedirectResponse:
        return _redirect_legacy_protocol_page(request, target="/import")

    @app.post(f"{base}/api/accounts/import")
    async def import_accounts(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        from ..importer import run_import

        source_dir = Path(str(payload.get("source_dir", "")).strip())
        if not await asyncio.to_thread(source_dir.is_dir):
            raise HTTPException(status_code=400, detail=f"目录不存在: {source_dir}")
        dry_run = bool(payload.get("dry_run", False))
        skip_existing = bool(payload.get("skip_existing", True))
        ws_url = str(payload.get("ws_url", "") or "").strip()
        ws_token = str(payload.get("ws_token", "") or "")
        ws_name = str(payload.get("ws_name", "") or "pallas").strip() or "pallas"

        existing = {acc["id"]: acc for acc in manager.list_accounts()}
        result, new_accounts = run_import(
            source_dir,
            existing,
            dry_run=dry_run,
            skip_existing=skip_existing,
            ws_url=ws_url,
            ws_name=ws_name,
            ws_token=ws_token,
            instances_root=manager._instances_root,
        )
        if not dry_run and result.imported:
            manager.bulk_register(new_accounts)
        return {
            "imported": result.imported,
            "skipped": result.skipped,
            "failed": result.failed,
        }

    @app.get(f"{base}/assets")
    @app.get(f"{base}/runtime")
    async def legacy_protocol_assets(request: Request) -> RedirectResponse:
        return _redirect_legacy_protocol_page(request, target="/assets")

    @app.get(f"{base}/account/{{account_id}}/edit")
    async def legacy_protocol_account_edit(account_id: str, request: Request) -> RedirectResponse:
        aid = quote(account_id, safe="")
        return _redirect_legacy_protocol_page(request, target=f"/account/{aid}", query="tab=settings")

    @app.get(f"{base}/account/{{account_id}}")
    async def legacy_protocol_account(account_id: str, request: Request) -> RedirectResponse:
        return _redirect_legacy_protocol_page(request, target=f"/account/{quote(account_id, safe='')}")

    @app.get(f"{base}/api/runtime")
    async def runtime_status(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.runtime_overview()

    @app.get(f"{base}/api/connection-hints")
    async def connection_hints(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.connection_hints()

    @app.get(f"{base}/api/nonebot-logs")
    async def nonebot_log_tail(
        request: Request,
        lines: int = Query(default=400, ge=1, le=2000),
        scope: str = Query(default="all"),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token, request=request)
        from pallas.console.web import install_nonebot_log_sink

        install_nonebot_log_sink()

        sc = scope if scope in ("all", "webui", "protocol") else "all"

        def _load() -> dict[str, object]:
            from pallas.api.platform import is_sharded_hub
            from pallas.console.web import (
                tail_nonebot_log_entries_scoped,
                tail_nonebot_log_lines_scoped,
            )

            cap = min(lines, 220) if is_sharded_hub() else lines
            return {
                "logs": tail_nonebot_log_lines_scoped(cap, sc),
                "entries": tail_nonebot_log_entries_scoped(cap, sc),
                "scope": sc,
            }

        return await asyncio.to_thread(_load)

    @app.get(f"{base}/api/nonebot-logs/stream")
    async def nonebot_logs_stream(
        request: Request,
        scope: str = Query(default="all"),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token, request=request)
        from pallas.console.web import install_nonebot_log_sink, iter_nonebot_log_sse

        install_nonebot_log_sink()
        sc = scope if scope in ("all", "webui", "protocol") else "all"
        return StreamingResponse(
            iter_nonebot_log_sse(sc),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(f"{base}/api/runtime/download")
    async def runtime_download(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
        tag: str | None = Query(default=None),
        target_platform: str | None = Query(default=None),
        runtime_mode: str | None = Query(default=None),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return manager.start_runtime_download(
                tag=tag or None,
                target_platform=target_platform or None,
                runtime_mode=runtime_mode or None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get(f"{base}/api/runtime/profile")
    async def runtime_profile(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return {"profile": manager.runtime_profile()}

    @app.put(f"{base}/api/runtime/profile")
    async def update_runtime_profile(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return {"profile": await manager.update_runtime_profile(payload)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post(f"{base}/api/runtime/docker/pull")
    async def runtime_docker_pull(
        payload: dict[str, Any] | None = None,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        image = ""
        protocol = ""
        if isinstance(payload, dict):
            image = str(payload.get("image", "") or "").strip()
            protocol = str(payload.get("protocol", "") or "").strip().lower()
        if protocol and protocol not in ("napcat", "snowluma"):
            raise HTTPException(status_code=400, detail="protocol 仅支持 napcat、snowluma 或省略")
        return await manager.start_docker_pull_job(image or None, protocol=protocol or None)

    @app.get(f"{base}/api/runtime/docker/pull/{{job_id}}")
    async def runtime_docker_pull_status(
        job_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        job = manager.docker_pull_coordinator().job_to_dict(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="拉取任务不存在")
        return {"job": job}

    @app.get(f"{base}/api/runtime/docker/pull/{{job_id}}/stream")
    async def runtime_docker_pull_stream(
        job_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        if manager.docker_pull_coordinator().get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="拉取任务不存在")

        async def _iter():
            async for chunk in manager.docker_pull_coordinator().subscribe_sse(job_id):
                yield chunk

        return StreamingResponse(
            _iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(f"{base}/api/runtime/docker/images")
    async def runtime_docker_images(
        protocol: str | None = Query(
            default=None,
            description="napcat / snowluma：仅列出当前全局配置对应仓库的本地镜像；不传则列出全部。",
        ),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        p = str(protocol or "").strip().lower()
        if p and p not in ("napcat", "snowluma"):
            raise HTTPException(status_code=400, detail="protocol 仅支持 napcat、snowluma 或省略")
        return await manager.list_local_docker_images(protocol=p or None)

    @app.post(f"{base}/api/runtime/docker/stop-all")
    async def runtime_docker_stop_all(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return await manager.stop_all_labeled_docker_containers()

    @app.post(f"{base}/api/runtime/docker/prune-stopped")
    async def runtime_docker_prune_stopped(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return await manager.prune_stopped_labeled_docker_containers()

    @app.get(f"{base}/api/runtime/releases")
    async def runtime_releases(
        limit: int = Query(default=10, ge=1, le=200),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        releases = await manager.fetch_runtime_releases(limit=limit)
        return {"releases": releases}

    @app.post(f"{base}/api/runtime/rescan")
    async def runtime_rescan(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.rescan_runtime_extract()

    @app.post(f"{base}/api/runtime/cleanup-dist")
    async def runtime_cleanup_dist(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.cleanup_runtime_dist_caches()

    @app.get(f"{base}/api/runtime/local-inventory")
    async def runtime_local_inventory(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.napcat_local_inventory()

    @app.post(f"{base}/api/runtime/activate-extract")
    async def runtime_activate_extract(
        payload: dict[str, Any] | None = None,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        folder = str(body.get("folder", "") or "").strip()
        if not folder:
            raise HTTPException(status_code=400, detail="缺少 folder")
        try:
            return manager.activate_napcat_extract(folder)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post(f"{base}/api/runtime/activate-tag")
    async def runtime_activate_tag(
        payload: dict[str, Any] | None = None,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        tag = str(body.get("tag", "") or "").strip()
        if not tag:
            raise HTTPException(status_code=400, detail="缺少 tag")
        try:
            return manager.activate_napcat_by_tag(tag)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get(f"{base}/api/snowluma/runtime/overview")
    async def snowluma_runtime_overview(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.snowluma_runtime_overview()

    @app.post(f"{base}/api/snowluma/runtime/download")
    async def snowluma_runtime_download(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
        tag: str | None = Query(default=None),
        target_platform: str | None = Query(default=None),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return manager.start_snowluma_runtime_download(tag=tag or None, target_platform=target_platform)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get(f"{base}/api/snowluma/runtime/releases")
    async def snowluma_runtime_releases(
        limit: int = Query(default=10, ge=1, le=200),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        releases = await manager.fetch_snowluma_runtime_releases(limit=limit)
        return {"releases": releases}

    @app.get(f"{base}/api/snowluma/runtime/local-inventory")
    async def snowluma_runtime_local_inventory(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        return manager.snowluma_local_inventory()

    @app.post(f"{base}/api/snowluma/runtime/activate-extract")
    async def snowluma_runtime_activate_extract(
        payload: dict[str, Any] | None = None,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        folder = str(body.get("folder", "") or "").strip()
        if not folder:
            raise HTTPException(status_code=400, detail="缺少 folder")
        try:
            return manager.activate_snowluma_extract(folder)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post(f"{base}/api/snowluma/runtime/activate-tag")
    async def snowluma_runtime_activate_tag(
        payload: dict[str, Any] | None = None,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        tag = str(body.get("tag", "") or "").strip()
        if not tag:
            raise HTTPException(status_code=400, detail="缺少 tag")
        try:
            return manager.activate_snowluma_by_tag(tag)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get(f"{base}/api/snowluma/runtimes")
    async def list_snowluma_runtimes(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
        lite: bool = Query(
            default=False,
            description="跳过进程/Docker 探测，仅返回注册表与成员（选 Runtime 用）",
        ),
    ):
        _auth(x_pallas_protocol_token, token)
        return {
            "runtimes": await asyncio.to_thread(
                manager.list_snowluma_runtimes,
                include_process=not lite,
            )
        }

    @app.post(f"{base}/api/snowluma/runtimes/image-switch")
    async def start_snowluma_runtime_image_switch(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        try:
            job_id = await manager.start_snowluma_runtime_image_switch(
                str(body.get("image") or ""), str(body.get("apply_mode") or "")
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        job = manager.snowluma_image_switch_coordinator().job_to_dict(job_id)
        return {"ok": True, "job_id": job_id, "job": job}

    @app.get(f"{base}/api/snowluma/runtimes/image-switch/{{job_id}}")
    async def snowluma_runtime_image_switch_status(
        job_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        job = manager.snowluma_image_switch_coordinator().job_to_dict(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="镜像切换任务不存在")
        return {"job": job}

    @app.get(f"{base}/api/snowluma/runtimes/image-switch/{{job_id}}/stream")
    async def snowluma_runtime_image_switch_stream(
        job_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        coordinator = manager.snowluma_image_switch_coordinator()
        if coordinator.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="镜像切换任务不存在")

        async def _iter():
            async for chunk in coordinator.subscribe_sse(job_id):
                yield chunk

        return StreamingResponse(
            _iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(f"{base}/api/snowluma/runtimes/{{runtime_id}}")
    async def get_snowluma_runtime(
        runtime_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        item = await asyncio.to_thread(manager.get_snowluma_runtime, runtime_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Runtime 不存在")
        return {"runtime": item}

    @app.post(f"{base}/api/snowluma/runtimes")
    async def create_snowluma_runtime(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            item = await asyncio.to_thread(manager.create_snowluma_runtime, payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"runtime": item}

    @app.put(f"{base}/api/snowluma/runtimes/{{runtime_id}}")
    async def update_snowluma_runtime(
        runtime_id: str,
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            item = await asyncio.to_thread(manager.update_snowluma_runtime, runtime_id, payload)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"runtime": item}

    @app.delete(f"{base}/api/snowluma/runtimes/{{runtime_id}}")
    async def delete_snowluma_runtime(
        runtime_id: str,
        force: bool = Query(default=False),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            await manager.delete_snowluma_runtime(runtime_id, force=force)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True}

    @app.post(f"{base}/api/snowluma/runtimes/{{runtime_id}}/start")
    async def start_snowluma_runtime(
        runtime_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            item = await manager.start_snowluma_runtime(runtime_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"runtime": item}

    @app.post(f"{base}/api/snowluma/runtimes/{{runtime_id}}/stop")
    async def stop_snowluma_runtime(
        runtime_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        item = await manager.stop_snowluma_runtime(runtime_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Runtime 不存在")
        return {"runtime": item}

    @app.get(f"{base}/api/accounts")
    async def list_accounts(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        accounts = await asyncio.to_thread(manager.list_accounts)
        return {"accounts": accounts}

    @app.get(f"{base}/api/accounts/{{account_id}}")
    async def get_one_account(
        account_id: str,
        brief: bool = Query(default=False, description="列表/轮询用：跳过 SnowLuma 日志口令解析等重操作"),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        acc = await asyncio.to_thread(manager.get_account, account_id, brief=brief)
        if acc is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"account": acc}

    @app.post(f"{base}/api/accounts")
    async def create_account(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            account = manager.create_account(payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"account": account}

    @app.put(f"{base}/api/accounts/{{account_id}}")
    async def update_account(
        account_id: str,
        payload: dict[str, Any],
        restart: bool = Query(default=True),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            result = await manager.update_account(account_id, payload, restart=restart)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (RuntimeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return result

    @app.delete(f"{base}/api/accounts/{{account_id}}")
    async def delete_account(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            await manager.delete_account(account_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"ok": True}

    @app.post(f"{base}/api/accounts/{{account_id}}/start")
    async def start_account(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            account = await manager.start_account(account_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"account": account}

    @app.post(f"{base}/api/accounts/{{account_id}}/stop")
    async def stop_account(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            account = await manager.stop_account(account_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"account": account}

    @app.post(f"{base}/api/accounts/{{account_id}}/restart")
    async def restart_account(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            account = await manager.restart_account(account_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"account": account}

    @app.post(f"{base}/api/accounts/batch")
    async def accounts_batch(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        action = str(body.get("action", "") or "").strip().lower()
        raw_ids = body.get("account_ids")
        ids: list[str] | None = None
        if isinstance(raw_ids, list):
            ids = [str(x) for x in raw_ids]
        mode = body.get("mode")
        max_concurrency = body.get("max_concurrency")
        stagger_ms = body.get("stagger_ms")
        try:
            job_id = await manager.start_account_batch(
                action,
                ids,
                mode=str(mode).strip().lower() if mode is not None else None,
                max_concurrency=int(max_concurrency) if max_concurrency is not None else None,
                stagger_ms=int(stagger_ms) if stagger_ms is not None else None,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        job = manager.batch_coordinator().job_to_dict(job_id)
        return {"ok": True, "job_id": job_id, "job": job}

    @app.post(f"{base}/api/accounts/migrate-to-snowluma")
    async def migrate_accounts_to_snowluma(
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        body = payload if isinstance(payload, dict) else {}
        raw_ids = body.get("account_ids")
        ids: list[str] | None = None
        if isinstance(raw_ids, list):
            ids = [str(x) for x in raw_ids]
        preserve = bool(body.get("preserve_napcat_data", False))
        stop_nc = bool(body.get("stop_napcat_containers", True))
        start_after = bool(body.get("start_after", False))
        stagger_ms = body.get("stagger_ms")
        max_concurrency = body.get("max_concurrency")
        try:
            result = await manager.migrate_accounts_to_snowluma(
                ids,
                preserve_napcat_data=preserve,
                stop_napcat_containers=stop_nc,
                start_after=start_after,
                stagger_ms=int(stagger_ms) if stagger_ms is not None else None,
                max_concurrency=int(max_concurrency) if max_concurrency is not None else None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, **result}

    @app.get(f"{base}/api/snowluma/host-deps")
    async def snowluma_host_deps(
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        from ..snowluma_host_deps import host_deps_report

        return host_deps_report()

    @app.get(f"{base}/api/accounts/batch/{{job_id}}")
    async def accounts_batch_status(
        job_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        job = manager.batch_coordinator().job_to_dict(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="批量任务不存在")
        return {"job": job}

    @app.get(f"{base}/api/accounts/batch/{{job_id}}/stream")
    async def accounts_batch_stream(
        job_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        if manager.batch_coordinator().get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="批量任务不存在")

        async def _iter():
            async for chunk in manager.batch_coordinator().subscribe_sse(job_id):
                yield chunk

        return StreamingResponse(
            _iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(f"{base}/api/accounts/{{account_id}}/snowluma/inject-hook")
    async def snowluma_inject_hook(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return await manager.snowluma_inject_hook_via_webui(account_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"连接 SnowLuma WebUI 失败: {e}") from e

    @app.get(f"{base}/api/accounts/{{account_id}}/logs")
    async def account_logs(
        account_id: str,
        lines: int = Query(default=200, ge=1, le=2000),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        if not manager.has_account(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        try:
            await asyncio.wait_for(manager.ensure_docker_logs_if_needed(account_id), timeout=2.5)
        except TimeoutError:
            pass
        logs = await asyncio.to_thread(manager.tail_logs, account_id, lines)
        return {"logs": logs}

    @app.get(f"{base}/api/accounts/{{account_id}}/qrcode/meta")
    async def account_qrcode_meta(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        if not manager.has_account(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        return await asyncio.to_thread(manager.account_qrcode_meta, account_id)

    @app.post(f"{base}/api/accounts/{{account_id}}/qrcode/refresh")
    async def refresh_account_qrcode(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        if not manager.has_account(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        try:
            return await manager.refresh_account_qrcode(account_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get(f"{base}/api/accounts/{{account_id}}/qrcode")
    async def account_qrcode_image(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        if not manager.has_account(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        path = await asyncio.to_thread(manager.account_qrcode_path, account_id)
        if path is None:
            raise HTTPException(status_code=404, detail="暂无二维码文件")
        return FileResponse(path, media_type="image/png", filename=f"{account_id}-qrcode.png")

    @app.get(f"{base}/api/accounts/{{account_id}}/configs")
    async def get_account_configs(
        account_id: str,
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return manager.get_account_configs(account_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.put(f"{base}/api/accounts/{{account_id}}/configs")
    async def update_account_configs(
        account_id: str,
        payload: dict[str, Any],
        restart: bool = Query(default=True),
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return await manager.update_account_configs(account_id, payload, restart=restart)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post(f"{base}/api/accounts/{{account_id}}/runtime-switch")
    async def switch_account_runtime(
        account_id: str,
        payload: dict[str, Any],
        token: str | None = Query(default=None),
        x_pallas_protocol_token: str | None = Header(default=None, alias="X-Pallas-Protocol-Token"),
    ):
        _auth(x_pallas_protocol_token, token)
        try:
            return await manager.switch_account_runtime(account_id, payload)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
