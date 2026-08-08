"""Linux：SnowLuma Docker 镜像。"""

from __future__ import annotations

import json
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
SNOWLUMA_DOCKER_BASE_REPO = "motricseven7/snowluma"
SNOWLUMA_DOCKER_IMAGE_REPO = "pallas/snowluma-auto-login"
SNOWLUMA_DOCKER_IMAGE = f"{SNOWLUMA_DOCKER_IMAGE_REPO}:latest"


def snowluma_dockerfile(base_image: str | None = None) -> str:
    packages = "xdotool imagemagick tesseract-ocr tesseract-ocr-chi-sim"
    base = str(base_image or "").strip() or SNOWLUMA_DOCKER_BASE_IMAGE
    return f"""FROM {base}
USER root
RUN apt-get update \\
 && apt-get install -y --no-install-recommends {packages} \\
 && rm -rf /var/lib/apt/lists/*
"""


def normalize_snowluma_version_tag(version: str) -> str:
    """把 ``1.12.9`` / ``v1.12.9`` 规范为 ``v1.12.9``；其它原样返回。"""
    v = str(version or "").strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] in "vV" and v[1].isdigit():
        return f"v{v[1:]}"
    if v[0].isdigit():
        return f"v{v}"
    return v


def is_derived_snowluma_image(ref: str) -> bool:
    repo = docker_cli.docker_repository_from_ref(ref).strip().lower()
    if not repo:
        return False
    return repo == SNOWLUMA_DOCKER_IMAGE_REPO.lower() or repo.endswith("/snowluma-auto-login")


def derived_snowluma_image_ref(base_image: str | None = None) -> str:
    """上游 ``repo:tag`` → 派生 ``pallas/snowluma-auto-login:<同 tag>``。"""
    base = str(base_image or "").strip() or SNOWLUMA_DOCKER_BASE_IMAGE
    if is_derived_snowluma_image(base):
        tag = docker_cli.docker_tag_from_ref(base) or "latest"
        return f"{SNOWLUMA_DOCKER_IMAGE_REPO}:{tag}"
    tag = docker_cli.docker_tag_from_ref(base) or "latest"
    return f"{SNOWLUMA_DOCKER_IMAGE_REPO}:{tag}"


def coerce_snowluma_run_image(ref: str | None = None) -> str:
    """上游或派生引用 → 实际 ``docker run`` 用的派生镜像。"""
    raw = str(ref or "").strip()
    if not raw:
        return SNOWLUMA_DOCKER_IMAGE
    if is_derived_snowluma_image(raw):
        return raw
    return derived_snowluma_image_ref(raw)


def resolve_snowluma_upstream_base(ref: str | None = None) -> str:
    """派生或上游引用 → 重建用的上游基础镜像。"""
    raw = str(ref or "").strip() or SNOWLUMA_DOCKER_BASE_IMAGE
    if is_derived_snowluma_image(raw):
        tag = docker_cli.docker_tag_from_ref(raw) or "latest"
        return f"{SNOWLUMA_DOCKER_BASE_REPO}:{tag}"
    return raw


def resolve_snowluma_base_image(config: Any | None = None) -> str:
    raw = ""
    if config is not None:
        raw = str(getattr(config, "pallas_protocol_snowluma_docker_image", "") or "").strip()
    return raw or SNOWLUMA_DOCKER_BASE_IMAGE


def resolve_snowluma_run_image(
    config: Any | None = None,
    *,
    account: dict | None = None,
    runtime: dict | None = None,
    override: str | None = None,
) -> str:
    """账号 / Runtime / 全局配置 → 实际运行的派生镜像。"""
    for raw in (
        str(override or "").strip(),
        str((runtime or {}).get("snowluma_docker_image", "") or "").strip(),
        str((account or {}).get("snowluma_docker_image", "") or "").strip(),
        resolve_snowluma_base_image(config),
    ):
        if raw:
            return coerce_snowluma_run_image(raw)
    return SNOWLUMA_DOCKER_IMAGE


