"""批量账号操作：解析 ID、默认参数与 job 启动（可独立于 service 单测）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .account_batch import AccountBatchCoordinator, BatchAction, BatchMode

if TYPE_CHECKING:
    from .config import Config
    from .service import PallasProtocolService


def resolve_batch_account_ids(
    accounts: dict[str, dict],
    raw_ids: list[str] | None,
) -> list[str]:
    if raw_ids:
        out: list[str] = []
        seen: set[str] = set()
        for item in raw_ids:
            aid = str(item or "").strip()
            if not aid or aid in seen:
                continue
            if aid not in accounts:
                raise KeyError(f"账号不存在: {aid}")
            seen.add(aid)
            out.append(aid)
        if not out:
            raise ValueError("account_ids 为空")
        return out
    enabled = [aid for aid, acc in accounts.items() if bool(acc.get("enabled", True))]
    if not enabled:
        raise ValueError("没有可操作的已启用账号")
    return enabled


def batch_defaults_from_config(config: Config) -> dict[str, int | float | str]:
    return {
        "max_concurrency": int(getattr(config, "pallas_protocol_restart_max_concurrency", 2)),
        "stagger_ms": int(float(getattr(config, "pallas_protocol_restart_stagger_s", 3.0)) * 1000),
        "mode": BatchMode.ROLLING.value,
    }


async def start_account_batch_job(
    service: PallasProtocolService,
    coordinator: AccountBatchCoordinator,
    action: str,
    account_ids: list[str] | None = None,
    *,
    mode: str | None = None,
    max_concurrency: int | None = None,
    stagger_ms: int | None = None,
) -> str:
    defaults = batch_defaults_from_config(service._config)
    resolved_ids = resolve_batch_account_ids(service._accounts, account_ids)
    try:
        batch_action = BatchAction(str(action or "").strip().lower())
    except ValueError as e:
        raise ValueError("action 须为 restart、start 或 stop") from e
    mode_raw = str(mode or defaults["mode"]).strip().lower()
    try:
        batch_mode = BatchMode(mode_raw)
    except ValueError as e:
        raise ValueError("mode 须为 rolling 或 parallel") from e
    mc = int(max_concurrency if max_concurrency is not None else defaults["max_concurrency"])
    sm = int(stagger_ms if stagger_ms is not None else defaults["stagger_ms"])
    return await coordinator.start_job(
        batch_action,
        resolved_ids,
        stop_fn=service.stop_account,
        start_fn=service.start_account,
        restart_fn=service.restart_account,
        max_concurrency=mc,
        stagger_ms=sm,
        mode=batch_mode,
    )


async def wait_batch_job(coordinator: AccountBatchCoordinator, job_id: str) -> None:
    task = coordinator._tasks.get(job_id)
    if task is not None:
        await task


def log_batch_job_failures(
    logger: Any,
    job_id: str,
    coordinator: AccountBatchCoordinator,
    *,
    prefix: str,
) -> None:
    job = coordinator.get_job(job_id)
    if not job:
        return
    for item in job.results:
        if item.ok:
            continue
        logger.warning("{} 账号 {} 失败：{}", prefix, item.account_id, item.error)
