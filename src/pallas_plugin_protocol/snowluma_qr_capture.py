"""SnowLuma Docker：从容器 X11 桌面截屏并识别 QQ 登录二维码。"""

from __future__ import annotations

import asyncio
import io
import logging
import operator
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .contract import ACCOUNT_PROTOCOL_BACKEND_KEY, SNOWLUMA_PROTOCOL_BACKEND
from .snowluma_docker import (
    snowluma_docker_container_name,
    snowluma_docker_container_name_for_runtime,
)

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY = ":1"
REMOTE_CAPTURE_PATH = "/tmp/pallas-qr-capture.png"
REMOTE_XWD_PATH = "/tmp/pallas-qr-capture.xwd"
INITIAL_DELAY_SEC = 8.0
POLL_INTERVAL_SEC = 2.0
# 一键登录窗约 320×460：0.78 易点到页脚「账密登录」；0.68 对准蓝色「登录」
QQ_LOGIN_QUICK_CLICK_Y_RATIO = 0.68
# 扫码页「自动登录」圆钮：约在二维码卡片下方偏左（320×460 实测 ~133,372）
QQ_AUTO_LOGIN_CHECKBOX_X_RATIO = 0.415
QQ_AUTO_LOGIN_CHECKBOX_Y_RATIO = 0.81
# QQ 原生“身份失效”弹窗的确定按钮，基于 320×460 登录窗实测位置。
QQ_LOGIN_FAILURE_CONFIRM_X_RATIO = 0.75
QQ_LOGIN_FAILURE_CONFIRM_Y_RATIO = 0.60
# QQ 二维码过期遮罩的「刷新」按钮，基于 320×460 登录窗实测位置。
QQ_EXPIRED_QRCODE_REFRESH_X_RATIO = 0.50
QQ_EXPIRED_QRCODE_REFRESH_Y_RATIO = 0.61

QQ_LOGIN_WINDOW_RE = re.compile(
    r'\s+(0x[0-9a-f]+)\s+"QQ":\s+\([^)]+\)\s+(\d+)x(\d+)\+',
    re.IGNORECASE,
)
XMESSAGE_WINDOW_RE = re.compile(
    r'\s+(0x[0-9a-f]+)\s+"xmessage":',
    re.IGNORECASE,
)
QQ_LOGIN_QR_HOST_MARKERS = ("qq.com", "q.qq.com", "txz.qq.com")
EXPIRED_SESSION_MESSAGE = "账号当前登录已失效"
QQ_LOGIN_FAILURE_TEXT_MARKERS = (
    "身份验证失败",
    "身份验证失效",
    "用户身份已失效",
    "当前登录已失效",
)
QQ_EXPIRED_QRCODE_TEXT_MARKER = "当前二维码已过期"
XMESSAGE_DISMISSABLE_TEXT_MARKERS = ("fbsetbg:",)
OCR_UNAVAILABLE_SENTINEL = "__PALLAS_OCR_UNAVAILABLE__"


def account_qrcode_cache_path(account_data_dir: Path) -> Path:
    return account_data_dir / "cache" / "qrcode.png"


def account_qrcode_cache_path_for_qq(account_data_dir: Path, qq: str) -> Path:
    uin = str(qq or "").strip()
    if uin.isdigit():
        return account_data_dir / "cache" / f"qrcode_{uin}.png"
    return account_qrcode_cache_path(account_data_dir)


def resolve_snowluma_docker_container_name(account: dict) -> str:
    rid = str(account.get("snowluma_runtime_id") or "").strip()
    if rid:
        return snowluma_docker_container_name_for_runtime({
            "id": rid,
            "legacy_container_account_id": str(
                account.get("snowluma_runtime_legacy_container_account_id") or ""
            ).strip(),
        })
    return snowluma_docker_container_name(account)


def account_uses_snowluma_docker(account: dict) -> bool:
    bk = str(account.get(ACCOUNT_PROTOCOL_BACKEND_KEY) or "napcat").strip().lower()
    return bk == SNOWLUMA_PROTOCOL_BACKEND and bool(account.get("snowluma_linux_docker"))


def snowluma_qr_capture_display(config: Any | None) -> str:
    raw = str(
        getattr(config, "pallas_protocol_snowluma_qr_capture_display", DEFAULT_DISPLAY) or DEFAULT_DISPLAY
    ).strip()
    return raw or DEFAULT_DISPLAY


