"""牛牛重新上号 / 创建牛牛：无 NoneBot 依赖的核心流程。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from nonebot import get_driver
from pallas.api.config import user_is_bot_admin
from pallas.api.presence import bot_has_cluster_connection
from pallas.core.foundation.db import make_bot_config_repository
from pallas.core.platform.shard.coord.relogin_payload import (
    ReloginHandleResult,
    ReplyItem,
)

_CANCEL_WORDS = {"取消", "cancel", "退出", "quit"}
_RISK_DEVICE_YES = {"是", "yes", "y"}
_RISK_DEVICE_NO = {"否", "no", "n"}


@dataclass
class ReloginSession:
    kind: Literal["relogin", "create"]
    step: str
    data: dict[str, Any] = field(default_factory=dict)


_sessions: dict[str, ReloginSession] = {}


def session_key(bot_id: str, user_id: str) -> str:
    return f"{bot_id}:{user_id}"


def clear_session(bot_id: str, user_id: str) -> None:
    _sessions.pop(session_key(bot_id, user_id), None)


def user_is_superuser_id(user_id: str) -> bool:
    driver = get_driver()
    sup = getattr(driver.config, "superusers", None) or set()
    uid = str(user_id).strip()
    return uid in {str(x) for x in sup}


def extract_qq(arg: str) -> str:
    text = (arg or "").strip()
    return text if text.isdigit() else ""


def parse_risk_device_answer(text: str) -> bool | None:
    answer = (text or "").strip().casefold()
    if answer in _RISK_DEVICE_YES:
        return True
    if answer in _RISK_DEVICE_NO:
        return False
    return None


async def bot_id_exists_in_db(bot_id: int) -> bool:
    try:
        repo = make_bot_config_repository()
        return await repo.get(bot_id) is not None
    except Exception:
        return False


async def wait_qrcode(account_data_dir: Path, since: datetime, timeout_sec: int = 60) -> Path | None:
    from pallas_plugin_protocol import manager as protocol_manager

    for aid, acc in (getattr(protocol_manager, "_accounts", {}) or {}).items():
        if str(acc.get("account_data_dir", "")).strip() == str(account_data_dir):
            return await protocol_manager.wait_account_qrcode(aid, since, timeout_sec=timeout_sec)

    qr_path = account_data_dir / "cache" / "qrcode.png"
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        if qr_path.is_file():
            try:
                mtime = datetime.fromtimestamp(qr_path.stat().st_mtime, tz=since.tzinfo)
                if mtime >= since:
                    return qr_path
            except OSError:
                pass
        await asyncio.sleep(1.2)
    return None


def append_text(result: ReloginHandleResult, text: str) -> None:
    result.replies.append(ReplyItem(kind="text", content=text))


def append_qrcode(result: ReloginHandleResult, qr_path: Path) -> None:
    try:
        data = base64.b64encode(qr_path.read_bytes()).decode("ascii")
    except OSError as err:
        append_text(result, f"二维码读取失败：{err}")
        return
    result.replies.append(ReplyItem(kind="image_base64", content=data))


_RELOGIN_QR_WAIT_SEC = 60
_RELOGIN_CONNECT_WAIT_SEC = 90
_RELOGIN_PROCESS_WAIT_SEC = 90


async def wait_bot_connected_after_login(protocol_manager: Any, qq: str, *, timeout_sec: int) -> bool:
    if protocol_manager.is_bot_connected(qq):
        return True
    return await protocol_manager.wait_account_bot_connected(qq, timeout_sec=timeout_sec)


async def finish_relogin_after_restart(
    protocol_manager: Any,
    qq: str,
    account: dict,
    result: ReloginHandleResult,
    *,
    started_at: datetime,
) -> None:
    from pallas_plugin_protocol.snowluma_qr_capture import account_uses_snowluma_docker

    if account_uses_snowluma_docker(account):
        if not await protocol_manager.wait_account_process_running(qq, timeout_sec=_RELOGIN_PROCESS_WAIT_SEC):
            append_text(
                result,
                "协议容器启动超时，请稍后在协议端控制台查看或联系管理员。",
            )
            return

        try:
            meta = await protocol_manager.refresh_account_qrcode(qq, timeout_sec=_RELOGIN_QR_WAIT_SEC)
        except (KeyError, ValueError) as err:
            append_text(
                result,
                f"首次恢复登录未完成（{err}），正在继续等待二维码或一键登录…",
            )
            meta = {}

        if str(meta.get("login_mode") or "") == "quick_login":
            append_text(result, "正在登录，请稍候。")
            if await wait_bot_connected_after_login(protocol_manager, qq, timeout_sec=_RELOGIN_CONNECT_WAIT_SEC):
                append_text(result, "牛牛已重新上线。")
                return
            append_text(result, "登录超时，请稍后在协议端控制台查看。")
            return

        if meta.get("exists"):
            qr_path = protocol_manager.account_qrcode_path(qq)
            if qr_path is not None:
                append_text(result, "请扫描二维码完成登录。")
                append_qrcode(result, qr_path)
                return

    qr_path = await protocol_manager.wait_account_qrcode(qq, started_at, timeout_sec=_RELOGIN_QR_WAIT_SEC)
    if qr_path is None:
        append_text(
            result,
            f"已完成启动，但在 {_RELOGIN_QR_WAIT_SEC} 秒内未检测到新的二维码，请寻找号主上报情况。",
        )
        return

    append_text(result, "请扫描二维码完成登录。")
    append_qrcode(result, qr_path)


async def run_relogin_restart(
    qq: str,
    result: ReloginHandleResult,
    *,
    reset_login_state: bool = False,
) -> None:
    from pallas_plugin_protocol import manager as protocol_manager

    if not reset_login_state and bot_has_cluster_connection(int(qq)):
        append_text(result, "牛牛已在线，无需重新上号。")
        return

    account = protocol_manager.get_account(qq) or {}
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip())
    if not account_data_dir:
        append_text(result, "账号目录缺失，无法执行重新上号。")
        return

    from pallas_plugin_protocol.snowluma_qr_capture import account_uses_snowluma_docker

    started_at = datetime.now().astimezone()
    if reset_login_state:
        try:
            await protocol_manager.reset_snowluma_login_state_and_recreate(qq)
        except (KeyError, ValueError) as err:
            append_text(result, f"重置登录态失败：{err}")
            return
        await finish_relogin_after_restart(protocol_manager, qq, account, result, started_at=started_at)
        return

    if account_uses_snowluma_docker(account) and protocol_manager._linux_docker_container_running_sync(account):
        append_text(result, "正在恢复登录...")
        await finish_relogin_after_restart(protocol_manager, qq, account, result, started_at=started_at)
        return

    append_text(result, "正在启动协议端...")
    try:
        await protocol_manager.restart_account(qq)
    except Exception as err:
        append_text(result, f"启动失败：{err}")
        return

    await finish_relogin_after_restart(protocol_manager, qq, account, result, started_at=started_at)


async def handle_relogin_message(
    *,
    bot_id: str,
    user_id: str,
    text: str,
) -> ReloginHandleResult:
    result = ReloginHandleResult()
    key = session_key(bot_id, user_id)
    session = _sessions.get(key)
    plain = (text or "").strip()
    su = user_is_superuser_id(user_id)

    if session is None and plain.startswith("牛牛重新上号"):
        arg = plain.removeprefix("牛牛重新上号").strip()
        qq = extract_qq(arg)
        if qq:
            session = ReloginSession(kind="relogin", step="validate_qq", data={"qq": qq})
            _sessions[key] = session
        else:
            append_text(result, "请回复要重新上号的QQ号：")
            _sessions[key] = ReloginSession(kind="relogin", step="await_qq")
            result.session_active = True
            return result

    if session is None and plain.startswith("创建牛牛"):
        arg = plain.removeprefix("创建牛牛").strip()
        if arg:
            parts = arg.split()
            if len(parts) < 3:
                append_text(result, "参数不足，需要：牛牛昵称 牛牛账号 号主账号（至少一个）")
                return result
            display_name, qq, *owner_qqs = parts
            if not qq.isdigit() or len(qq) < 5:
                append_text(result, "牛牛账号格式不正确")
                return result
            invalid = [oq for oq in owner_qqs if not oq.isdigit()]
            if invalid:
                append_text(result, f"号主账号格式不正确：{'、'.join(invalid)}")
                return result
            session = ReloginSession(
                kind="create",
                step="execute",
                data={
                    "display_name": display_name,
                    "qq": qq,
                    "owner_qqs": owner_qqs,
                    "interactive": False,
                },
            )
            _sessions[key] = session
        else:
            append_text(result, "请输入牛牛昵称：")
            _sessions[key] = ReloginSession(kind="create", step="await_name", data={"interactive": True})
            result.session_active = True
            return result

    if session is None:
        return result

    if session.kind == "relogin":
        await handle_relogin_session(session, user_id, plain, su, result, key)
    else:
        await handle_create_session(session, plain, result, key)

    if not result.session_active:
        clear_session(bot_id, user_id)
    return result


async def handle_relogin_session(
    session: ReloginSession,
    user_id: str,
    plain: str,
    su: bool,
    result: ReloginHandleResult,
    key: str,
) -> None:
    if session.step == "await_qq":
        if plain in _CANCEL_WORDS:
            append_text(result, "已取消重新上号。")
            return
        qq = extract_qq(plain)
        if not qq:
            result.reject_hint = "QQ号格式不正确，请重新输入："
            result.session_active = True
            return
        session.step = "validate_qq"
        session.data["qq"] = qq

    if session.step == "validate_qq":
        from pallas_plugin_protocol import manager as protocol_manager

        qq = str(session.data.get("qq", ""))
        is_target_admin = await user_is_bot_admin(int(qq), int(user_id))
        if not (is_target_admin or su):
            append_text(result, f"你不是 {qq} 的管理员，无法执行重新上号。")
            return

        if protocol_manager.has_account(qq):
            account = protocol_manager.get_account(qq) or {}
            from pallas_plugin_protocol.snowluma_qr_capture import (
                account_uses_snowluma_docker,
            )

            if account_uses_snowluma_docker(account):
                session.step = "await_risk_device"
                append_text(result, "是否提示“风险/外挂设备”？（是/否）")
                result.session_active = True
                _sessions[key] = session
                return
            session.step = "execute"
        elif not await bot_id_exists_in_db(int(qq)):
            append_text(result, f"数据库中不存在账号为：{qq} 的牛牛")
            return
        else:
            session.step = "await_nickname"
            append_text(result, "该账号协议端不存在，请输入牛牛昵称以自动创建：")
            result.session_active = True
            _sessions[key] = session
            return

    if session.step == "await_nickname":
        qq = str(session.data.get("qq", ""))
        if plain in _CANCEL_WORDS:
            append_text(result, "已取消重新上号。")
            return
        nickname = plain.strip()
        if not nickname:
            result.reject_hint = "昵称不能为空，请重新输入："
            result.session_active = True
            return
        try:
            from pallas_plugin_protocol import manager as protocol_manager

            protocol_manager.create_account({"qq": qq, "display_name": nickname, "enabled": True})
            append_text(result, f"已创建 {nickname} 并继续上号流程。")
        except Exception as err:
            append_text(result, f"自动创建协议端失败：{err}")
            return
        session.step = "execute"

    if session.step == "await_risk_device":
        if plain in _CANCEL_WORDS:
            append_text(result, "已取消重新上号。")
            return
        reset_login_state = parse_risk_device_answer(plain)
        if reset_login_state is None:
            result.reject_hint = "请回复 是 或 否："
            result.session_active = True
            return
        session.data["reset_login_state"] = reset_login_state
        session.step = "execute"

    if session.step == "execute":
        qq = str(session.data.get("qq", ""))
        await run_relogin_restart(
            qq,
            result,
            reset_login_state=bool(session.data.get("reset_login_state")),
        )


async def handle_create_session(
    session: ReloginSession,
    plain: str,
    result: ReloginHandleResult,
    key: str,
) -> None:
    interactive = bool(session.data.get("interactive"))

    if session.step == "await_name":
        if plain in _CANCEL_WORDS:
            append_text(result, "已取消创建牛牛。")
            return
        if not plain.strip():
            result.reject_hint = "昵称不能为空，请重新输入："
            result.session_active = True
            return
        session.data["display_name"] = plain.strip()
        session.step = "await_qq"
        if interactive:
            append_text(result, "请输入牛牛QQ号：")
            result.session_active = True
            _sessions[key] = session
        return

    if session.step == "await_qq":
        if plain in _CANCEL_WORDS:
            append_text(result, "已取消创建牛牛。")
            return
        qq = plain.strip()
        if not qq.isdigit() or len(qq) < 5:
            result.reject_hint = "QQ号格式不正确，请重新输入："
            result.session_active = True
            return
        session.data["qq"] = qq
        session.step = "await_owners"
        if interactive:
            append_text(result, "请输入号主QQ号（如有多个用空格分隔）：")
            result.session_active = True
            _sessions[key] = session
        return

    if session.step == "await_owners":
        if plain in _CANCEL_WORDS:
            append_text(result, "已取消创建牛牛。")
            return
        owner_qqs = plain.strip().split()
        if not owner_qqs:
            result.reject_hint = "号主账号不能为空，请重新输入："
            result.session_active = True
            return
        invalid = [oq for oq in owner_qqs if not oq.isdigit()]
        if invalid:
            result.reject_hint = f"号主账号格式不正确：{'、'.join(invalid)}，请重新输入："
            result.session_active = True
            return
        session.data["owner_qqs"] = owner_qqs
        session.step = "execute"

    if session.step != "execute":
        return

    from pallas_plugin_protocol import manager as protocol_manager

    display_name = str(session.data.get("display_name", "")).strip()
    qq = str(session.data.get("qq", "")).strip()
    owner_qqs = list(session.data.get("owner_qqs") or [])

    try:
        protocol_manager.create_account({"qq": qq, "display_name": display_name, "enabled": True})
    except Exception as err:
        append_text(result, f"创建账号失败：{err}")
        return

    account = protocol_manager.get_account(qq) or {}
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip())
    if not account_data_dir:
        append_text(result, "账号已创建，但账号目录缺失，无法启动。")
        return

    append_text(result, "正在启动协议端...")
    started_at = datetime.now().astimezone()
    try:
        await protocol_manager.start_account(qq)
    except Exception as err:
        append_text(result, f"账号已创建，但启动失败：{err}")
        return

    owner_ids = [int(oq) for oq in owner_qqs]
    try:
        repo = make_bot_config_repository()
        await repo.upsert_field(int(qq), "admins", owner_ids)
        from pallas.api.config import invalidate_bot_admins_cache

        await invalidate_bot_admins_cache(int(qq))
    except Exception as err:
        append_text(result, f"账号已创建并启动，但写入号主失败：{err}")
        return

    qr_path = await protocol_manager.wait_account_qrcode(qq, started_at)
    if qr_path is not None:
        append_text(result, "请使用下面二维码完成登录：")
        append_qrcode(result, qr_path)

    owners_str = "、".join(owner_qqs)
    timeout_hint = "\n但在 60 秒内未检测到新的二维码文件，请到协议端控制台查看或联系管理员。" if qr_path is None else ""
    append_text(result, f"{display_name}：{qq} 已创建并启动。\n号主：{owners_str}{timeout_hint}")
