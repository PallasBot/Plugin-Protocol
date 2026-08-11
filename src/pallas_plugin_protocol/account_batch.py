"""批量账号操作编排：rolling restart、并发上限与 job 进度。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nonebot import logger
from pallas.api.logging import format_plugin_event

AccountActionFn = Callable[[str], Awaitable[object | None]]


class BatchAction(StrEnum):
    RESTART = "restart"
    START = "start"
    STOP = "stop"


class BatchMode(StrEnum):
    ROLLING = "rolling"
    PARALLEL = "parallel"


@dataclass
class BatchItemResult:
    account_id: str
    ok: bool
    error: str = ""


@dataclass
class BatchJobState:
    job_id: str
    action: str
    mode: str
    account_ids: list[str]
    phase: str = "pending"
    total: int = 0
    completed: int = 0
    current_account_id: str | None = None
    results: list[BatchItemResult] = field(default_factory=list)
    status: str = "running"
    max_concurrency: int = 2
    stagger_ms: int = 3000
    started_at: str = ""
    finished_at: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k != "results"},
            "results": [asdict(r) for r in self.results],
        }

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def progress_percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.completed * 100 / self.total))


class AccountBatchCoordinator:
    MAX_JOBS = 32

    def __init__(self) -> None:
        self._jobs: dict[str, BatchJobState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}
        self._lock = asyncio.Lock()

    def get_job(self, job_id: str) -> BatchJobState | None:
        return self._jobs.get(job_id)

    def job_to_dict(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        return job.to_dict() if job else None

    def _prune_old_jobs(self) -> None:
        if len(self._jobs) <= self.MAX_JOBS:
            return
        finished = [(jid, job) for jid, job in self._jobs.items() if job.status != "running"]
        finished.sort(key=lambda x: x[1].finished_at or x[1].started_at)
        for jid, _ in finished[: max(0, len(self._jobs) - self.MAX_JOBS + 1)]:
            self._jobs.pop(jid, None)
            self._tasks.pop(jid, None)
            self._subscribers.pop(jid, None)

    def _emit(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        payload = f"event: progress\ndata: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n"
        for queue in list(self._subscribers.get(job_id, [])):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def subscribe_sse(self, job_id: str) -> AsyncIterator[str]:
        job = self.get_job(job_id)
        if job is None:
            yield (f"event: error\ndata: {json.dumps({'detail': 'job not found'}, ensure_ascii=False)}\n\n")
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._subscribers.setdefault(job_id, []).append(queue)
        try:
            yield (f"event: snapshot\ndata: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n")
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                except TimeoutError:
                    current = self.get_job(job_id)
                    if current is None or current.status != "running":
                        if current:
                            yield (f"event: snapshot\ndata: {json.dumps(current.to_dict(), ensure_ascii=False)}\n\n")
                        break
                    yield ": keepalive\n\n"
                    continue
                yield msg
                current = self.get_job(job_id)
                if current is None or current.status != "running":
                    if current:
                        yield (f"event: snapshot\ndata: {json.dumps(current.to_dict(), ensure_ascii=False)}\n\n")
                    break
        finally:
            subs = self._subscribers.get(job_id, [])
            if queue in subs:
                subs.remove(queue)

    async def start_job(
        self,
        action: BatchAction,
        account_ids: list[str],
        *,
        stop_fn: AccountActionFn,
        start_fn: AccountActionFn,
        restart_fn: AccountActionFn,
        max_concurrency: int = 2,
        stagger_ms: int = 3000,
        mode: BatchMode = BatchMode.ROLLING,
    ) -> str:
        ids = [str(i).strip() for i in account_ids if str(i).strip()]
        if not ids:
            raise ValueError("account_ids 为空")
        max_concurrency = max(1, min(int(max_concurrency), 8))
        stagger_ms = max(0, min(int(stagger_ms), 120_000))
        job_id = uuid.uuid4().hex
        job = BatchJobState(
            job_id=job_id,
            action=action.value,
            mode=mode.value,
            account_ids=ids,
            total=len(ids),
            max_concurrency=max_concurrency,
            stagger_ms=stagger_ms,
            started_at=datetime.now(UTC).isoformat(),
            phase="running",
        )
        async with self._lock:
            self._prune_old_jobs()
            self._jobs[job_id] = job
        task = asyncio.create_task(
            self._run_job(
                job_id,
                action,
                ids,
                stop_fn=stop_fn,
                start_fn=start_fn,
                restart_fn=restart_fn,
                max_concurrency=max_concurrency,
                stagger_ms=stagger_ms,
                mode=mode,
            )
        )
        self._tasks[job_id] = task
        return job_id

    async def _run_one(
        self,
        job_id: str,
        account_id: str,
        fn: AccountActionFn,
        *,
        phase: str,
        count_progress: bool = True,
    ) -> BatchItemResult:
        job = self._jobs.get(job_id)
        if job:
            job.current_account_id = account_id
            job.phase = phase
            self._emit(job_id)
        try:
            await fn(account_id)
            result = BatchItemResult(account_id=account_id, ok=True)
        except KeyError as e:
            result = BatchItemResult(account_id=account_id, ok=False, error=str(e) or "账号不存在")
        except ValueError as e:
            result = BatchItemResult(account_id=account_id, ok=False, error=str(e))
        except Exception as e:
            result = BatchItemResult(
                account_id=account_id,
                ok=False,
                error=f"{type(e).__name__}: {e}",
            )
        job = self._jobs.get(job_id)
        if job:
            if count_progress:
                job.results.append(result)
                job.completed += 1
            job.current_account_id = None
            self._emit(job_id)
        return result

    async def _run_parallel(
        self,
        job_id: str,
        account_ids: list[str],
        fn: AccountActionFn,
        *,
        phase: str,
        max_concurrency: int,
    ) -> None:
        sem = asyncio.Semaphore(max_concurrency)

        async def one(aid: str) -> None:
            async with sem:
                await self._run_one(job_id, aid, fn, phase=phase)

        await asyncio.gather(*(one(aid) for aid in account_ids))

    async def _run_rolling_restart(
        self,
        job_id: str,
        account_ids: list[str],
        *,
        stop_fn: AccountActionFn,
        start_fn: AccountActionFn,
        stagger_ms: int,
    ) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.phase = "stopping"
            job.message = "正在停止实例…"
            self._emit(job_id)
        for aid in account_ids:
            await self._run_one(job_id, aid, stop_fn, phase="stopping", count_progress=False)
        job = self._jobs.get(job_id)
        if job:
            job.phase = "starting"
            job.message = "正在按间隔启动实例…"
            self._emit(job_id)
        for index, aid in enumerate(account_ids):
            if index > 0 and stagger_ms > 0:
                await asyncio.sleep(stagger_ms / 1000.0)
            await self._run_one(job_id, aid, start_fn, phase="starting")

    async def _run_rolling_linear(
        self,
        job_id: str,
        account_ids: list[str],
        fn: AccountActionFn,
        *,
        phase: str,
        stagger_ms: int,
    ) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.phase = phase
            self._emit(job_id)
        for index, aid in enumerate(account_ids):
            if index > 0 and stagger_ms > 0:
                await asyncio.sleep(stagger_ms / 1000.0)
            await self._run_one(job_id, aid, fn, phase=phase)

    async def _run_job(
        self,
        job_id: str,
        action: BatchAction,
        account_ids: list[str],
        *,
        stop_fn: AccountActionFn,
        start_fn: AccountActionFn,
        restart_fn: AccountActionFn,
        max_concurrency: int,
        stagger_ms: int,
        mode: BatchMode,
    ) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        try:
            if action is BatchAction.RESTART:
                if mode is BatchMode.ROLLING:
                    await self._run_rolling_restart(
                        job_id,
                        account_ids,
                        stop_fn=stop_fn,
                        start_fn=start_fn,
                        stagger_ms=stagger_ms,
                    )
                else:
                    await self._run_parallel(
                        job_id,
                        account_ids,
                        restart_fn,
                        phase="restarting",
                        max_concurrency=max_concurrency,
                    )
            elif action is BatchAction.START:
                if mode is BatchMode.ROLLING:
                    await self._run_rolling_linear(
                        job_id,
                        account_ids,
                        start_fn,
                        phase="starting",
                        stagger_ms=stagger_ms,
                    )
                else:
                    await self._run_parallel(
                        job_id,
                        account_ids,
                        start_fn,
                        phase="starting",
                        max_concurrency=max_concurrency,
                    )
            elif action is BatchAction.STOP:
                if mode is BatchMode.ROLLING:
                    await self._run_rolling_linear(
                        job_id,
                        account_ids,
                        stop_fn,
                        phase="stopping",
                        stagger_ms=0,
                    )
                else:
                    await self._run_parallel(
                        job_id,
                        account_ids,
                        stop_fn,
                        phase="stopping",
                        max_concurrency=max_concurrency,
                    )
            job = self._jobs.get(job_id)
            if job:
                failed = job.failed_count
                job.status = "completed"
                job.phase = "completed"
                job.finished_at = datetime.now(UTC).isoformat()
                job.message = f"完成 {job.total - failed}/{job.total}" + (f"，{failed} 个失败" if failed else "")
                self._emit(job_id)
                logger.info(
                    format_plugin_event(
                        "batch_accounts",
                        f"Batch {action.value} for {len(account_ids)} account(s) completed (failed={failed})",
                    )
                )
        except asyncio.CancelledError:
            job = self._jobs.get(job_id)
            if job:
                job.status = "cancelled"
                job.phase = "cancelled"
                job.finished_at = datetime.now(UTC).isoformat()
                job.message = "已取消"
                self._emit(job_id)
            raise
        except Exception as e:
            job = self._jobs.get(job_id)
            if job:
                job.status = "failed"
                job.phase = "failed"
                job.finished_at = datetime.now(UTC).isoformat()
                job.message = f"{type(e).__name__}: {e}"
                self._emit(job_id)
                logger.warning(
                    format_plugin_event(
                        "batch_accounts",
                        f"Batch {action.value} for {len(account_ids)} account(s) failed ({type(e).__name__}: {e})",
                    )
                )
