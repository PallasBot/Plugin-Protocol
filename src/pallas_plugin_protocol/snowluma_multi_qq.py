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


def supervisorctl_qq(
    container_name: str,
    action: str,
    *,
    program: str = "qq",
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """对镜像内 supervisord 的 QQ 程序执行 start/stop（``qq`` / ``qq-extra-N``）。"""
    act = str(action or "").strip().lower()
    prog = str(program or "").strip() or "qq"
    if act not in {"start", "stop"}:
        return False, f"不支持的 supervisorctl 动作：{action}"
    if not re.fullmatch(r"qq(?:-extra-\d+)?", prog):
        return False, f"不支持的 supervisor 程序名：{prog}"
    cmd = [
        "docker",
        "exec",
        container_name,
        "supervisorctl",
        "-c",
        "/etc/supervisord.conf",
        act,
        prog,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, f"supervisorctl {act} {prog} 失败：{err}"
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
    if proc.returncode != 0:
        return False, detail[-300:] or f"supervisorctl {act} {prog} 失败 (exit {proc.returncode})"
    return True, detail


def resolve_qq_supervisor_program(
    container_name: str,
    home: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> str | None:
    """按 HOME 解析 supervisord 程序名：主号 ``qq``，副号 ``qq-extra-N``。"""
    target_home = str(home or "").strip() or PRIMARY_QQ_HOME
    if target_home in {PRIMARY_QQ_HOME, "/home/snowluma", ""}:
        return "qq"
    text_runner = run_exec_text or _docker_exec_text
    conf = text_runner(
        container_name,
        ["sh", "-c", "cat /etc/supervisor/conf.d/extra-qq.conf 2>/dev/null || true"],
        display=display,
    )
    current: str | None = None
    home_pat = re.compile(r'HOME=(?:"([^"]+)"|\'([^\']+)\'|([^,\s]+))')
    for line in conf.splitlines():
        m_prog = re.match(r"\[program:(qq-extra-\d+)\]\s*$", line.strip())
        if m_prog:
            current = m_prog.group(1)
            continue
        if not current:
            continue
        m_home = home_pat.search(line)
        if not m_home:
            continue
        conf_home = (m_home.group(1) or m_home.group(2) or m_home.group(3) or "").strip()
        if conf_home == target_home:
            return current
    # 回退：按 SNOWLUMA_EXTRA_QQ_HOMES 顺序（与 start.sh 生成 conf 一致）
    extras_raw = text_runner(
        container_name,
        ["sh", "-c", 'printf %s "${SNOWLUMA_EXTRA_QQ_HOMES-}"'],
        display=display,
    )
    extras = [h.strip() for h in extras_raw.split(",") if h.strip()]
    try:
        idx = extras.index(target_home)
    except ValueError:
        return None
    return f"qq-extra-{idx + 1}"


def list_qq_main_pid_candidates_by_home(
    container_name: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> dict[str, list[int]]:
    """容器内主 QQ 进程：``HOME -> [pid]``（排除渲染进程与 crashpad）。"""
    text_runner = run_exec_text or _docker_exec_text
    # 勿用笼统 *qq*：会命中 /opt/QQ/chrome_crashpad_handler、qq-homes 路径
    script = (
        "for f in /proc/[0-9]*/cmdline; do "
        "pid=${f%/cmdline}; pid=${pid#/proc/}; "
        "cmd=$(tr '\\0' ' ' < \"$f\" 2>/dev/null); "
        "case \"$cmd\" in *'--type='*) continue ;; esac; "
        'case "$cmd" in '
        "qq\\ *|*/qq\\ *|qq) ;; "
        "*) continue ;; "
        "esac; "
        "home=$(tr '\\0' '\\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^HOME=//p' | head -n1); "
        "home=${home:-/app}; "
        'printf \'%s %s\\n\' "$pid" "$home"; '
        "done"
    )
    raw = text_runner(container_name, ["sh", "-c", script], display=display)
    out: dict[str, list[int]] = {}
    for line in raw.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        home = parts[1].strip() or PRIMARY_QQ_HOME
        out.setdefault(home, []).append(pid)
    for pids in out.values():
        pids.sort()
    return out


def list_qq_main_pids_by_home(
    container_name: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> dict[str, int]:
    """每个 HOME 取最新 QQ 主进程 PID。"""
    candidates = list_qq_main_pid_candidates_by_home(
        container_name,
        display=display,
        run_exec_text=run_exec_text,
    )
    return {home: pids[-1] for home, pids in candidates.items() if pids}


def _pids_for_qq_home(pids_by_home: dict[str, list[int]], home: str) -> list[int]:
    pids = pids_by_home.get(home)
    if pids:
        return pids
    if home == PRIMARY_QQ_HOME:
        for key in ("/app", "/home/snowluma", ""):
            pids = pids_by_home.get(key)
            if pids:
                return pids
    return []


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
    candidates = list_qq_main_pid_candidates_by_home(
        container_name,
        display=display,
        run_exec_text=run_exec_text,
    )
    pids = _pids_for_qq_home(candidates, home)
    return pids[-1] if pids else None


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
    home = resolve_account_qq_home(
        target,
        member_uins=member_uins,
        primary_uin=primary_uin,
    )
    candidates = list_qq_main_pid_candidates_by_home(
        container_name,
        display=display,
        run_exec_text=text_runner,
    )
    existing_pids = _pids_for_qq_home(candidates, home)
    if existing_pids:
        active_pid = existing_pids[-1]
        for stale_pid in existing_pids[:-1]:
            logger.warning(
                "SnowLuma 容器 %s 发现 UIN %s 重复 QQ 进程，清理孤儿 pid=%s，保留 pid=%s",
                container_name,
                target,
                stale_pid,
                active_pid,
            )
            _kill_qq_main_pid(
                container_name,
                stale_pid,
                display=display,
                run_exec_text=text_runner,
            )
        return True, active_pid, ""
    program = resolve_qq_supervisor_program(
        container_name,
        home,
        display=display,
        run_exec_text=text_runner,
    )
    if program:
        # 主号 qq / 副号 qq-extra-N 均由 supervisord 托管（autorestart）
        ok, detail = supervisorctl_qq(container_name, "start", program=program)
        if not ok:
            return False, None, detail or f"未能启动 QQ（supervisorctl {program}）"
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
                "SnowLuma 容器 {} 已经 supervisorctl 启动 UIN {} QQ pid={} program={}",
                container_name,
                target,
                pid,
                program,
            )
            return True, pid, ""
        return False, None, f"supervisorctl start {program} 后仍未见 QQ 进程"

    mkdir_script = f"mkdir -p '{home}' && (chown -R snowluma:snowluma '{home}' 2>/dev/null || true)"
    text_runner(container_name, ["sh", "-c", mkdir_script], display=display)

    # 无 supervisord 条目时的兜底：独立 HOME + 同组 QQ flags
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


def _kill_qq_main_pid(
    container_name: str,
    pid: int,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> None:
    """对单个 QQ 主进程发 TERM，仍存活则 KILL（Electron 常吞 TERM）。"""
    text_runner = run_exec_text or _docker_exec_text
    text_runner(
        container_name,
        ["sh", "-c", f"kill -TERM {pid} 2>/dev/null || true"],
        display=display,
    )
    time.sleep(0.5)
    text_runner(
        container_name,
        [
            "sh",
            "-c",
            (f"kill -KILL {pid} 2>/dev/null || true; pkill -KILL -P {pid} 2>/dev/null || true"),
        ],
        display=display,
    )
    time.sleep(0.4)


def stop_snowluma_qq_process_for_uin(
    container_name: str,
    uin: str,
    *,
    member_uins: list[str] | None = None,
    primary_uin: str | None = None,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> tuple[bool, int | None, str]:
    """停止指定 UIN 的 QQ 主进程；不影响容器与其它号。"""
    text_runner = run_exec_text or _docker_exec_text
    target = str(uin or "").strip()
    if not target.isdigit():
        return False, None, "QQ 无效"
    home = resolve_account_qq_home(
        target,
        member_uins=member_uins,
        primary_uin=primary_uin,
    )
    pid = resolve_qq_main_pid_for_uin(
        container_name,
        target,
        member_uins=member_uins,
        primary_uin=primary_uin,
        display=display,
        run_exec_text=text_runner,
    )
    program = resolve_qq_supervisor_program(
        container_name,
        home,
        display=display,
        run_exec_text=text_runner,
    )
    original_pid = pid

    def _current_pid() -> int | None:
        return resolve_qq_main_pid_for_uin(
            container_name,
            target,
            member_uins=member_uins,
            primary_uin=primary_uin,
            display=display,
            run_exec_text=text_runner,
        )

    # supervisord 托管（主号 qq / 副号 qq-extra-N）均带 autorestart，必须走 supervisorctl
    if program:
        ok, detail = supervisorctl_qq(container_name, "stop", program=program)
        if not ok:
            return False, pid, detail or f"未能停止 QQ（supervisorctl {program}）"
        time.sleep(0.6)
        still = _current_pid()
        if not still:
            logger.info(
                "SnowLuma 容器 %s 已经 supervisorctl 停止 UIN %s QQ（program=%s 原 pid=%s）",
                container_name,
                target,
                program,
                original_pid,
            )
            return True, original_pid, ""
        # supervisor 已 STOPPED，但 QQ --relaunch 常变成 ppid=1 孤儿，需补刀
        logger.warning(
            "SnowLuma 容器 %s supervisorctl stop %s 后仍有 QQ pid=%s，尝试强制结束",
            container_name,
            program,
            still,
        )
        pid = still

    if not pid:
        return True, None, ""

    # Electron/QQ 常吞掉 SIGTERM；supervisor 停后的 --relaunch 孤儿同样走这里
    for _ in range(3):
        cur = _current_pid()
        if not cur:
            logger.info(
                "SnowLuma 容器 %s 已停止 UIN %s 的 QQ 进程（原 pid=%s）",
                container_name,
                target,
                original_pid,
            )
            return True, original_pid, ""
        _kill_qq_main_pid(
            container_name,
            cur,
            display=display,
            run_exec_text=text_runner,
        )
    still = _current_pid()
    if still:
        return False, still, f"未能停止 QQ 进程 pid={still}"
    logger.info(
        "SnowLuma 容器 %s 已停止 UIN %s 的 QQ 进程 pid=%s",
        container_name,
        target,
        original_pid,
    )
    return True, original_pid, ""


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
    "resolve_qq_supervisor_program",
    "snowluma_qq_home_container_path",
    "snowluma_qq_home_host_path",
    "snowluma_qq_homes_host_root",
    "stop_snowluma_qq_process_for_uin",
    "supervisorctl_qq",
    "window_net_wm_pid",
]