def snowluma_qr_capture_initial_delay(config: Any | None) -> float:
    if config is None:
        return INITIAL_DELAY_SEC
    try:
        raw = getattr(config, "pallas_protocol_snowluma_qr_capture_initial_delay_s", None)
        if raw is None:
            return INITIAL_DELAY_SEC
        return float(raw)
    except (TypeError, ValueError):
        return INITIAL_DELAY_SEC


def is_valid_qq_login_qr_payload(data: bytes) -> bool:
    try:
        text = data.decode("utf-8", errors="ignore").strip().lower()
    except (TypeError, ValueError, AttributeError):
        return False
    if not text.startswith("http"):
        return False
    return any(marker in text for marker in QQ_LOGIN_QR_HOST_MARKERS)


def find_qq_login_window(
    tree_text: str,
) -> tuple[str, int, int] | None:
    """从 ``xwininfo -root -tree`` 输出中定位 QQ 扫码登录窗（约 320×460）。"""
    windows = list_qq_login_windows(tree_text)
    return windows[0] if windows else None


def list_qq_login_windows(tree_text: str) -> list[tuple[str, int, int]]:
    """列出所有疑似 QQ 登录窗，面积小的优先（扫码/一键登录窗通常更小）。"""
    found: list[tuple[str, int, int, int]] = []
    for match in QQ_LOGIN_WINDOW_RE.finditer(tree_text):
        window_id = match.group(1)
        width = int(match.group(2))
        height = int(match.group(3))
        if width < 200 or width > 520 or height < 300 or height > 620:
            continue
        area = width * height
        found.append((window_id, width, height, area))
    found.sort(key=operator.itemgetter(3))
    return [(window_id, width, height) for window_id, width, height, _ in found]


def account_qq_uin(account: dict) -> str:
    explicit = str(account.get("qq", "") or "").strip()
    if explicit.isdigit():
        return explicit
    account_id = str(account.get("id", "") or "").strip()
    if account_id.isdigit():
        return account_id
    return ""


def account_multi_qq_context(account: dict) -> tuple[list[str], str | None]:
    raw = account.get("snowluma_member_uins")
    members: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            uin = str(item or "").strip()
            if uin.isdigit() and uin not in members:
                members.append(uin)
    primary = str(account.get("snowluma_primary_uin") or "").strip() or None
    qq = account_qq_uin(account)
    if qq and qq not in members:
        members.append(qq)
    if primary is None and members:
        primary = members[0]
    return members, primary


