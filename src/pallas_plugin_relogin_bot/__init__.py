from datetime import datetime
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.params import ArgPlainText, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State
from pallas.api.config import user_is_bot_admin
from pallas.api.metadata import (
    PLUGIN_EXTRA_VERSION,
    PLUGIN_HOMEPAGE,
    PLUGIN_MENU_TEMPLATE,
    SCENE_PRIVATE,
    join_usage,
    usage_line,
)
from pallas.api.perm import private_message_permission_for_command
from pallas.api.platform import is_hub_role
from pallas.api.utils import reply_private_message
from pallas.core.foundation.db import make_bot_config_repository
from pallas.product.llm.knowledge.declare import knowledge_source_row

if is_hub_role():
    from nonebot import get_app
    from pallas.core.platform.shard.coord.relogin_hub_routes import (
        mount_relogin_hub_routes,
    )

    mount_relogin_hub_routes(get_app())

from pallas_plugin_protocol import manager as protocol_manager

__all__ = ["create_cmd", "relogin_cmd"]

__plugin_meta__ = PluginMetadata(
    name="牛牛重新上号",
    description="号主重启协议端收二维码；超管可创建新牛牛。",
    usage=join_usage(
        usage_line("牛牛重新上号 [QQ]", "重启并私聊推送登录二维码"),
        usage_line("创建牛牛 …", "超管新建实例"),
    ),
    type="application",
    homepage=PLUGIN_HOMEPAGE,
    supported_adapters={"~onebot.v11"},
    extra={
        "version": PLUGIN_EXTRA_VERSION,
        "menu_template": PLUGIN_MENU_TEMPLATE,
        "command_permissions": [
            {
                "id": "relogin.relogin",
                "label": "牛牛重新上号",
                "default": "bot_moderator",
            },
            {"id": "relogin.create", "label": "创建牛牛", "default": "superuser"},
        ],
        "command_limits": [
            {"id": "relogin.relogin", "cd_sec": 60},
            {"id": "relogin.create", "cd_sec": 120},
        ],
        "menu_data": [
            {
                "func": "重新上号",
                "trigger_method": "on_cmd",
                "trigger_scene": SCENE_PRIVATE,
                "trigger_condition": "牛牛重新上号 [QQ号]",
                "command_permission": "relogin.relogin",
                "brief_des": "重启账号并回传二维码（号主可用）",
                "detail_des": "自动重启协议端账号，等待二维码文件生成并在私聊推送。",
            },
            {
                "func": "创建牛牛",
                "trigger_method": "on_cmd",
                "trigger_scene": SCENE_PRIVATE,
                "trigger_condition": "创建牛牛 …",
                "command_permission": "relogin.create",
                "brief_des": "创建并启动新牛牛账号（仅超管）",
                "detail_des": "在协议端创建账号并启动，私聊回传登录二维码",
            },
        ],
        "knowledge_sources": [
            knowledge_source_row(
                source_id="relogin.faq",
                title="牛牛重新上号说明",
                description="协议端重启与登录二维码",
                chunks=[
                    {
                        "title": "重新上号",
                        "content": (
                            "私聊发送「牛牛重新上号 [QQ号]」可重启协议端账号并在私聊推送登录二维码；"
                            "号主或 bot 管理员可用。"
                        ),
                        "keywords": "重新上号,登录,二维码,掉线,重启",
                    },
                    {
                        "title": "创建牛牛",
                        "content": ("超管私聊「创建牛牛 …」可在协议端新建实例并回传二维码；属于维护者向操作。"),
                        "keywords": "创建牛牛,新建,超管,实例",
                    },
                    {
                        "title": "与协议端管理页的分工",
                        "content": (
                            "Bot 控制台「协议连接」（/pallas/protocol）供维护者图形化管理；"
                            "普通用户掉线优先用「牛牛重新上号」口令。"
                        ),
                        "keywords": "协议端,控制台,管理页,分工",
                    },
                ],
            ),
        ],
    },
)

relogin_cmd = on_command(
    "牛牛重新上号",
    priority=5,
    block=True,
    permission=private_message_permission_for_command("relogin.relogin"),
)
create_cmd = on_command(
    "创建牛牛",
    priority=5,
    block=True,
    permission=private_message_permission_for_command("relogin.create"),
)

