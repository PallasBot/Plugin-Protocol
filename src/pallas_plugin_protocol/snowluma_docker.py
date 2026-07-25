"""Linux：SnowLuma Docker 镜像。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import docker_cli
from .docker_onebot_host import docker_host_gateway_extra_args
from .linux_docker import sanitize_docker_name_suffix

snowluma_docker_container_running = docker_cli.docker_inspect_running_async
snowluma_docker_container_running_sync = docker_cli.docker_inspect_running_sync
snowluma_docker_remove_force = docker_cli.docker_rm_force_async
snowluma_docker_remove_force_sync = docker_cli.docker_rm_force_sync

SNOWLUMA_DOCKER_BASE_IMAGE = "motricseven7/snowluma:latest"
SNOWLUMA_DOCKER_IMAGE = "pallas/snowluma-auto-login:latest"


def snowluma_dockerfile(base_image: str | None = None) -> str:
    packages = "xdotool imagemagick tesseract-ocr tesseract-ocr-chi-sim"
    base = str(base_image or "").strip() or SNOWLUMA_DOCKER_BASE_IMAGE
    return f"""FROM {base}
USER root
RUN apt-get update \\
 && apt-get install -y --no-install-recommends {packages} \\
 && rm -rf /var/lib/apt/lists/*
"""


__all__ = [
    "SNOWLUMA_DOCKER_BASE_IMAGE",
    "SNOWLUMA_DOCKER_IMAGE",
    "append_snowluma_docker_resource_limits",
    "build_snowluma_docker_run_argv",
    "build_snowluma_docker_run_argv_for_runtime",
    "clear_snowluma_login_state",
    "clear_snowluma_login_state_for_uin",
    "ensure_snowluma_docker_image",
    "rebuild_snowluma_docker_image",
    "snowluma_docker_container_name",
    "snowluma_docker_container_name_for_runtime",
    "snowluma_docker_container_running",
    "snowluma_docker_container_running_sync",
    "snowluma_docker_effective_host_novnc_port",
    "snowluma_docker_effective_host_vnc_port",
    "snowluma_docker_program_dir_marker",
    "snowluma_docker_remove_force",
    "snowluma_docker_remove_force_sync",
    "snowluma_docker_stop",
    "snowluma_docker_stop_sync",
    "snowluma_docker_volume_paths",
    "snowluma_docker_volume_paths_from_data_dir",
    "snowluma_dockerfile",
]


def rebuild_snowluma_docker_image(base_image: str | None = None) -> tuple[bool, str]:
    """强制重建含 xdotool 等依赖的本地派生镜像（FROM 上游 SnowLuma）。"""
    base = str(base_image or "").strip() or SNOWLUMA_DOCKER_BASE_IMAGE
    try:
        build = subprocess.run(
            ["docker", "build", "--tag", SNOWLUMA_DOCKER_IMAGE, "-"],
            input=snowluma_dockerfile(base),
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, f"构建 SnowLuma Docker 镜像失败：{err}"
    output = (build.stdout or build.stderr or "").strip()
    if build.returncode == 0:
        return True, output[-2000:] if output else f"已重建 {SNOWLUMA_DOCKER_IMAGE}（FROM {base}）"
    return False, f"构建 SnowLuma Docker 镜像失败：{output[-1200:]}"


def ensure_snowluma_docker_image(
    *,
    base_image: str | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """在首次使用前构建含 xdotool 的本地 SnowLuma 镜像；force=True 时强制重建。"""
    if not force:
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", SNOWLUMA_DOCKER_IMAGE],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            return False, f"检查 SnowLuma Docker 镜像失败：{err}"
        if inspect.returncode == 0:
            return True, ""
    return rebuild_snowluma_docker_image(base_image)


def snowluma_docker_container_name_for_runtime(runtime: dict) -> str:
    legacy = str(runtime.get("legacy_container_account_id", "") or "").strip()
    if legacy:
        return f"pallas-proto-sl-{sanitize_docker_name_suffix(legacy)}"
    rid = str(runtime.get("id", "x") or "x").strip() or "x"
    return f"pallas-proto-sl-rt-{sanitize_docker_name_suffix(rid)}"


def snowluma_docker_container_name(account: dict) -> str:
    """兼容旧调用：优先用账号 id（迁移前容器名）。新路径请用 runtime 版。"""
    return f"pallas-proto-sl-{sanitize_docker_name_suffix(str(account.get('id', 'x')))}"


def snowluma_docker_volume_paths_from_data_dir(
    data_dir: Path,
) -> tuple[Path, Path, Path]:
    base = Path(data_dir).resolve() / "docker" / "snowluma"
    return base / "snowluma-data", base / "dot-config", base / "dot-local-share"


def snowluma_docker_volume_paths(account: dict) -> tuple[Path, Path, Path]:
    ad = Path(str(account.get("account_data_dir", "")).strip()).resolve()
    return snowluma_docker_volume_paths_from_data_dir(ad)


def clear_snowluma_login_state_for_uin(data_dir: Path, qq: str) -> int:
    """按 UIN 清理登录缓存；不整卷删除 .config / .local/share（多 QQ 不安全）。"""
    root = Path(data_dir).resolve()
    if not str(data_dir).strip():
        raise ValueError("数据目录缺失")
    uin = str(qq or "").strip()
    if not uin.isdigit():
        raise ValueError("QQ 无效")
    cleared = 0
    cache = root / "cache"
    for name in (f"qrcode_{uin}.png", "qrcode.png"):
        path = cache / name
        try:
            if path.is_file():
                path.unlink()
                cleared += 1
        except OSError as err:
            raise ValueError(f"清理二维码失败：{err}") from err
    _, dot_config, dot_local_share = snowluma_docker_volume_paths_from_data_dir(root)
    for base in (dot_config, dot_local_share):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.name == uin or child.name.endswith(uin):
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                        cleared += 1
                    elif child.is_file():
                        child.unlink()
                        cleared += 1
                except OSError as err:
                    raise ValueError(f"清理登录态失败：{err}") from err
    return cleared


def clear_snowluma_login_state(account: dict) -> int:
    """整卷清理登录态（仅单 QQ Runtime / 兼容旧行为）。多 QQ 请用 for_uin。"""
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip()).resolve()
    if not str(account.get("account_data_dir", "")).strip():
        raise ValueError("账号目录缺失")
    _, dot_config, dot_local_share = snowluma_docker_volume_paths(account)
    targets = [dot_config, dot_local_share, account_data_dir / "cache" / "qrcode.png"]
    cleared = 0
    for target in targets:
        resolved = target.resolve()
        if resolved != account_data_dir and account_data_dir not in resolved.parents:
            raise ValueError("登录态路径不在账号目录内")
        try:
            if target.is_dir():
                shutil.rmtree(target)
                cleared += 1
            elif target.is_file():
                target.unlink()
                cleared += 1
        except OSError as err:
            raise ValueError(f"清理登录态失败：{err}") from err
    return cleared


def _internal_webui_port(config: Any) -> int:
    return int(getattr(config, "pallas_protocol_snowluma_docker_internal_webui_port", 5099) or 5099)


def _internal_onebot_http_port(config: Any) -> int:
    return int(getattr(config, "pallas_protocol_snowluma_docker_internal_onebot_http_port", 3000) or 3000)


def _internal_onebot_ws_port(config: Any) -> int:
    return int(getattr(config, "pallas_protocol_snowluma_docker_internal_onebot_ws_port", 3001) or 3001)


def snowluma_docker_effective_host_novnc_port(account: dict, config: Any) -> int:
    if "snowluma_docker_host_novnc_port" in account:
        try:
            return int(str(account["snowluma_docker_host_novnc_port"]).strip())
        except (TypeError, ValueError):
            return 0
    return int(getattr(config, "pallas_protocol_snowluma_docker_host_novnc_port", 0) or 0)


def snowluma_docker_effective_host_vnc_port(account: dict, config: Any) -> int:
    if "snowluma_docker_host_vnc_port" in account:
        try:
            return int(str(account["snowluma_docker_host_vnc_port"]).strip())
        except (TypeError, ValueError):
            return 0
    return int(getattr(config, "pallas_protocol_snowluma_docker_host_vnc_port", 0) or 0)


def append_snowluma_docker_resource_limits(argv: list[str], config: Any) -> None:
    mem = str(getattr(config, "pallas_protocol_snowluma_docker_memory_limit", "") or "").strip()
    if mem:
        argv.extend(["--memory", mem])
    swap = str(getattr(config, "pallas_protocol_snowluma_docker_memory_swap", "") or "").strip()
    if swap:
        argv.extend(["--memory-swap", swap])


def build_snowluma_docker_run_argv(account: dict, config: Any, resolve_qq) -> list[str]:
    _ = str(resolve_qq(account) or "").strip()
    rid = str(account.get("snowluma_runtime_id") or "").strip()
    legacy = str(account.get("snowluma_runtime_legacy_container_account_id") or "").strip()
    runtime_stub: dict[str, Any] = {
        "id": rid or str(account.get("id", "x")),
        "data_dir": str(account.get("account_data_dir", "")).strip(),
        "webui_port": account.get("webui_port"),
        "snowluma_docker_host_onebot_http": account.get("snowluma_docker_host_onebot_http"),
        "snowluma_docker_host_onebot_ws": account.get("snowluma_docker_host_onebot_ws"),
        "snowluma_docker_host_novnc_port": account.get("snowluma_docker_host_novnc_port"),
        "snowluma_docker_host_vnc_port": account.get("snowluma_docker_host_vnc_port"),
    }
    if legacy:
        runtime_stub["legacy_container_account_id"] = legacy
    elif not rid:
        # 无 Runtime 注册表时的旧 1:1 容器名
        runtime_stub["legacy_container_account_id"] = str(account.get("id", "x"))
    return build_snowluma_docker_run_argv_for_runtime(
        runtime_stub,
        config,
        account_id_label=str(account.get("id", "x")),
    )


def build_snowluma_docker_run_argv_for_runtime(
    runtime: dict,
    config: Any,
    *,
    account_id_label: str = "",
) -> list[str]:
    img = SNOWLUMA_DOCKER_IMAGE
    in_webui = _internal_webui_port(config)
    in_http = _internal_onebot_http_port(config)
    in_ws = _internal_onebot_ws_port(config)
    try:
        host_webui = int(str(runtime.get("webui_port", "")).strip())
    except (TypeError, ValueError):
        host_webui = 0
    if not (1 <= host_webui <= 65535):
        host_webui = in_webui
    try:
        host_http = int(str(runtime.get("snowluma_docker_host_onebot_http", "")).strip())
    except (TypeError, ValueError):
        host_http = 0
    try:
        host_ws = int(str(runtime.get("snowluma_docker_host_onebot_ws", "")).strip())
    except (TypeError, ValueError):
        host_ws = 0
    if not (1 <= host_http <= 65535) or not (1 <= host_ws <= 65535):
        msg = "SnowLuma Docker 需要有效的 snowluma_docker_host_onebot_http / snowluma_docker_host_onebot_ws"
        raise ValueError(msg)

    name = snowluma_docker_container_name_for_runtime(runtime)
    data_root = Path(str(runtime.get("data_dir", "") or "").strip()).resolve()
    if not str(runtime.get("data_dir", "") or "").strip():
        raise ValueError("Runtime data_dir 缺失")
    data_dir, cfg_dir, local_share = snowluma_docker_volume_paths_from_data_dir(data_root)
    shm = str(getattr(config, "pallas_protocol_snowluma_docker_shm_size", "") or "").strip() or "1g"
    vnc_pw = str(getattr(config, "pallas_protocol_snowluma_docker_vnc_passwd", "") or "").strip()
    rid = sanitize_docker_name_suffix(str(runtime.get("id", "x")))
    label_account = sanitize_docker_name_suffix(
        str(account_id_label or runtime.get("legacy_container_account_id") or rid)
    )

    argv: list[str] = [
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "pallas.protocol=snowluma",
        "--label",
        f"pallas.runtime_id={rid}",
        "--label",
        f"pallas.account_id={label_account}",
        "--restart",
        "unless-stopped",
        *docker_host_gateway_extra_args(),
        "--shm-size",
        shm,
        "--cap-add",
        "SYS_PTRACE",
        "--security-opt",
        "seccomp=unconfined",
        "-e",
        f"SNOWLUMA_WEBUI_PORT={in_webui}",
        "-e",
        "SNOWLUMA_ACCEPT_EULA=1",
        "-e",
        "SNOWLUMA_ACCEPT_PRIVACY=1",
        "-v",
        f"{data_dir}:/app/snowluma-data",
        "-v",
        f"{cfg_dir}:/app/.config",
        "-v",
        f"{local_share}:/app/.local/share",
        "-p",
        f"{host_webui}:{in_webui}",
        "-p",
        f"{host_http}:{in_http}",
        "-p",
        f"{host_ws}:{in_ws}",
    ]
    if vnc_pw:
        argv.extend(["-e", f"VNC_PASSWD={vnc_pw}"])

    novnc = snowluma_docker_effective_host_novnc_port(runtime, config)
    vnc = snowluma_docker_effective_host_vnc_port(runtime, config)
    in_novnc = int(getattr(config, "pallas_protocol_snowluma_docker_internal_novnc_port", 6081) or 6081)
    in_vnc = int(getattr(config, "pallas_protocol_snowluma_docker_internal_vnc_port", 5900) or 5900)
    if 1 <= novnc <= 65535:
        argv.extend(["-p", f"{novnc}:{in_novnc}"])
    if 1 <= vnc <= 65535:
        argv.extend(["-p", f"{vnc}:{in_vnc}"])

    append_snowluma_docker_resource_limits(argv, config)
    argv.append(img)
    return argv


def snowluma_docker_program_dir_marker(config: Any) -> str:
    return f"docker:snowluma:{SNOWLUMA_DOCKER_IMAGE}"


async def snowluma_docker_stop(name: str) -> None:
    await docker_cli.docker_stop_async(name, wait_timeout=120)


def snowluma_docker_stop_sync(name: str) -> None:
    docker_cli.docker_stop_sync(name, subprocess_timeout=120)
