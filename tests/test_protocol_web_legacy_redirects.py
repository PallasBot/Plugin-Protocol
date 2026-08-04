from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pallas.console.webui import console_login
from starlette.routing import WebSocketRoute

nonebot.init()

from pallas_plugin_protocol.web.routes import register_pallas_protocol_routes  # noqa: E402


class DummyManager:
    pass


class _UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = f"upstream:{self.path}".encode()
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args) -> None:
        _ = args


class ProxyManager:
    def __init__(self, port: int) -> None:
        self.port = port

    def get_account(self, account_id: str, *, brief: bool = False) -> dict | None:
        _ = brief
        if account_id != "snowluma-1":
            return None
        return {
            "protocol_backend": "snowluma",
            "snowluma_linux_docker": True,
            "snowluma_docker_host_novnc_port": self.port,
            "running": True,
        }


def make_client() -> TestClient:
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=DummyManager(),
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )
    return TestClient(app, follow_redirects=False)


def test_legacy_protocol_console_root_redirects_without_old_login() -> None:
    response = make_client().get("/protocol/console?source=bookmark")

    assert response.status_code == 307
    assert response.headers["location"] == "/pallas/protocol?source=bookmark"


def test_legacy_protocol_console_subpages_redirect_to_console_routes() -> None:
    client = make_client()

    assert (
        client.get("/protocol/console/new").headers["location"]
        == "/pallas/protocol/create"
    )
    assert (
        client.get("/protocol/console/import").headers["location"]
        == "/pallas/protocol/import"
    )
    assert (
        client.get("/protocol/console/assets").headers["location"]
        == "/pallas/protocol/assets"
    )
    assert (
        client.get("/protocol/console/settings").headers["location"]
        == "/pallas/preferences"
    )


def test_legacy_protocol_login_is_not_an_independent_html_page() -> None:
    response = make_client().get("/protocol/console/login")

    assert response.status_code == 307
    assert response.headers["location"] == "/pallas/protocol"


def test_console_instance_novnc_proxies_the_registered_subpath(monkeypatch) -> None:
    monkeypatch.setattr(
        console_login,
        "extract_session_from_request",
        lambda **_kwargs: "session",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=ProxyManager(server.server_port),
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )
    try:
        response = TestClient(app).get(
            "/pallas/protocol/instances/snowluma-1/novnc/assets/app.js"
        )
    finally:
        server.shutdown()
        thread.join()

    assert response.status_code == 200
    assert response.text == "upstream:/assets/app.js"


def test_console_registers_an_instance_websocket_proxy_route() -> None:
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=DummyManager(),
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )

    assert any(
        isinstance(route, WebSocketRoute)
        and route.path
        == "/pallas/protocol/instances/{account_id}/{surface}/{subpath:path}"
        for route in app.routes
    )


def test_runtime_switch_route_authenticates_and_calls_service(monkeypatch) -> None:
    monkeypatch.setattr(
        console_login,
        "extract_session_from_request",
        lambda **_kwargs: "session",
    )
    manager = SimpleNamespace(
        switch_account_runtime=AsyncMock(return_value={"account": {"id": "10001"}})
    )
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=manager,
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )

    response = TestClient(app).post(
        "/protocol/console/api/accounts/10001/runtime-switch",
        json={"protocol_backend": "snowluma", "runtime_mode": "new"},
    )

    assert response.status_code == 200
    assert response.json() == {"account": {"id": "10001"}}
    manager.switch_account_runtime.assert_awaited_once_with(
        "10001", {"protocol_backend": "snowluma", "runtime_mode": "new"}
    )


def test_snowluma_image_switch_route_calls_service(monkeypatch) -> None:
    monkeypatch.setattr(
        console_login,
        "extract_session_from_request",
        lambda **_kwargs: "session",
    )
    coordinator = SimpleNamespace(job_to_dict=lambda job_id: {"job_id": job_id})
    manager = SimpleNamespace(
        start_snowluma_runtime_image_switch=AsyncMock(return_value="switch-1"),
        snowluma_image_switch_coordinator=lambda: coordinator,
    )
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=manager,
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )

    response = TestClient(app).post(
        "/protocol/console/api/snowluma/runtimes/image-switch",
        json={"image": "snowluma:test", "apply_mode": "next_start"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == "switch-1"
    manager.start_snowluma_runtime_image_switch.assert_awaited_once_with(
        "snowluma:test", "next_start"
    )


def test_runtime_switch_route_rejects_unauthenticated_requests(monkeypatch) -> None:
    monkeypatch.setattr(
        console_login, "extract_session_from_request", lambda **_kwargs: None
    )
    monkeypatch.setattr(console_login, "is_console_auth_configured", lambda: True)
    manager = SimpleNamespace(switch_account_runtime=AsyncMock())
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=manager,
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )

    response = TestClient(app).post(
        "/protocol/console/api/accounts/10001/runtime-switch",
        json={"protocol_backend": "snowluma"},
    )

    assert response.status_code == 401
    manager.switch_account_runtime.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(KeyError("账号不存在"), 404), (ValueError("参数无效"), 400)],
)
def test_runtime_switch_route_maps_service_errors(
    monkeypatch, error: Exception, status_code: int
) -> None:
    monkeypatch.setattr(
        console_login,
        "extract_session_from_request",
        lambda **_kwargs: "session",
    )
    manager = SimpleNamespace(switch_account_runtime=AsyncMock(side_effect=error))
    app = FastAPI()
    register_pallas_protocol_routes(
        app,
        manager=manager,
        plugin_config=SimpleNamespace(
            pallas_protocol_webui_path="",
            pallas_protocol_web_implementation="",
            pallas_protocol_webui_enabled=True,
        ),
    )
    payload = {"protocol_backend": "snowluma"}

    response = TestClient(app).post(
        "/protocol/console/api/accounts/10001/runtime-switch",
        json=payload,
    )

    assert response.status_code == status_code
    manager.switch_account_runtime.assert_awaited_once_with("10001", payload)
