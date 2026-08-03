from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"
_PKG = "pallas_plugin_protocol_webui_client_test"


def load_module(qualified: str, filename: str):
    path = _ROOT / filename
    spec = importlib.util.spec_from_file_location(qualified, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod


if _PKG not in sys.modules:
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_ROOT)]
    sys.modules[_PKG] = pkg

# snowluma_webui_client imports snowluma_config at call-time for password candidates
for name, file in (
    ("docker_cli", "docker_cli.py"),
    ("docker_onebot_host", "docker_onebot_host.py"),
    ("linux_docker", "linux_docker.py"),
    ("snowluma_docker", "snowluma_docker.py"),
    ("snowluma_config", "snowluma_config.py"),
):
    load_module(f"{_PKG}.{name}", file)

webui_client = load_module(f"{_PKG}.snowluma_webui_client", "snowluma_webui_client.py")
generate_snowluma_managed_webui_password = webui_client.generate_snowluma_managed_webui_password
snowluma_ensure_webui_session = webui_client.snowluma_ensure_webui_session
snowluma_apply_onebot_config = webui_client.snowluma_apply_onebot_config
snowluma_fetch_onebot_config = webui_client.snowluma_fetch_onebot_config
snowluma_webui_login = webui_client.snowluma_webui_login
snowluma_webui_password_candidates = webui_client.snowluma_webui_password_candidates


def test_generate_snowluma_managed_webui_password_strength() -> None:
    pwd = generate_snowluma_managed_webui_password()
    assert len(pwd) >= 10
    assert any(c.isupper() for c in pwd)
    assert any(c.islower() for c in pwd)
    assert any(not c.isalnum() for c in pwd)
    assert " " not in pwd


@pytest.mark.asyncio
async def test_snowluma_webui_login_maps_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(
                429,
                json={"success": False, "message": "登录尝试过多，请 900 秒后重试"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://sl.test") as client:
        with pytest.raises(ValueError, match="429"):
            await snowluma_webui_login(client, "http://sl.test", "wrong")


@pytest.mark.asyncio
async def test_snowluma_apply_onebot_config_returns_hot_reload_state() -> None:
    expected = {
        "success": True,
        "saved": True,
        "applied": True,
        "online": True,
        "reloaded": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/config/12345"
        assert request.headers["Authorization"] == "Bearer tok"
        assert json.loads(request.content) == {"networks": {"wsClients": []}}
        return httpx.Response(200, json=expected)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://sl.test") as client:
        result = await snowluma_apply_onebot_config(
            client,
            "http://sl.test",
            {"Authorization": "Bearer tok"},
            "12345",
            {"networks": {"wsClients": []}},
        )

    assert result == expected


@pytest.mark.asyncio
async def test_snowluma_fetch_onebot_config_returns_full_webui_config() -> None:
    expected = {
        "account": {"uin": "12345"},
        "networks": {"wsClients": []},
        "message": {"reportSelfMessage": False},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/config/12345"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=expected)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://sl.test") as client:
        result = await snowluma_fetch_onebot_config(
            client,
            "http://sl.test",
            {"Authorization": "Bearer tok"},
            "12345",
        )

    assert result == expected


def test_password_candidates_prefer_managed_only() -> None:
    account = {"snowluma_managed_webui_password": "Pa!managedXy9"}
    assert snowluma_webui_password_candidates(
        account,
        ["initial credentials: user=admin password=af7c7aaa85693d30"],
    ) == ["Pa!managedXy9"]


@pytest.mark.asyncio
async def test_snowluma_ensure_webui_session_rotates_password() -> None:
    calls: list[tuple[str, str]] = []
    consented = {"ok": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/login":
            body = json.loads(request.content.decode())
            calls.append(("login", body.get("password", "")))
            pwd = str(body.get("password") or "")
            if pwd == "af7c7aaa85693d30":
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "token": "tok-bootstrap",
                        "mustChangePassword": True,
                    },
                )
            if pwd.startswith("Pa!"):
                return httpx.Response(
                    200,
                    json={"success": True, "token": "tok-managed"},
                )
            return httpx.Response(401, json={"success": False, "message": "bad"})
        if path == "/api/agreements":
            calls.append(("agreements", request.headers.get("Authorization", "")))
            if consented["ok"]:
                return httpx.Response(200, json={"consentRequired": False})
            return httpx.Response(
                200,
                json={"consentRequired": True, "version": "eula-test"},
            )
        if path == "/api/agreements/record-consent":
            calls.append(("consent", "ok"))
            consented["ok"] = True
            return httpx.Response(200, json={"success": True})
        if path == "/api/auth/change-password":
            calls.append(("change-password", "ok"))
            if not consented["ok"]:
                return httpx.Response(
                    403,
                    json={
                        "status": "failed",
                        "message": "请先阅读并同意用户协议与隐私政策",
                        "consentRequired": True,
                    },
                )
            return httpx.Response(200, json={"success": True, "requireRelogin": True})
        if path == "/api/processes":
            auth = request.headers.get("Authorization", "")
            if auth == "Bearer tok-managed":
                return httpx.Response(200, json={"list": [{"pid": 1, "uin": "12345"}]})
            return httpx.Response(
                403,
                json={
                    "status": "failed",
                    "message": "请先修改密码",
                    "mustChangePassword": True,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    account: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=transport, base_url="http://sl.test") as client:
        headers, dirty = await snowluma_ensure_webui_session(
            client,
            "http://sl.test",
            account,
            ["initial credentials: user=admin password=af7c7aaa85693d30"],
        )

    assert dirty is True
    assert headers["Authorization"] == "Bearer tok-managed"
    assert str(account.get("snowluma_managed_webui_password", "")).startswith("Pa!")
    assert ("login", "af7c7aaa85693d30") in calls
    assert ("consent", "ok") in calls
    assert ("change-password", "ok") in calls
    # 必须先同意协议再改密
    assert calls.index(("consent", "ok")) < calls.index(("change-password", "ok"))
