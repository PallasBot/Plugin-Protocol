"""SnowLuma Docker 内存限额与资源参数。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"
_PKG = "pallas_plugin_protocol_docker_prod_test"


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

for name, file in (
    ("docker_cli", "docker_cli.py"),
    ("docker_onebot_host", "docker_onebot_host.py"),
    ("linux_docker", "linux_docker.py"),
    ("snowluma_multi_qq", "snowluma_multi_qq.py"),
):
    load_module(f"{_PKG}.{name}", file)

snowluma_docker = load_module(f"{_PKG}.snowluma_docker", "snowluma_docker.py")
append_snowluma_docker_resource_limits = snowluma_docker.append_snowluma_docker_resource_limits
build_snowluma_docker_run_argv = snowluma_docker.build_snowluma_docker_run_argv
snowluma_docker_program_dir_marker = snowluma_docker.snowluma_docker_program_dir_marker
snowluma_dockerfile = snowluma_docker.snowluma_dockerfile
clear_snowluma_login_state = snowluma_docker.clear_snowluma_login_state


def test_append_snowluma_docker_resource_limits() -> None:
    cfg = SimpleNamespace(
        pallas_protocol_snowluma_docker_memory_limit="1g",
        pallas_protocol_snowluma_docker_memory_swap="1536m",
    )
    argv: list[str] = ["run", "-d"]
    append_snowluma_docker_resource_limits(argv, cfg)
    assert "--memory" in argv
    assert "1g" in argv
    assert "--memory-swap" in argv
    assert "1536m" in argv


def test_build_snowluma_docker_run_argv_includes_memory_limits() -> None:
    cfg = SimpleNamespace(
        pallas_protocol_snowluma_docker_image="motricseven7/snowluma:latest",
        pallas_protocol_snowluma_docker_internal_webui_port=5099,
        pallas_protocol_snowluma_docker_internal_onebot_http_port=3000,
        pallas_protocol_snowluma_docker_internal_onebot_ws_port=3001,
        pallas_protocol_snowluma_docker_shm_size="1g",
        pallas_protocol_snowluma_docker_memory_limit="1g",
        pallas_protocol_snowluma_docker_memory_swap="1536m",
        pallas_protocol_snowluma_docker_vnc_passwd="",
        pallas_protocol_snowluma_docker_host_novnc_port=0,
        pallas_protocol_snowluma_docker_host_vnc_port=0,
        pallas_protocol_snowluma_docker_internal_novnc_port=6081,
        pallas_protocol_snowluma_docker_internal_vnc_port=5900,
    )
    account = {
        "id": "123",
        "webui_port": 6100,
        "snowluma_docker_host_onebot_http": 17100,
        "snowluma_docker_host_onebot_ws": 17101,
        "account_data_dir": "/tmp/sl-test",
    }

    def resolve_qq(acc: dict) -> str:
        return str(acc.get("id", ""))

    argv = build_snowluma_docker_run_argv(account, cfg, resolve_qq)
    assert "--memory" in argv
    assert "--cap-add" in argv
    assert "SYS_PTRACE" in argv
    assert "SNOWLUMA_ACCEPT_EULA=1" in argv
    assert "SNOWLUMA_ACCEPT_PRIVACY=1" in argv
    assert any("/docker/snowluma/qq-homes:/app/qq-homes" in str(item) for item in argv)
    assert not any(str(item).startswith("SNOWLUMA_EXTRA_QQ_HOMES=") for item in argv)


def test_build_snowluma_docker_run_argv_multi_qq_sets_extra_homes(tmp_path: Path) -> None:
    cfg = SimpleNamespace(
        pallas_protocol_snowluma_docker_image="motricseven7/snowluma:latest",
        pallas_protocol_snowluma_docker_internal_webui_port=5099,
        pallas_protocol_snowluma_docker_internal_onebot_http_port=3000,
        pallas_protocol_snowluma_docker_internal_onebot_ws_port=3001,
        pallas_protocol_snowluma_docker_shm_size="1g",
        pallas_protocol_snowluma_docker_memory_limit="",
        pallas_protocol_snowluma_docker_memory_swap="",
        pallas_protocol_snowluma_docker_vnc_passwd="",
        pallas_protocol_snowluma_docker_host_novnc_port=0,
        pallas_protocol_snowluma_docker_host_vnc_port=0,
        pallas_protocol_snowluma_docker_internal_novnc_port=6081,
        pallas_protocol_snowluma_docker_internal_vnc_port=5900,
    )
    account = {
        "id": "111",
        "webui_port": 6100,
        "snowluma_docker_host_onebot_http": 17100,
        "snowluma_docker_host_onebot_ws": 17101,
        "account_data_dir": str(tmp_path),
        "snowluma_member_uins": ["111", "222"],
        "snowluma_primary_uin": "111",
    }
    argv = build_snowluma_docker_run_argv(account, cfg, lambda _: "111")
    assert "SNOWLUMA_EXTRA_QQ_HOMES=/app/qq-homes/222" in argv
    assert (tmp_path / "docker" / "snowluma" / "qq-homes" / "222").is_dir()


def test_snowluma_maps_base_tag_onto_derived_repo() -> None:
    cfg = SimpleNamespace(
        pallas_protocol_snowluma_docker_image="registry.invalid/ignored:v1.12.8",
        pallas_protocol_snowluma_docker_internal_webui_port=5099,
        pallas_protocol_snowluma_docker_internal_onebot_http_port=3000,
        pallas_protocol_snowluma_docker_internal_onebot_ws_port=3001,
        pallas_protocol_snowluma_docker_shm_size="1g",
        pallas_protocol_snowluma_docker_memory_limit="",
        pallas_protocol_snowluma_docker_memory_swap="",
        pallas_protocol_snowluma_docker_vnc_passwd="",
        pallas_protocol_snowluma_docker_host_novnc_port=0,
        pallas_protocol_snowluma_docker_host_vnc_port=0,
        pallas_protocol_snowluma_docker_internal_novnc_port=6081,
        pallas_protocol_snowluma_docker_internal_vnc_port=5900,
    )
    account = {
        "id": "123",
        "webui_port": 6100,
        "snowluma_docker_host_onebot_http": 17100,
        "snowluma_docker_host_onebot_ws": 17101,
        "account_data_dir": "/tmp/sl-test",
    }
    argv = build_snowluma_docker_run_argv(account, cfg, lambda _: "123")
    assert argv[-1] == "pallas/snowluma-auto-login:v1.12.8"
    assert snowluma_docker_program_dir_marker(cfg) == "docker:snowluma:pallas/snowluma-auto-login:v1.12.8"


def test_account_snowluma_docker_image_overrides_global() -> None:
    cfg = SimpleNamespace(
        pallas_protocol_snowluma_docker_image="motricseven7/snowluma:latest",
        pallas_protocol_snowluma_docker_internal_webui_port=5099,
        pallas_protocol_snowluma_docker_internal_onebot_http_port=3000,
        pallas_protocol_snowluma_docker_internal_onebot_ws_port=3001,
        pallas_protocol_snowluma_docker_shm_size="1g",
        pallas_protocol_snowluma_docker_memory_limit="",
        pallas_protocol_snowluma_docker_memory_swap="",
        pallas_protocol_snowluma_docker_vnc_passwd="",
        pallas_protocol_snowluma_docker_host_novnc_port=0,
        pallas_protocol_snowluma_docker_host_vnc_port=0,
        pallas_protocol_snowluma_docker_internal_novnc_port=6081,
        pallas_protocol_snowluma_docker_internal_vnc_port=5900,
    )
    account = {
        "id": "123",
        "webui_port": 6100,
        "snowluma_docker_host_onebot_http": 17100,
        "snowluma_docker_host_onebot_ws": 17101,
        "account_data_dir": "/tmp/sl-test",
        "snowluma_docker_image": "pallas/snowluma-auto-login:v1.12.9",
    }
    argv = build_snowluma_docker_run_argv(account, cfg, lambda _: "123")
    assert argv[-1] == "pallas/snowluma-auto-login:v1.12.9"
    assert (
        snowluma_docker_program_dir_marker(cfg, account=account) == "docker:snowluma:pallas/snowluma-auto-login:v1.12.9"
    )


def test_snowluma_dockerfile_pins_base_image_and_installs_xdotool() -> None:
    dockerfile = snowluma_dockerfile()
    assert "FROM motricseven7/snowluma:latest" in dockerfile
    assert "USER root" in dockerfile
    assert "USER snowluma" not in dockerfile
    assert "xdotool" in dockerfile
    assert "imagemagick" in dockerfile
    assert "tesseract-ocr" in dockerfile
    assert "tesseract-ocr-chi-sim" in dockerfile
    assert "apt-get update" in dockerfile


def test_ensure_snowluma_docker_image_builds_local_tag(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        if "inspect" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        if argv[:3] == ["docker", "run", "--rm"]:
            return subprocess.CompletedProcess(argv, 0, '{"version":"1.12.9"}\n', "")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(snowluma_docker.subprocess, "run", fake_run)
    ok, msg = snowluma_docker.ensure_snowluma_docker_image()
    assert ok is True
    assert "pallas/snowluma-auto-login:latest" in msg or msg == ""
    assert calls[0][0] == [
        "docker",
        "image",
        "inspect",
        "pallas/snowluma-auto-login:latest",
    ]
    build_calls = [c for c in calls if c[0][:2] == ["docker", "build"]]
    assert len(build_calls) == 1
    assert "pallas/snowluma-auto-login:latest" in build_calls[0][0]
    assert "pallas/snowluma-auto-login:v1.12.9" in build_calls[0][0]
    assert "FROM motricseven7/snowluma:latest" in str(build_calls[0][1])
    assert "xdotool" in str(build_calls[0][1])


def test_rebuild_snowluma_docker_image_dual_tags_latest_and_version(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        if argv[:3] == ["docker", "run", "--rm"]:
            return subprocess.CompletedProcess(argv, 0, '{"version":"1.12.9"}\n', "")
        return subprocess.CompletedProcess(argv, 0, "built\n", "")

    monkeypatch.setattr(snowluma_docker.subprocess, "run", fake_run)
    ok, out = snowluma_docker.rebuild_snowluma_docker_image("motricseven7/snowluma:latest")
    assert ok is True
    assert len(calls) == 2
    build_argv = calls[1][0]
    assert build_argv[0:3] == ["docker", "build", "--tag"]
    assert "pallas/snowluma-auto-login:latest" in build_argv
    assert "pallas/snowluma-auto-login:v1.12.9" in build_argv
    assert "FROM motricseven7/snowluma:latest" in str(calls[1][1])
    assert "built" in out


def test_rebuild_snowluma_docker_image_forces_build_with_pulled_base(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        return subprocess.CompletedProcess(argv, 0, "built\n", "")

    monkeypatch.setattr(snowluma_docker.subprocess, "run", fake_run)
    ok, out = snowluma_docker.rebuild_snowluma_docker_image("motricseven7/snowluma:nightly")
    assert ok is True
    assert len(calls) == 1
    assert "inspect" not in calls[0][0]
    assert calls[0][0] == [
        "docker",
        "build",
        "--tag",
        "pallas/snowluma-auto-login:nightly",
        "-",
    ]
    assert "FROM motricseven7/snowluma:nightly" in str(calls[0][1])
    assert "built" in out


def test_ensure_snowluma_docker_image_force_skips_inspect(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "run", "--rm"]:
            return subprocess.CompletedProcess(argv, 0, '{"version":"1.0.0"}\n', "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(snowluma_docker.subprocess, "run", fake_run)
    assert snowluma_docker.ensure_snowluma_docker_image(force=True)[0] is True
    assert all("inspect" not in argv for argv in calls)
    assert any(argv[:2] == ["docker", "build"] for argv in calls)


def test_derived_snowluma_image_ref_keeps_tag() -> None:
    assert snowluma_docker.derived_snowluma_image_ref("motricseven7/snowluma:v1.12.9") == (
        "pallas/snowluma-auto-login:v1.12.9"
    )
    assert snowluma_docker.normalize_snowluma_version_tag("1.12.9") == "v1.12.9"
    assert snowluma_docker.normalize_snowluma_version_tag("v1.12.9") == "v1.12.9"


def test_snowluma_dockerfile_accepts_custom_base() -> None:
    dockerfile = snowluma_dockerfile("registry.example/snowluma:v2")
    assert "FROM registry.example/snowluma:v2" in dockerfile
    assert "FROM motricseven7/snowluma:latest" not in dockerfile


def test_clear_snowluma_login_state_preserves_snowluma_config(tmp_path: Path) -> None:
    account_data_dir = tmp_path / "instance" / "snowluma"
    base = account_data_dir / "docker" / "snowluma"
    config = base / "snowluma-data"
    dot_config = base / "dot-config"
    dot_local_share = base / "dot-local-share"
    (config / "config").mkdir(parents=True)
    dot_config.mkdir(parents=True)
    dot_local_share.mkdir(parents=True)
    (config / "config" / "onebot.json").write_text("{}")
    (dot_config / "qq-session").write_text("expired")
    (dot_local_share / "qq-cache").write_text("expired")
    qr_cache = account_data_dir / "cache" / "qrcode.png"
    qr_cache.parent.mkdir(parents=True)
    qr_cache.write_bytes(b"expired")

    cleared = clear_snowluma_login_state({"account_data_dir": str(account_data_dir)})

    assert cleared == 3
    assert (config / "config" / "onebot.json").is_file()
    assert not dot_config.exists()
    assert not dot_local_share.exists()
    assert not qr_cache.exists()