_CANCEL_WORDS = {"取消", "cancel", "退出", "quit"}


async def _bot_id_exists_in_db(bot_id: int) -> bool:
    try:
        repo = make_bot_config_repository()
        return await repo.get(bot_id) is not None
    except Exception:
        return False


def _extract_qq(arg: str) -> str:
    text = (arg or "").strip()
    return text if text.isdigit() else ""


@relogin_cmd.handle()
async def _relogin_handle(event: MessageEvent, state: T_State, args: Message = CommandArg()):
    if not isinstance(event, PrivateMessageEvent):
        await relogin_cmd.finish("请私聊使用该命令。")

    qq = _extract_qq(args.extract_plain_text())
    if qq:
        state["qq"] = Message(qq)
    else:
        await relogin_cmd.send("请回复要重新上号的QQ号：")


@relogin_cmd.got("qq")
async def _relogin_got_qq(bot: Bot, event: MessageEvent, state: T_State, qq_input: str = ArgPlainText("qq")):
    if qq_input.strip() in _CANCEL_WORDS:
        await relogin_cmd.finish("已取消重新上号。")

    qq = _extract_qq(qq_input)
    if not qq:
        await relogin_cmd.reject("QQ号格式不正确，请重新输入：")

    # 检查用户是否是目标 bot 的管理员
    is_target_admin = await user_is_bot_admin(int(qq), int(event.get_user_id()))
    if not (is_target_admin or await SUPERUSER(bot, event)):
        await relogin_cmd.finish(f"你不是 {qq} 的管理员，无法执行重新上号。")

    state["_qq"] = qq

    if protocol_manager.has_account(qq):
        state["_needs_create"] = False
        state["nickname"] = Message("__skip__")
    else:
        if not await _bot_id_exists_in_db(int(qq)):
            await relogin_cmd.finish(f"数据库中不存在账号为：{qq} 的牛牛")
        state["_needs_create"] = True
        await relogin_cmd.send("该账号协议端不存在，请输入牛牛昵称以自动创建：")


@relogin_cmd.got("nickname")
async def _relogin_got_nickname(
    bot: Bot,
    event: MessageEvent,
    state: T_State,
    nickname_input: str = ArgPlainText("nickname"),
):
    qq: str = state["_qq"]
    needs_create: bool = state.get("_needs_create", False)

    if needs_create:
        if nickname_input.strip() in _CANCEL_WORDS:
            await relogin_cmd.finish("已取消重新上号。")
        nickname = nickname_input.strip()
        if not nickname:
            await relogin_cmd.reject("昵称不能为空，请重新输入：")
        try:
            protocol_manager.create_account({"qq": qq, "display_name": nickname, "enabled": True})
            await reply_private_message(bot, event, f"已创建 {nickname} 并继续上号流程。")
        except Exception as e:
            await relogin_cmd.finish(f"自动创建协议端失败：{e}")

    account = protocol_manager.get_account(qq) or {}
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip())
    if not account_data_dir:
        await relogin_cmd.finish("账号目录缺失，无法执行重新上号。")

    await reply_private_message(bot, event, "正在启动协议端...")
    started_at = datetime.now().astimezone()
    try:
        await protocol_manager.restart_account(qq)
    except Exception as e:
        await relogin_cmd.finish(f"启动失败：{e}")

    qr_path = await protocol_manager.wait_account_qrcode(qq, started_at)
    if qr_path is None:
        await relogin_cmd.finish("已完成启动，但在 60 秒内未检测到新的二维码文件，请寻找号主上报情况")

    try:
        qr_bytes = qr_path.read_bytes()
        await reply_private_message(bot, event, "启动完成，请使用下面二维码登录：")
        await reply_private_message(bot, event, MessageSegment.image(qr_bytes))
    except OSError as e:
        await relogin_cmd.finish(f"二维码读取失败：{e}")