def resolve_prefer_qq_pid_for_account(
    account: dict,
    container_name: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> int | None:
    from .snowluma_multi_qq import resolve_qq_main_pid_for_uin

    qq = account_qq_uin(account)
    if not qq:
        return None
    members, primary = account_multi_qq_context(account)
    return resolve_qq_main_pid_for_uin(
        container_name,
        qq,
        member_uins=members or None,
        primary_uin=primary,
        display=display,
        run_exec_text=run_exec_text,
    )


def pick_qq_login_window(
    tree_text: str,
    *,
    prefer_pid: int | None = None,
    window_pid_of: Any | None = None,
) -> tuple[str, int, int] | None:
    """多登录窗时优先匹配 ``prefer_pid``（``_NET_WM_PID``），否则取面积最小者。"""
    windows = list_qq_login_windows(tree_text)
    if not windows:
        return None
    if prefer_pid and callable(window_pid_of):
        for window_id, width, height in windows:
            try:
                pid = window_pid_of(window_id)
            except Exception:
                pid = None
            if pid == prefer_pid:
                return window_id, width, height
    return windows[0]


def list_xmessage_window_ids(tree_text: str) -> list[str]:
    return [match.group(1) for match in XMESSAGE_WINDOW_RE.finditer(tree_text)]


def xmessage_is_expired_session(
    container_name: str,
    window_id: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec_text: Any | None = None,
) -> bool:
    text_runner = run_exec_text or _docker_exec_text
    title = text_runner(
        container_name,
        ["xprop", "-id", window_id, "WM_NAME"],
        display=display,
    )
    return EXPIRED_SESSION_MESSAGE in title


def is_known_dismissable_xmessage_text(text: str) -> bool:
    """仅关闭 SnowLuma 桌面已知的无关报错，未知 xmessage 保持不动。"""
    normalized = (text or "").casefold()
    return any(marker in normalized for marker in XMESSAGE_DISMISSABLE_TEXT_MARKERS)


def xmessage_is_known_dismissable(
    container_name: str,
    window_id: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
) -> bool:
    """确认 xmessage 是已知失效提示或 fbsetbg 桌面报错。"""
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    if xmessage_is_expired_session(
        container_name,
        window_id,
        display=display,
        run_exec_text=text_runner,
    ):
        return True
    if (
        exec_runner(
            container_name,
            ["xwd", "-id", window_id, "-silent", "-out", REMOTE_XWD_PATH],
            display=display,
        )
        != 0
    ):
        return False
    text = text_runner(
        container_name,
        [
            "sh",
            "-c",
            "if command -v tesseract >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then "
            f"convert {REMOTE_XWD_PATH} png:- 2>/dev/null | tesseract stdin stdout -l chi_sim+eng 2>/dev/null; "
            f"else printf '{OCR_UNAVAILABLE_SENTINEL}'; fi",
        ],
        display=display,
    )
    return OCR_UNAVAILABLE_SENTINEL not in text and is_known_dismissable_xmessage_text(text)


def is_known_qq_login_failure_text(text: str) -> bool:
    """仅匹配已人工确认的 QQ 原生失效弹窗，避免误点未知对话框。"""
    return any(marker in (text or "") for marker in QQ_LOGIN_FAILURE_TEXT_MARKERS)


def is_known_qq_expired_qrcode_text(text: str) -> bool:
    """仅匹配 QQ 二维码过期遮罩，避免误点登录窗。"""
    return QQ_EXPIRED_QRCODE_TEXT_MARKER in (text or "")


def click_known_qq_expired_qrcode_refresh(
    container_name: str,
    window_id: str,
    width: int,
    height: int,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
) -> bool | None:
    """识别 QQ 已过期二维码遮罩后点击「刷新」。"""
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    if (
        exec_runner(
            container_name,
            ["xwd", "-id", window_id, "-silent", "-out", REMOTE_XWD_PATH],
            display=display,
        )
        != 0
    ):
        return None
    text = text_runner(
        container_name,
        [
            "sh",
            "-c",
            "if command -v tesseract >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then "
            f"convert {REMOTE_XWD_PATH} png:- 2>/dev/null | tesseract stdin stdout -l chi_sim+eng 2>/dev/null; "
            f"else printf '{OCR_UNAVAILABLE_SENTINEL}'; fi",
        ],
        display=display,
    )
    if OCR_UNAVAILABLE_SENTINEL in text:
        return None
    if not is_known_qq_expired_qrcode_text(text):
        return False
    click_x = max(1, int(width * QQ_EXPIRED_QRCODE_REFRESH_X_RATIO))
    click_y = max(1, int(height * QQ_EXPIRED_QRCODE_REFRESH_Y_RATIO))
    script = (
        f"xdotool windowactivate --sync {window_id} && "
        f"xdotool mousemove --window {window_id} {click_x} {click_y} click 1"
    )
    if exec_runner(container_name, ["sh", "-c", script], display=display) != 0:
        return False
    time.sleep(2.0)
    logger.info("SnowLuma 容器 {} 已刷新过期 QQ 二维码", container_name)
    return True


def confirm_known_qq_login_failure_dialog(
    container_name: str,
    window_id: str,
    width: int,
    height: int,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
) -> bool | None:
    """OCR 识别已知身份失效提示后点击“确定”。

    返回 ``None`` 表示 OCR 不可用，调用方不会继续点击 QQ 登录窗。
    """
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    if (
        exec_runner(
            container_name,
            ["xwd", "-id", window_id, "-silent", "-out", REMOTE_XWD_PATH],
            display=display,
        )
        != 0
    ):
        return None
    text = text_runner(
        container_name,
        [
            "sh",
            "-c",
            "if command -v tesseract >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then "
            f"convert {REMOTE_XWD_PATH} png:- 2>/dev/null | tesseract stdin stdout -l chi_sim+eng 2>/dev/null; "
            f"else printf '{OCR_UNAVAILABLE_SENTINEL}'; fi",
        ],
        display=display,
    )
    if OCR_UNAVAILABLE_SENTINEL in text:
        return None
    if not is_known_qq_login_failure_text(text):
        return False
    click_x = max(1, int(width * QQ_LOGIN_FAILURE_CONFIRM_X_RATIO))
    click_y = max(1, int(height * QQ_LOGIN_FAILURE_CONFIRM_Y_RATIO))
    script = (
        f"xdotool windowactivate --sync {window_id} && "
        f"xdotool mousemove --window {window_id} {click_x} {click_y} click 1"
    )
    if exec_runner(container_name, ["sh", "-c", script], display=display) != 0:
        return False
    time.sleep(1.0)
    logger.info("SnowLuma 容器 {} 已确认 QQ 身份失效提示", container_name)
    return True


def locate_qq_login_window(
    container_name: str,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
    prefer_pid: int | None = None,
) -> tuple[str, int, int] | None:
    from .snowluma_multi_qq import window_net_wm_pid

    text_runner = run_exec_text or _docker_exec_text
    exec_runner = run_exec or _docker_exec

    def pick(tree: str) -> tuple[str, int, int] | None:
        return pick_qq_login_window(
            tree,
            prefer_pid=prefer_pid,
            window_pid_of=lambda wid: window_net_wm_pid(
                container_name,
                wid,
                display=display,
                run_exec_text=text_runner,
            ),
        )

    tree = text_runner(
        container_name,
        ["xwininfo", "-root", "-tree"],
        display=display,
    )
    if not tree:
        return None
    xmessage_ids = list_xmessage_window_ids(tree)
    if xmessage_ids:
        if len(xmessage_ids) != 1 or not xmessage_is_known_dismissable(
            container_name,
            xmessage_ids[0],
            display=display,
            run_exec=exec_runner,
            run_exec_text=text_runner,
        ):
            return None
        exec_runner(
            container_name,
            ["xkill", "-id", xmessage_ids[0]],
            display=display,
        )
        tree = text_runner(
            container_name,
            ["xwininfo", "-root", "-tree"],
            display=display,
        )
        if not tree or list_xmessage_window_ids(tree):
            return None
    login = pick(tree)
    if login is None:
        return None
    window_id, width, height = login
    confirmed = confirm_known_qq_login_failure_dialog(
        container_name,
        window_id,
        width,
        height,
        display=display,
        run_exec=exec_runner,
        run_exec_text=text_runner,
    )
    if confirmed is None:
        return None
    if not confirmed:
        return login
    tree = text_runner(
        container_name,
        ["xwininfo", "-root", "-tree"],
        display=display,
    )
    if not tree or list_xmessage_window_ids(tree):
        return None
    return pick(tree)


def click_qq_login_window(
    container_name: str,
    window_id: str,
    width: int,
    height: int,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
) -> bool:
    """点击已确认的 QQ 一键登录蓝色按钮。"""
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    if not _command_available_in_container(container_name, "xdotool", display=display, run_exec_text=text_runner):
        logger.info("SnowLuma 容器 {} 未安装 xdotool，无法自动点击「登录」", container_name)
        return False
    click_x = max(1, int(width * 0.5))
    click_y = max(1, int(height * QQ_LOGIN_QUICK_CLICK_Y_RATIO))
    script = (
        f"xdotool windowactivate --sync {window_id} && "
        f"xdotool mousemove --window {window_id} {click_x} {click_y} click 1"
    )
    exec_runner(container_name, ["sh", "-c", script], display=display)
    time.sleep(2.0)
    return True


def click_qq_auto_login_checkbox(
    container_name: str,
    window_id: str,
    width: int,
    height: int,
    *,
    display: str = DEFAULT_DISPLAY,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
) -> bool:
    """勾选扫码/登录页「自动登录」，便于容器重建后 QQ 自行保持会话。"""
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    if not _command_available_in_container(container_name, "xdotool", display=display, run_exec_text=text_runner):
        logger.info(
            "SnowLuma 容器 {} 未安装 xdotool，无法勾选「自动登录」",
            container_name,
        )
        return False
    click_x = max(1, int(width * QQ_AUTO_LOGIN_CHECKBOX_X_RATIO))
    click_y = max(1, int(height * QQ_AUTO_LOGIN_CHECKBOX_Y_RATIO))
    script = (
        f"xdotool windowactivate --sync {window_id} && "
        f"xdotool mousemove --sync --window {window_id} {click_x} {click_y} && "
        f"xdotool click 1"
    )
    if exec_runner(container_name, ["sh", "-c", script], display=display) != 0:
        return False
    time.sleep(0.6)
    logger.info(
        "SnowLuma 容器 {} 已点击 QQ「自动登录」勾选（{}x{} @ {},{}）",
        container_name,
        width,
        height,
        click_x,
        click_y,
    )
    return True


def qq_auto_login_checkbox_is_checked(
    screen_png: bytes,
    width: int,
    height: int,
) -> bool:
    """按二维码页圆钮的 QQ 蓝色像素判断「自动登录」是否已选中。"""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(screen_png)).convert("RGB")
    except Exception:
        return False
    center_x = max(1, min(image.width - 1, int(width * QQ_AUTO_LOGIN_CHECKBOX_X_RATIO)))
    center_y = max(1, min(image.height - 1, int(height * QQ_AUTO_LOGIN_CHECKBOX_Y_RATIO)))
    blue_pixels = 0
    for x in range(max(0, center_x - 7), min(image.width, center_x + 8)):
        for y in range(max(0, center_y - 7), min(image.height, center_y + 8)):
            red, green, blue = image.getpixel((x, y))
            if red <= 80 and 110 <= green <= 190 and blue >= 200:
                blue_pixels += 1
    return blue_pixels >= 12


