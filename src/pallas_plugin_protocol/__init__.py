import logging

from nonebot import get_app, get_driver, logger
from nonebot.plugin import PluginMetadata
from pallas.api.logging import register_plugin_startup_ready
from pallas.api.metadata import (
    PLUGIN_EXTRA_VERSION,
    PLUGIN_HOMEPAGE,
    PLUGIN_MENU_TEMPLATE,
    join_usage,
    usage_line,
)
from pallas.api.paths import plugin_data_dir
from pallas.console.webui.console_login import prime_shared_console_login

from .config import (
    Config as Config,
)
from .config import (
    get_pallas_protocol_config,
    plugin_config,
)
from .service import PallasProtocolService
from .web import register_pallas_protocol_routes

__plugin_meta__ = PluginMetadata(
    name="协议端管理",
    description="NapCat/SnowLuma 协议端账号管理与 Web 控制台。",
    usage=join_usage(
        usage_line("/pallas/protocol", "协议连接（Bot 控制台）"),
        usage_line("/protocol/console", "旧书签（自动跳转）"),
        usage_line("X-Pallas-Protocol-Token / ?token=", "API 鉴权"),
    ),
    type="application",
    homepage=PLUGIN_HOMEPAGE,
    supported_adapters={"~onebot.v11"},
    extra={
        "help_tag": "admin",
        "version": PLUGIN_EXTRA_VERSION,
        "menu_template": PLUGIN_MENU_TEMPLATE,
        "menu_data": [
            {
                "func": "协议端管理页",
                "trigger_method": "http",
                "help_audience": "superuser",
                "trigger_condition": "/pallas/protocol",
                "brief_des": "管理协议账号与进程",
                "detail_des": "可在页面执行创建账号、启动、停止、重启与日志查看。",
            },
            {
                "func": "协议端 API",
                "trigger_method": "http",
                "help_audience": "superuser",
                "trigger_condition": "/protocol/*",
                "brief_des": "提供协议管理接口",
                "detail_des": "提供账号、配置、协议端发行包下载与状态查询接口。",
            },
        ],
    },
)

app = get_app()
driver = get_driver()
manager = PallasProtocolService(plugin_data_dir("pallas_protocol"), get_pallas_protocol_config())

register_pallas_protocol_routes(app, manager=manager, plugin_config=plugin_config)


@driver.on_startup
async def _pallas_protocol_prime_shared_console_login() -> None:
    prime_shared_console_login()


@driver.on_startup
async def _startup() -> None:
    if not plugin_config.pallas_protocol_enabled:
        return
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    await manager.initialize()
    from pallas.api.platform import is_sharded_worker

    if not is_sharded_worker():
        manager.schedule_snowluma_stale_cleanup()
    register_plugin_startup_ready("protocol", detail="协议端已就绪")
    profile = manager.runtime_profile()
    if bool(profile.get("follow_bot_lifecycle", True)):
        await manager.start_all_enabled_accounts()


@driver.on_shutdown
async def _shutdown() -> None:
    if not plugin_config.pallas_protocol_enabled:
        return
    manager.cancel_snowluma_stale_cleanup()
    profile = manager.runtime_profile()
    if not bool(profile.get("follow_bot_lifecycle", True)):
        return
    await manager.stop_all_enabled_accounts()