@create_cmd.handle()
async def _create_handle(event: MessageEvent, state: T_State, args: Message = CommandArg()):
    if not isinstance(event, PrivateMessageEvent):
        await create_cmd.finish("请私聊使用该命令。")

    text = args.extract_plain_text().strip()
    if text:
        parts = text.split()
        if len(parts) < 3:
            await create_cmd.finish("参数不足，需要：牛牛昵称 牛牛账号 号主账号（至少一个）")
        display_name, qq, *owner_qqs = parts
        if not qq.isdigit() or len(qq) < 5:
            await create_cmd.finish("牛牛账号格式不正确")
        invalid = [oq for oq in owner_qqs if not oq.isdigit()]
        if invalid:
            await create_cmd.finish(f"号主账号格式不正确：{'、'.join(invalid)}")
        state["display_name"] = Message(display_name)
        state["qq"] = Message(qq)
        state["owners"] = Message(" ".join(owner_qqs))
        state["_interactive"] = False
        return

    state["_interactive"] = True
    await create_cmd.send("请输入牛牛昵称：")


@create_cmd.got("display_name")
async def _create_got_name(state: T_State, display_name_input: str = ArgPlainText("display_name")):
    if display_name_input.strip() in _CANCEL_WORDS:
        await create_cmd.finish("已取消创建牛牛。")
    if not display_name_input.strip():
        await create_cmd.reject("昵称不能为空，请重新输入：")
    if state.get("_interactive"):
        await create_cmd.send("请输入牛牛QQ号：")


@create_cmd.got("qq")
async def _create_got_qq(state: T_State, qq_input: str = ArgPlainText("qq")):
    if qq_input.strip() in _CANCEL_WORDS:
        await create_cmd.finish("已取消创建牛牛。")
    qq = qq_input.strip()
    if not qq.isdigit() or len(qq) < 5:
        await create_cmd.reject("QQ号格式不正确，请重新输入：")
    if state.get("_interactive"):
        await create_cmd.send("请输入号主QQ号（如有多个用空格分隔）：")


@create_cmd.got("owners")
async def _create_got_owners(
    bot: Bot,
    event: MessageEvent,
    display_name_input: str = ArgPlainText("display_name"),
    qq_input: str = ArgPlainText("qq"),
    owners_input: str = ArgPlainText("owners"),
):
    if owners_input.strip() in _CANCEL_WORDS:
        await create_cmd.finish("已取消创建牛牛。")

    owner_qqs = owners_input.strip().split()
    if not owner_qqs:
        await create_cmd.reject("号主账号不能为空，请重新输入：")

    invalid = [oq for oq in owner_qqs if not oq.isdigit()]
    if invalid:
        await create_cmd.reject(f"号主账号格式不正确：{'、'.join(invalid)}，请重新输入：")

    display_name = display_name_input.strip()
    qq = qq_input.strip()

    try:
        protocol_manager.create_account({"qq": qq, "display_name": display_name, "enabled": True})
    except Exception as e:
        await create_cmd.finish(f"创建账号失败：{e}")

    account = protocol_manager.get_account(qq) or {}
    account_data_dir = Path(str(account.get("account_data_dir", "")).strip())
    if not account_data_dir:
        await create_cmd.finish("账号已创建，但账号目录缺失，无法启动。")

    await reply_private_message(bot, event, "正在启动协议端...")
    started_at = datetime.now().astimezone()
    try:
        await protocol_manager.start_account(qq)
    except Exception as e:
        await create_cmd.finish(f"账号已创建，但启动失败：{e}")

    owner_ids = [int(oq) for oq in owner_qqs]
    try:
        repo = make_bot_config_repository()
        await repo.upsert_field(int(qq), "admins", owner_ids)
        from pallas.api.config import invalidate_bot_admins_cache

        await invalidate_bot_admins_cache(int(qq))
    except Exception as e:
        await create_cmd.finish(f"账号已创建并启动，但写入号主失败：{e}")

    qr_path = await protocol_manager.wait_account_qrcode(qq, started_at)
    if qr_path is not None:
        try:
            qr_bytes = qr_path.read_bytes()
            await reply_private_message(bot, event, "请使用下面二维码完成登录：")
            await reply_private_message(bot, event, MessageSegment.image(qr_bytes))
        except OSError as e:
            await reply_private_message(bot, event, f"二维码读取失败：{e}")

    owners_str = "、".join(owner_qqs)
    timeout_hint = "\n但在 60 秒内未检测到新的二维码文件，请到协议端控制台查看或联系管理员。" if qr_path is None else ""
    await create_cmd.finish(f"{display_name}：{qq} 已创建并启动。\n号主：{owners_str}{timeout_hint}")