def ensure_qq_auto_login_checked(
    account: dict,
    *,
    config: Any | None = None,
    run_exec: Any | None = None,
    run_cp: Any | None = None,
    run_exec_text: Any | None = None,
    screen_png: bytes | None = None,
    login_window: tuple[str, int, int] | None = None,
) -> bool:
    """定位 QQ 登录窗，确认「自动登录」勾选状态后按需点击。"""
    if not account_uses_snowluma_docker(account):
        return False
    container = resolve_snowluma_docker_container_name(account)
    display = snowluma_qr_capture_display(config)
    text_runner = run_exec_text or _docker_exec_text
    prefer_pid = resolve_prefer_qq_pid_for_account(
        account,
        container,
        display=display,
        run_exec_text=text_runner,
    )
    login = login_window or locate_qq_login_window(
        container,
        display=display,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
        prefer_pid=prefer_pid,
    )
    if login is None:
        return False
    window_id, width, height = login
    before = screen_png or capture_screen_png_from_container(
        container,
        display=display,
        window_id=window_id,
        run_exec=run_exec,
        run_cp=run_cp,
    )
    if before and qq_auto_login_checkbox_is_checked(before, width, height):
        return True
    clicked = click_qq_auto_login_checkbox(
        container,
        window_id,
        width,
        height,
        display=display,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
    )
    if not clicked:
        return False
    after = capture_screen_png_from_container(
        container,
        display=display,
        window_id=window_id,
        run_exec=run_exec,
        run_cp=run_cp,
    )
    return bool(after and qq_auto_login_checkbox_is_checked(after, width, height))