def resolve_snowluma_version_tag_from_image(image: str) -> str:
    """从镜像内 ``/app/snowluma/package.json`` 读取版本，返回 ``vX.Y.Z``；失败为空串。"""
    ref = str(image or "").strip()
    if not ref:
        return ""
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "cat", ref, "/app/snowluma/package.json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    text = (proc.stdout or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return normalize_snowluma_version_tag(str(data.get("version", "") or ""))


def snowluma_rebuild_tags(base_image: str | None = None) -> list[str]:
    """重建派生镜像时要打的 tag 列表（同上游 tag；``:latest`` 额外打解析出的版本 tag）。"""
    upstream = resolve_snowluma_upstream_base(base_image)
    primary = derived_snowluma_image_ref(upstream)
    tags = [primary]
    tag = docker_cli.docker_tag_from_ref(upstream) or "latest"
    if tag == "latest":
        ver = resolve_snowluma_version_tag_from_image(upstream)
        if ver:
            ver_ref = f"{SNOWLUMA_DOCKER_IMAGE_REPO}:{ver}"
            if ver_ref not in tags:
                tags.append(ver_ref)
    return tags


def snowluma_docker_build_argv(tags: list[str]) -> list[str]:
    argv = ["docker", "build", "--pull"]
    for tag in tags:
        t = str(tag or "").strip()
        if t:
            argv.extend(["--tag", t])
    argv.append("-")
    return argv


__all__ = [
    "SNOWLUMA_DOCKER_BASE_IMAGE",
    "SNOWLUMA_DOCKER_BASE_REPO",
    "SNOWLUMA_DOCKER_IMAGE",
    "SNOWLUMA_DOCKER_IMAGE_REPO",
    "append_snowluma_docker_resource_limits",
    "build_snowluma_docker_run_argv",
    "build_snowluma_docker_run_argv_for_runtime",
    "clear_snowluma_login_state",
    "clear_snowluma_login_state_for_uin",
    "coerce_snowluma_run_image",
    "derived_snowluma_image_ref",
    "ensure_snowluma_docker_image",
    "is_derived_snowluma_image",
    "normalize_snowluma_version_tag",
    "prepare_snowluma_webui_bootstrap",
    "resolve_snowluma_docker_bootstrap_password",
    "rebuild_snowluma_docker_image",
    "snowluma_webui_credentials_settled",
    "snowluma_webui_json_path",
    "resolve_snowluma_base_image",
    "resolve_snowluma_run_image",
    "resolve_snowluma_upstream_base",
    "resolve_snowluma_version_tag_from_image",
    "snowluma_docker_build_argv",
    "snowluma_docker_container_name",
    "snowluma_docker_container_name_for_runtime",
    "snowluma_docker_container_running",
    "snowluma_docker_container_running_sync",
    "snowluma_docker_effective_host_novnc_port",
    "snowluma_docker_effective_host_vnc_port",
    "snowluma_docker_image_exists",
    "snowluma_docker_program_dir_marker",
    "snowluma_docker_remove_force",
    "snowluma_docker_remove_force_sync",
    "snowluma_docker_stop",
    "snowluma_docker_stop_sync",
    "snowluma_docker_volume_paths",
    "snowluma_docker_volume_paths_from_data_dir",
    "snowluma_dockerfile",
    "snowluma_rebuild_tags",
]


