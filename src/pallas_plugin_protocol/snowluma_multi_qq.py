"""SnowLuma 单容器多 QQ：独立 HOME、进程定位与按需拉起。"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .linux_docker import sanitize_docker_name_suffix

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY = ":1"
PRIMARY_QQ_HOME = "/app"
QQ_HOMES_CONTAINER_ROOT = "/app/qq-homes"
QQ_MAIN_CMD_RE = re.compile(r"(?:^|[/\s])qq(?:\s|$)", re.IGNORECASE)


def snowluma_qq_homes_host_root(data_dir: Path) -> Path:
    return Path(data_dir).resolve() / "docker" / "snowluma" / "qq-homes"


def snowluma_qq_home_container_path(uin: str) -> str:
    safe = sanitize_docker_name_suffix(str(uin or "").strip() or "x")
    return f"{QQ_HOMES_CONTAINER_ROOT}/{safe}"


def snowluma_qq_home_host_path(data_dir: Path, uin: str) -> Path:
    safe = sanitize_docker_name_suffix(str(uin or "").strip() or "x")
    return snowluma_qq_homes_host_root(data_dir) / safe


def normalize_member_uins(raw: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or ():
        uin = str(item or "").strip()
        if not uin.isdigit() or uin in seen:
            continue
        seen.add(uin)
        out.append(uin)
    return out


def resolve_primary_uin(
    member_uins: list[str] | None,
    *,
    primary_uin: str | None = None,
) -> str:
    explicit = str(primary_uin or "").strip()
    if explicit.isdigit():
        return explicit
    members = normalize_member_uins(member_uins)
    return members[0] if members else ""


def resolve_account_qq_home(
    uin: str,
    *,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
) -> str:
    target = str(uin or "").strip()
    primary = resolve_primary_uin(member_uins, primary_uin=primary_uin)
    if not target:
        return PRIMARY_QQ_HOME
    if not primary or target == primary:
        return PRIMARY_QQ_HOME
    return snowluma_qq_home_container_path(target)


def append_snowluma_multi_qq_docker_args(
    argv: list[str],
    *,
    data_dir: Path,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
) -> list[str]:
    """挂载 qq-homes 父目录，并为非主号写入 SNOWLUMA_EXTRA_QQ_HOMES。"""
    members = normalize_member_uins(member_uins)
    primary = resolve_primary_uin(members, primary_uin=primary_uin)
    host_root = snowluma_qq_homes_host_root(data_dir)
    host_root.mkdir(parents=True, exist_ok=True)
    argv.extend(["-v", f"{host_root}:{QQ_HOMES_CONTAINER_ROOT}"])
    extras: list[str] = []
    for uin in members:
        if uin == primary:
            continue
        host = snowluma_qq_home_host_path(data_dir, uin)
        host.mkdir(parents=True, exist_ok=True)
        extras.append(snowluma_qq_home_container_path(uin))
    if extras:
        argv.extend(["-e", f"SNOWLUMA_EXTRA_QQ_HOMES={','.join(extras)}"])
    return extras


def _docker_exec_text(
    container_name: str,
    argv: list[str],
    *,
    display: str = DEFAULT_DISPLAY,
    timeout: float = 20.0,
) -> str:
    cmd = ["docker", "exec", "-e", f"DISPLAY={display}", container_name, *argv]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def list_qq_main_pids_by_home(
    container_name: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> dict[str, int]:
    """容器内主 QQ 进程：``HOME -> pid``（仅无 ``--type=`` 的主进程）。"""
    text_runner = run_exec_text or _docker_exec_text
    script = (
        "for f in /proc/[0-9]*/cmdline; do "
        "pid=${f%/cmdline}; pid=${pid#/proc/}; "
        "cmd=$(tr '\\0' ' ' < \"$f\" 2>/dev/null); "
        "case \"$cmd\" in *'--type='*) continue ;; esac; "
        'case "$cmd" in *qq*) ;; *) continue ;; esac; '
        "home=$(tr '\\0' '\\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^HOME=//p' | head -n1); "
        "home=${home:-/app}; "
        'printf \'%s %s\\n\' "$pid" "$home"; '
        "done"
    )
    raw = text_runner(container_name, ["sh", "-c", script], display=display)
    out: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        home = parts[1].strip() or PRIMARY_QQ_HOME
        if home not in out or pid < out[home]:
            out[home] = pid
    return out


def resolve_qq_main_pid_for_uin(
    container_name: str,
    uin: str,
    *,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> int | None:
    home = resolve_account_qq_home(
        uin,
        member_uins=member_uins,
        primary_uin=primary_uin,
    )
    mapping = list_qq_main_pids_by_home(
        container_name,
        display=display,
        run_exec_text=run_exec_text,
    )
    pid = mapping.get(home)
    if pid:
        return pid
    # 兼容 HOME=/home/snowluma 等主号变体
    if home == PRIMARY_QQ_HOME:
        for key, value in mapping.items():
            if key in {"/app", "/home/snowluma", ""}:
                return value
    return None


def ensure_snowluma_qq_process_for_uin(
    container_name: str,
    uin: str,
    *,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> tuple[bool, int | None, str]:
    """确保指定 UIN 的 QQ 主进程在容器内；缺失时按独立 HOME 拉起。"""
    text_runner = run_exec_text or _docker_exec_text
    target = str(uin or "").strip()
    if not target.isdigit():
        return False, None, "QQ 无效"
    existing = resolve_qq_main_pid_for_uin(
        container_name,
        target,
        member_uins=member_uins,
        primary_uin=primary_uin,
        display=display,
        run_exec_text=text_runner,
    )
    if existing:
        return True, existing, ""
    home = resolve_account_qq_home(
        target,
        member_uins=member_uins,
        primary_uin=primary_uin,
    )
    if home == PRIMARY_QQ_HOME:
        return False, None, "主号 QQ 进程尚未就绪"

    mkdir_script = f"mkdir -p '{home}' && (chown -R snowluma:snowluma '{home}' 2>/dev/null || true)"
    text_runner(container_name, ["sh", "-c", mkdir_script], display=display)

    # 与镜像 start.sh 一致：独立 HOME + 同组 QQ flags
    launch_cmd = [
        "docker",
        "exec",
        "-d",
        "-u",
        "snowluma",
        "-e",
        f"DISPLAY={display}",
        "-e",
        f"HOME={home}",
        container_name,
        "sh",
        "-lc",
        "exec qq --no-sandbox ${SNOWLUMA_QQ_FLAGS}",
    ]
    try:
        proc = subprocess.run(
            launch_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, None, f"拉起 QQ 失败：{err}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, None, detail[-300:] or f"docker exec 失败 (exit {proc.returncode})"

    # 给进程一点启动时间
    time.sleep(1.5)
    pid = resolve_qq_main_pid_for_uin(
        container_name,
        target,
        member_uins=member_uins,
        primary_uin=primary_uin,
        display=display,
        run_exec_text=text_runner,
    )
    if pid:
        logger.info(
            "SnowLuma 容器 {} 已为 UIN {} 拉起 QQ 进程 pid={} HOME={}",
            container_name,
            target,
            pid,
            home,
        )
        return True, pid, ""
    return False, None, f"未能在 HOME={home} 拉起 QQ 进程"


def window_net_wm_pid(
    container_name: str,
    window_id: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> int | None:
    text_runner = run_exec_text or _docker_exec_text
    raw = text_runner(
        container_name,
        ["xprop", "-id", window_id, "_NET_WM_PID"],
        display=display,
    )
    match = re.search(r"=\s*(\d+)", raw)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


__all__ = [
    "DEFAULT_DISPLAY",
    "PRIMARY_QQ_HOME",
    "QQ_HOMES_CONTAINER_ROOT",
    "append_snowluma_multi_qq_docker_args",
    "ensure_snowluma_qq_process_for_uin",
    "list_qq_main_pids_by_home",
    "normalize_member_uins",
    "resolve_account_qq_home",
    "resolve_primary_uin",
    "resolve_qq_main_pid_for_uin",
    "snowluma_qq_home_container_path",
    "snowluma_qq_home_host_path",
    "snowluma_qq_homes_host_root",
    "window_net_wm_pid",
]