def attempt_snowluma_quick_login(
    account: dict,
    *,
    config: Any | None = None,
    run_exec: Any | None = None,
    run_exec_text: Any | None = None,
) -> bool:
    """已登录过账号的常见界面：点头像下的蓝色「登录」，而非截二维码。"""
    if not account_uses_snowluma_docker(account):
        return False
    container = resolve_snowluma_docker_container_name(account)
    display = snowluma_qr_capture_display(config)
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    prefer_pid = resolve_prefer_qq_pid_for_account(
        account,
        container,
        display=display,
        run_exec_text=text_runner,
    )
    login = locate_qq_login_window(
        container,
        display=display,
        run_exec=exec_runner,
        run_exec_text=run_exec_text,
        prefer_pid=prefer_pid,
    )
    if login is None:
        return False
    window_id, width, height = login
    ensure_qq_auto_login_checked(
        account,
        config=config,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
        login_window=login,
    )
    clicked = click_qq_login_window(
        container,
        window_id,
        width,
        height,
        display=display,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
    )
    if clicked:
        logger.info(
            "SnowLuma 容器 {} 已点击 QQ「登录」（一键登录界面）qq={}",
            container,
            account_qq_uin(account) or "-",
        )
    return clicked


def capture_screen_png_from_container(
    container_name: str,
    *,
    display: str = DEFAULT_DISPLAY,
    window_id: str | None = None,
    run_exec: Any | None = None,
    run_cp: Any | None = None,
    xwd_to_png: Any | None = None,
) -> bytes | None:
    """在 SnowLuma 容器内截屏，返回 PNG 字节。优先截取 QQ 登录窗。"""
    exec_runner = run_exec or _docker_exec
    cp_runner = run_cp or _docker_cp_out
    convert_runner = xwd_to_png or _xwd_bytes_to_png

    if window_id:
        rc = exec_runner(
            container_name,
            ["xwd", "-id", window_id, "-silent", "-out", REMOTE_XWD_PATH],
            display=display,
        )
        if rc == 0:
            xwd_data = cp_runner(container_name, REMOTE_XWD_PATH)
            if xwd_data:
                png = convert_runner(xwd_data)
                if png:
                    return png

    capture_cmds = (
        ["import", "-window", "root", REMOTE_CAPTURE_PATH],
        ["scrot", REMOTE_CAPTURE_PATH],
    )
    for tail in capture_cmds:
        rc = exec_runner(
            container_name,
            tail,
            display=display,
        )
        if rc != 0:
            continue
        data = cp_runner(container_name, REMOTE_CAPTURE_PATH)
        if data:
            return data

    rc = exec_runner(
        container_name,
        ["xwd", "-root", "-silent", "-out", REMOTE_XWD_PATH],
        display=display,
    )
    if rc != 0:
        return None
    xwd_data = cp_runner(container_name, REMOTE_XWD_PATH)
    if not xwd_data:
        return None
    return convert_runner(xwd_data)