def rebuild_snowluma_docker_image(base_image: str | None = None) -> tuple[bool, str]:
    """强制重建含 xdotool 等依赖的本地派生镜像（FROM 上游 SnowLuma；同打版本 tag）。"""
    upstream = resolve_snowluma_upstream_base(base_image)
    tags = snowluma_rebuild_tags(upstream)
    if not tags:
        tags = [SNOWLUMA_DOCKER_IMAGE]
    try:
        build = subprocess.run(
            snowluma_docker_build_argv(tags),
            input=snowluma_dockerfile(upstream),
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, f"构建 SnowLuma Docker 镜像失败：{err}"
    output = (build.stdout or build.stderr or "").strip()
    tagged = ", ".join(tags)
    if build.returncode == 0:
        return True, output[-2000:] if output else f"已重建 {tagged}（FROM {upstream}）"
    return False, f"构建 SnowLuma Docker 镜像失败：{output[-1200:]}"


def snowluma_docker_image_exists(image: str) -> tuple[bool, str]:
    """检查实际 docker run 镜像是否已在本地，绝不拉取或构建。"""
    ref = str(image or "").strip()
    if not ref:
        return False, "镜像引用为空"
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", ref],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, f"检查 SnowLuma Docker 镜像失败：{err}"
    if inspect.returncode == 0:
        return True, ""
    return False, f"本地不存在 SnowLuma Docker 镜像 {ref}，请先拉取或构建镜像"


def ensure_snowluma_docker_image(
    *,
    base_image: str | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """在首次使用前构建含 xdotool 的本地 SnowLuma 镜像；force=True 时强制重建。"""
    raw = str(base_image or "").strip() or SNOWLUMA_DOCKER_BASE_IMAGE
    derived = coerce_snowluma_run_image(raw)
    if not force:
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", derived],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            return False, f"检查 SnowLuma Docker 镜像失败：{err}"
        if inspect.returncode == 0:
            return True, ""
    return rebuild_snowluma_docker_image(raw)


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


def prepare_snowluma_webui_bootstrap(data_root: Path) -> bool:
    """删除未完成改密的 ``webui.json``，让 ``SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD`` 能生效。

    已完成改密（``mustChangePassword=false``）的凭据文件会保留。
    返回是否删除了文件。
    """
    path = snowluma_webui_json_path(data_root)
    if path is None or not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        try:
            path.unlink()
        except OSError:
            return False
        return True
    must_change = isinstance(raw, dict) and raw.get("mustChangePassword") is True
    if not isinstance(raw, dict) or must_change:
        try:
            path.unlink()
        except OSError:
            return False
        return True
    return False


def snowluma_webui_json_path(data_root: Path) -> Path | None:
    raw = str(data_root or "").strip()
    if not raw:
        return None
    data_dir, _, _ = snowluma_docker_volume_paths_from_data_dir(Path(raw))
    return data_dir / "config" / "webui.json"


def snowluma_webui_credentials_settled(data_root: Path) -> bool:
    """``webui.json`` 已存在且不要求强制改密。"""
    path = snowluma_webui_json_path(data_root)
    if path is None or not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return isinstance(raw, dict) and raw.get("mustChangePassword") is not True


def resolve_snowluma_docker_bootstrap_password(
    runtime: dict,
    account: dict | None = None,
) -> str:
    """决定写入 ``SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD`` 的口令。

    - 已有托管口令：直接用（并同步到 runtime/account）
    - 尚无托管口令且 WebUI 凭据未落盘：生成新托管口令
    - 凭据已落盘但无托管口令：不注入 bootstrap（避免用错密盖住现网）
    """
    from .snowluma_webui_client import ensure_snowluma_managed_webui_password

    data_root = Path(str(runtime.get("data_dir", "") or "").strip())
    existing = ""
    for item in (runtime, account):
        if not isinstance(item, dict):
            continue
        pwd = str(item.get("snowluma_managed_webui_password") or "").strip()
        if pwd:
            existing = pwd
            break
    if existing:
        ensure_snowluma_managed_webui_password(runtime, account)
        return existing
    if snowluma_webui_credentials_settled(data_root):
        return ""
    pwd, _ = ensure_snowluma_managed_webui_password(runtime, account)
    return pwd


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


def build_snowluma_docker_run_argv(
    account: dict,
    config: Any,
    resolve_qq,
    *,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
) -> list[str]:
    qq = str(resolve_qq(account) or "").strip()
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
    uins = member_uins
    if uins is None:
        raw = account.get("snowluma_member_uins")
        if isinstance(raw, (list, tuple)):
            uins = [str(x) for x in raw]
        elif qq.isdigit():
            uins = [qq]
    primary = primary_uin
    if primary is None:
        primary = str(account.get("snowluma_primary_uin") or "").strip() or (qq if qq.isdigit() else None)
    return build_snowluma_docker_run_argv_for_runtime(
        runtime_stub,
        config,
        account_id_label=str(account.get("id", "x")),
        account=account,
        member_uins=uins,
        primary_uin=primary,
    )


def build_snowluma_docker_run_argv_for_runtime(
    runtime: dict,
    config: Any,
    *,
    account_id_label: str = "",
    account: dict | None = None,
    image_override: str | None = None,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
) -> list[str]:
    img = resolve_snowluma_run_image(
        config,
        account=account,
        runtime=runtime,
        override=image_override,
    )
    in_webui = _internal_webui_port(config)
    in_http = _internal_onebot_http_port(config)
    in_ws = _internal_onebot_ws_port(config)

    def _host_port(*sources: Any) -> int:
        for raw in sources:
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 65535:
                return value
        return 0

    host_webui = _host_port(runtime.get("webui_port"), (account or {}).get("webui_port"))
    if not host_webui:
        host_webui = in_webui
    # Runtime 注册表可能尚未 sync；账号上 apply_defaults 已分配的端口作为回退。
    host_http = _host_port(
        runtime.get("snowluma_docker_host_onebot_http"),
        (account or {}).get("snowluma_docker_host_onebot_http"),
    )
    host_ws = _host_port(
        runtime.get("snowluma_docker_host_onebot_ws"),
        (account or {}).get("snowluma_docker_host_onebot_ws"),
    )
    if not host_http or not host_ws:
        msg = "SnowLuma Docker 需要有效的 snowluma_docker_host_onebot_http / snowluma_docker_host_onebot_ws"
        raise ValueError(msg)

    name = snowluma_docker_container_name_for_runtime(runtime)
    data_root_raw = str(runtime.get("data_dir", "") or "").strip()
    if not data_root_raw:
        raise ValueError("Runtime data_dir 缺失")
    data_root = Path(data_root_raw)
    data_dir, cfg_dir, local_share = snowluma_docker_volume_paths_from_data_dir(data_root)
    shm = str(getattr(config, "pallas_protocol_snowluma_docker_shm_size", "") or "").strip() or "1g"
    vnc_pw = str(getattr(config, "pallas_protocol_snowluma_docker_vnc_passwd", "") or "").strip()
    rid = sanitize_docker_name_suffix(str(runtime.get("id", "x")))
    label_account = sanitize_docker_name_suffix(
        str(account_id_label or runtime.get("legacy_container_account_id") or rid)
    )

    bootstrap_pwd = resolve_snowluma_docker_bootstrap_password(runtime, account)
    prepare_snowluma_webui_bootstrap(data_root)

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
        "-e",
        "SNOWLUMA_HOOK_AUTOLOAD=1",
        "-v",
        f"{data_dir}:/app/data",
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
    if bootstrap_pwd:
        argv.extend(["-e", f"SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD={bootstrap_pwd}"])
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

    from .snowluma_multi_qq import append_snowluma_multi_qq_docker_args

    uins = member_uins
    primary = primary_uin
    if uins is None and account is not None:
        raw = account.get("snowluma_member_uins")
        if isinstance(raw, (list, tuple)):
            uins = [str(x) for x in raw]
    if primary is None and account is not None:
        primary = str(account.get("snowluma_primary_uin") or "").strip() or None
    append_snowluma_multi_qq_docker_args(
        argv,
        data_dir=data_root,
        member_uins=uins,
        primary_uin=primary,
    )

    argv.append(img)
    return argv


def snowluma_docker_program_dir_marker(
    config: Any,
    *,
    account: dict | None = None,
    runtime: dict | None = None,
) -> str:
    return f"docker:snowluma:{resolve_snowluma_run_image(config, account=account, runtime=runtime)}"


async def snowluma_docker_stop(name: str) -> None:
    await docker_cli.docker_stop_async(name, wait_timeout=120)


def snowluma_docker_stop_sync(name: str) -> None:
    docker_cli.docker_stop_sync(name, subprocess_timeout=120)
