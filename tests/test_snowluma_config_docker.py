"""SnowLuma Docker snapshot wsClients 同步单元测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"


def load_module(qualified: str, filename: str):
    path = _ROOT / filename
    spec = importlib.util.spec_from_file_location(qualified, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod


if "pallas_plugin_protocol" not in sys.modules:
    pkg = types.ModuleType("pallas_plugin_protocol")
    pkg.__path__ = [str(_ROOT)]
    sys.modules["pallas_plugin_protocol"] = pkg

load_module("pallas_plugin_protocol.docker_onebot_host", "docker_onebot_host.py")
load_module("pallas_plugin_protocol.linux_docker", "linux_docker.py")
load_module("pallas_plugin_protocol.snowluma_docker", "snowluma_docker.py")
snowluma_config = load_module(
    "pallas_plugin_protocol.snowluma_config", "snowluma_config.py"
)


class FakeCfg:
    def safe_read_json(self, path: Path) -> dict:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}


def test_merge_snowluma_docker_snapshot_ws_clients() -> None:
    current = {
        "mode": "snapshot",
        "networks": {
            "wsClients": [{"name": "other", "url": "ws://example/ws"}],
        },
    }
    entry = snowluma_config.build_snowluma_ws_client_entry(
        {"ws_name": "pallas", "ws_token": ""},
        "ws://172.17.0.1:7973/onebot/v11/ws",
    )
    merged = snowluma_config.merge_snowluma_docker_snapshot_ws_clients(current, entry)
    clients = merged["networks"]["wsClients"]
    assert clients[0]["url"] == "ws://172.17.0.1:7973/onebot/v11/ws"
    assert any(c.get("name") == "other" for c in clients)
    assert merged["statusCommand"] == {"enabled": False}


def test_sync_snowluma_onebot_disables_status_command_by_default(
    tmp_path: Path,
) -> None:
    account = {
        "qq": "3879348674",
        "account_data_dir": str(tmp_path),
        "ws_url": "ws://127.0.0.1:7973/onebot/v11/ws",
    }

    snowluma_config.sync_snowluma_onebot(FakeCfg(), account, lambda acc: str(acc["qq"]))

    data = json.loads(
        (tmp_path / "config" / "onebot_3879348674.json").read_text(encoding="utf-8")
    )
    assert data["statusCommand"] == {"enabled": False}


def test_sync_snowluma_onebot_preserves_existing_status_command(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "onebot_3879348674.json"
    path.write_text(
        json.dumps({"statusCommand": {"enabled": True, "trigger": "/status"}}),
        encoding="utf-8",
    )
    account = {"qq": "3879348674", "account_data_dir": str(tmp_path)}

    snowluma_config.sync_snowluma_onebot(FakeCfg(), account, lambda acc: str(acc["qq"]))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["statusCommand"] == {"enabled": True, "trigger": "/status"}


def test_sync_snowluma_onebot_docker_snapshot_writes_file(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "3879348674",
        "qq": "3879348674",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
        "ws_url": "ws://172.17.0.1:7973/onebot/v11/ws",
        "ws_name": "pallas",
        "ws_token": "",
    }
    cfg = FakeCfg()
    path = snowluma_config.sync_snowluma_onebot_docker_snapshot(
        cfg,
        account,
        lambda acc: str(acc["qq"]),
        plugin_config=SimpleNamespace(pallas_protocol_docker_onebot_host="172.17.0.1"),
    )
    assert path is not None and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mode"] == "snapshot"
    assert data["networks"]["wsClients"][0]["url"].endswith("/onebot/v11/ws")


def test_sync_snowluma_onebot_docker_snapshot_makes_existing_file_readable(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    config_dir = ad / "docker" / "snowluma" / "snowluma-data" / "config"
    config_dir.mkdir(parents=True)
    path = config_dir / "onebot_3879348674.json"
    path.write_text(json.dumps({"mode": "snapshot"}), encoding="utf-8")
    path.chmod(0o600)
    account = {
        "id": "3879348674",
        "qq": "3879348674",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }

    result = snowluma_config.sync_snowluma_onebot_docker_snapshot(
        FakeCfg(),
        account,
        lambda acc: str(acc["qq"]),
    )

    assert result == path
    assert result.stat().st_mode & 0o777 == 0o644