def _xwd_bytes_to_png(xwd_bytes: bytes) -> bytes | None:
    """宿主机 ImageMagick 将 XWD 转为 PNG（SnowLuma 镜像通常仅有 xwd）。"""
    for argv in (
        ["convert", "xwd:-", "png:-"],
        ["magick", "xwd:-", "png:-"],
    ):
        try:
            proc = subprocess.run(
                argv,
                input=xwd_bytes,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            logger.debug("SnowLuma XWD 转 PNG 失败 {}: {}", argv[0], err)
            continue
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        if stderr:
            logger.debug("SnowLuma XWD 转 PNG {}: {}", argv[0], stderr)
    return None


def decode_qr_codes_from_image(img: Any) -> list[tuple[Any, int]]:
    """pyzbar 解码；小图（QQ 登录窗 ~320px）放大后再试。返回 (code, scale)。"""
    from PIL import Image
    from pyzbar.pyzbar import decode as pyzbar_decode

    attempts: list[tuple[Any, int]] = [(img, 1), (img.convert("L"), 1)]
    if max(img.width, img.height) < 640:
        scale = 3
        enlarged = img.resize(
            (img.width * scale, img.height * scale),
            Image.Resampling.LANCZOS,
        )
        attempts.extend(((enlarged, scale), (enlarged.convert("L"), scale)))
    for candidate, scale in attempts:
        codes = pyzbar_decode(candidate)
        if codes:
            return [(code, scale) for code in codes]
    return []


def extract_qr_png_from_screen(png_bytes: bytes) -> bytes | None:
    """从截图中检测 QQ 登录 QR 并裁切为 PNG（须为可扫码的 txz.qq.com 链接）。"""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("SnowLuma QR capture 需要 pillow，请安装 pallas-plugin-protocol 依赖")
        return None

    try:
        from pyzbar.pyzbar import decode as pyzbar_decode  # noqa: F401
    except ImportError:
        logger.warning("SnowLuma QR capture 需要 pyzbar（及系统 libzbar），无法识别二维码")
        return None

    img = Image.open(io.BytesIO(png_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    codes = decode_qr_codes_from_image(img)
    if not codes:
        return None

    for code, scale in codes:
        if not is_valid_qq_login_qr_payload(code.data):
            continue
        rect = code.rect
        if scale > 1:
            rect = rect.__class__(
                left=rect.left // scale,
                top=rect.top // scale,
                width=max(1, rect.width // scale),
                height=max(1, rect.height // scale),
            )
        pad = max(8, min(rect.width, rect.height) // 8)
        left = max(0, rect.left - pad)
        top = max(0, rect.top - pad)
        right = min(img.width, rect.left + rect.width + pad)
        bottom = min(img.height, rect.top + rect.height + pad)
        crop = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()
    return None


def qrcode_cache_looks_valid(path: Path) -> bool:
    """已有 cache/qrcode.png 是否仍可扫码（过滤历史整屏回退）。"""
    try:
        payload = path.read_bytes()
    except OSError:
        return False
    if not payload:
        return False
    return extract_qr_png_from_screen(payload) is not None


def write_qrcode_cache(account_data_dir: Path, png_bytes: bytes, *, qq: str = "") -> Path:
    cache_dir = account_data_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    legacy = account_qrcode_cache_path(account_data_dir)
    legacy.write_bytes(png_bytes)
    uin = str(qq or "").strip()
    if uin.isdigit():
        out = account_qrcode_cache_path_for_qq(account_data_dir, uin)
        out.write_bytes(png_bytes)
        return out
    return legacy


def capture_snowluma_qrcode_once(
    account: dict,
    *,
    config: Any | None = None,
    run_exec: Any | None = None,
    run_cp: Any | None = None,
    run_exec_text: Any | None = None,
) -> Path | None:
    if not account_uses_snowluma_docker(account):
        return None
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip())
    if not account_data_dir:
        return None

    container = resolve_snowluma_docker_container_name(account)
    display = snowluma_qr_capture_display(config)
    exec_runner = run_exec or _docker_exec
    text_runner = run_exec_text or _docker_exec_text
    prefer_pid = resolve_prefer_qq_pid_for_account(
        account,
        container,
        display=display,
        run_exec_text=text_runner,
    )

    login = locate_qq_login_window(
        container,
        display=display,
        run_exec=exec_runner,
        run_exec_text=text_runner,
        prefer_pid=prefer_pid,
    )
    if login is None:
        return None
    window_id, width, height = login
    qq = account_qq_uin(account)

    def capture_once() -> bytes | None:
        return capture_screen_png_from_container(
            container,
            display=display,
            window_id=window_id,
            run_exec=exec_runner,
            run_cp=run_cp,
        )

    screen = capture_once()
    if not screen:
        return None

    qr_png = extract_qr_png_from_screen(screen)
    if qr_png:
        ensure_qq_auto_login_checked(
            account,
            config=config,
            run_exec=run_exec,
            run_cp=run_cp,
            run_exec_text=run_exec_text,
            screen_png=screen,
            login_window=login,
        )
        return write_qrcode_cache(account_data_dir, qr_png, qq=qq)

    refreshed = click_known_qq_expired_qrcode_refresh(
        container,
        window_id,
        width,
        height,
        display=display,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
    )
    if refreshed:
        screen = capture_once()
        if screen:
            qr_png = extract_qr_png_from_screen(screen)
            if qr_png:
                ensure_qq_auto_login_checked(
                    account,
                    config=config,
                    run_exec=run_exec,
                    run_cp=run_cp,
                    run_exec_text=run_exec_text,
                    screen_png=screen,
                    login_window=login,
                )
                return write_qrcode_cache(account_data_dir, qr_png, qq=qq)

    logger.debug(
        "SnowLuma 截屏未识别到有效 QQ 登录二维码（可能为一键登录界面或仍在加载）: container={} qq={}",
        container,
        qq or "-",
    )
    return None


def restore_snowluma_qq_login(
    account: dict,
    *,
    config: Any | None = None,
    prefer_quick_login: bool = False,
    run_exec: Any | None = None,
    run_cp: Any | None = None,
    run_exec_text: Any | None = None,
) -> dict[str, Any]:
    """恢复 QQ 登录：有二维码则写入 cache；否则自动点击「登录」。

    ``prefer_quick_login``：容器重启后常见「一键登录」窗时优先点「登录」，
    避免先点扫码页「刷新」坐标误触。
    """
    path = capture_snowluma_qrcode_once(
        account,
        config=config,
        run_exec=run_exec,
        run_cp=run_cp,
        run_exec_text=run_exec_text,
    )
    if path is not None:
        return {"mode": "qrcode", "qrcode_path": str(path)}
    ensure_qq_auto_login_checked(
        account,
        config=config,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
    )
    clicked = attempt_snowluma_quick_login(
        account,
        config=config,
        run_exec=run_exec,
        run_exec_text=run_exec_text,
    )
    if clicked:
        # QQ 点击一键登录后可能继续弹出失效确认框；再次捕获会确认它并取二维码。
        path = capture_snowluma_qrcode_once(
            account,
            config=config,
            run_exec=run_exec,
            run_cp=run_cp,
            run_exec_text=run_exec_text,
        )
        if path is not None:
            return {"mode": "qrcode", "qrcode_path": str(path)}
        return {
            "mode": "quick_login",
            "message": "已点击 QQ「登录」按钮，请稍候确认账号上线",
        }
    return {
        "mode": "failed",
        "message": "未识别到二维码，且无法自动点击「登录」；请确认 QQ 登录窗已打开",
    }


async def wait_and_restore_snowluma_qq_login(
    account: dict,
    *,
    config: Any | None = None,
    timeout_sec: float = 90.0,
    initial_delay_sec: float | None = None,
    poll_interval_sec: float = POLL_INTERVAL_SEC,
    prefer_quick_login: bool = True,
) -> dict[str, Any]:
    """容器起来后轮询：优先一键登录；直到成功或超时。"""
    delay = initial_delay_sec if initial_delay_sec is not None else snowluma_qr_capture_initial_delay(config)
    if delay > 0:
        await asyncio.sleep(delay)

    deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_sec))
    last: dict[str, Any] = {
        "mode": "failed",
        "message": "超时仍未识别到二维码或一键登录窗",
    }
    while asyncio.get_running_loop().time() < deadline:
        restored = await asyncio.to_thread(
            restore_snowluma_qq_login,
            account,
            config=config,
            prefer_quick_login=prefer_quick_login,
        )
        last = restored
        mode = str(restored.get("mode") or "")
        if mode in {"qrcode", "quick_login"}:
            return restored
        await asyncio.sleep(poll_interval_sec)
    return last


async def wait_and_capture_snowluma_qrcode(
    account: dict,
    since: datetime,
    *,
    config: Any | None = None,
    timeout_sec: int = 60,
    initial_delay_sec: float | None = None,
    poll_interval_sec: float = POLL_INTERVAL_SEC,
) -> Path | None:
    """重启后轮询截屏，直到写出新的 cache/qrcode.png 或超时。"""
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip())
    if not account_data_dir:
        return None

    delay = initial_delay_sec if initial_delay_sec is not None else snowluma_qr_capture_initial_delay(config)
    if delay > 0:
        await asyncio.sleep(delay)

    deadline = asyncio.get_running_loop().time() + timeout_sec
    last_payload: bytes | None = None
    while asyncio.get_running_loop().time() < deadline:
        captured = await asyncio.to_thread(
            capture_snowluma_qrcode_once,
            account,
            config=config,
        )
        if captured is not None and captured.is_file():
            try:
                mtime = datetime.fromtimestamp(captured.stat().st_mtime, tz=since.tzinfo)
                payload = captured.read_bytes()
                if mtime >= since and payload and payload != last_payload:
                    return captured
                last_payload = payload
            except OSError:
                pass
        await asyncio.sleep(poll_interval_sec)
    return None


def _command_available_in_container(
    container_name: str,
    command: str,
    *,
    display: str,
    run_exec_text: Any,
) -> bool:
    path = run_exec_text(
        container_name,
        ["sh", "-c", f"command -v {command}"],
        display=display,
    )
    return bool(path and path.strip())


def _docker_exec(container_name: str, cmd_tail: list[str], *, display: str) -> int:
    argv = [
        "docker",
        "exec",
        "-u",
        "snowluma",
        "-e",
        f"DISPLAY={display}",
        container_name,
        *cmd_tail,
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        logger.debug("SnowLuma docker exec 截屏失败: {}", err)
        return 1
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if stderr:
            logger.debug("SnowLuma docker exec {}: {}", cmd_tail[0], stderr)
        return int(proc.returncode)
    return 0


def _docker_exec_text(container_name: str, cmd_tail: list[str], *, display: str) -> str:
    argv = [
        "docker",
        "exec",
        "-u",
        "snowluma",
        "-e",
        f"DISPLAY={display}",
        container_name,
        *cmd_tail,
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        logger.debug("SnowLuma docker exec 失败: {}", err)
        return ""
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if stderr:
            logger.debug("SnowLuma docker exec {}: {}", cmd_tail[0], stderr)
        return ""
    return proc.stdout or ""


def _docker_cp_out(container_name: str, remote_path: str) -> bytes | None:
    """读取容器内文件字节。不用 ``docker cp … -``（stdout 为 tar 包）。"""
    try:
        proc = subprocess.run(
            ["docker", "exec", container_name, "cat", remote_path],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        logger.debug("SnowLuma docker exec cat 失败: {}", err)
        return None
    if proc.returncode != 0 or not proc.stdout:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        if stderr:
            logger.debug("SnowLuma docker exec cat {}: {}", remote_path, stderr)
        return None
    return proc.stdout
