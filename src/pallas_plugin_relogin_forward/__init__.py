"""分片 worker：识别私聊 relogin 口令并转发 hub 执行。"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageSegment, PrivateMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from pallas.api.metadata import (
    PLUGIN_EXTRA_VERSION,
    PLUGIN_HOMEPAGE,
    PLUGIN_MENU_TEMPLATE,
    SCENE_PRIVATE,
    join_usage,
    usage_line,
)
from pallas.api.perm import satisfies_command_permission
from pallas.api.platform import is_sharded_worker
from pallas.api.utils import reply_private_message
from pallas.core.platform.shard.coord.relogin_worker_forward import (
    forward_relogin_to_hub,
)

if TYPE_CHECKING:
    from pallas.core.platform.shard.coord.relogin_payload import (
        ReloginHandleResult,
        ReplyItem,
    )

__plugin_meta__ = PluginMetadata(
    name="牛牛重新上号转发",
    description="分片 worker 私聊识别 relogin 口令并转发 hub。",
    usage=join_usage(
        usage_line("牛牛重新上号 [QQ]", "worker 识别后转发 hub"),
        usage_line("创建牛牛 …", "同上"),
    ),
    type="application",
    homepage=PLUGIN_HOMEPAGE,
    supported_adapters={"~onebot.v11"},
    extra={
        "version": PLUGIN_EXTRA_VERSION,
        "menu_template": PLUGIN_MENU_TEMPLATE,
        "help_audience": "superuser",
        "menu_data": [
            {
                "func": "relogin 分片转发",
                "trigger_method": "on_message",
                "trigger_scene": SCENE_PRIVATE,
                "trigger_condition": "牛牛重新上号 / 创建牛牛（worker 私聊）",
                "brief_des": "转发 hub 执行协议端重登",
                "detail_des": "分片模式下 worker 不加载 relogin_bot，由本插件识别口令并经 HTTP 交 hub 处理。",
            },
        ],
    },
)

_active_sessions: set[tuple[str, str]] = set()


def relogin_forward_active() -> bool:
    return is_sharded_worker()


async def relogin_forward_rule(bot: Bot, event: PrivateMessageEvent) -> bool:
    if not relogin_forward_active():
        return False
    text = (event.get_plaintext() or "").strip()
    key = (str(bot.self_id), str(event.user_id))
    if key in _active_sessions:
        return True
    if text.startswith("牛牛重新上号"):
        return await satisfies_command_permission(bot, event, "relogin.relogin")
    if text.startswith("创建牛牛"):
        return await satisfies_command_permission(bot, event, "relogin.create")
    return False


async def apply_relogin_result(bot: Bot, event: PrivateMessageEvent, result: ReloginHandleResult) -> None:
    for item in result.replies:
        await send_reply_item(bot, event, item)
    if result.reject_hint:
        await reply_private_message(bot, event, result.reject_hint)


async def send_reply_item(bot: Bot, event: PrivateMessageEvent, item: ReplyItem) -> None:
    if item.kind == "text":
        await reply_private_message(bot, event, item.content)
        return
    if item.kind == "image_base64":
        try:
            data = base64.b64decode(item.content.encode("ascii"))
        except Exception:
            await reply_private_message(bot, event, "二维码数据解码失败。")
            return
        try:
            await reply_private_message(bot, event, MessageSegment.image(data))
        except Exception:
            # 群临时会话等场景发图可能失败；勿拖垮整次回复（前文案仍应送达）
            await reply_private_message(
                bot,
                event,
                "二维码图片发送失败。请先加好友后再私聊「牛牛重新上号」，或到协议端控制台扫码登录。",
            )


relogin_forward_matcher = on_message(
    rule=Rule(relogin_forward_rule),
    priority=4,
    block=True,
)


@relogin_forward_matcher.handle()
async def relogin_forward_handler(bot: Bot, event: PrivateMessageEvent):
    text = (event.get_plaintext() or "").strip()
    key = (str(bot.self_id), str(event.user_id))

    if key in _active_sessions:
        normalized = text.casefold()
        if normalized in {"是", "yes", "y"}:
            await reply_private_message(bot, event, "正在重置登录态")
        elif normalized in {"否", "no", "n"}:
            await reply_private_message(bot, event, "正在恢复登录")

    result = await forward_relogin_to_hub(
        bot_id=str(bot.self_id),
        user_id=str(event.user_id),
        text=text,
    )
    if result is None:
        await reply_private_message(bot, event, "转发 hub 执行重新上号失败，请稍后重试或联系管理员。")
        _active_sessions.discard(key)
        return

    if key in _active_sessions and not result.replies and not result.reject_hint and not result.session_active:
        await reply_private_message(bot, event, "会话已过期，请重新发送「牛牛重新上号」或「创建牛牛」。")
        _active_sessions.discard(key)
        return

    await apply_relogin_result(bot, event, result)
    if result.session_active:
        _active_sessions.add(key)
    else:
        _active_sessions.discard(key)
