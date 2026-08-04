"""SnowLuma Runtime Docker 镜像批量切换任务与 SSE 状态。"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


@dataclass
class SnowLumaImageSwitchJobState:
    job_id: str
    image: str
    apply_mode: str
    status: str = "running"
    message: str = "排队中…"
    started_at: str = ""
    finished_at: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SnowLumaImageSwitchCoordinator:
    MAX_JOBS = 16

    def __init__(self) -> None:
        self._jobs: dict[str, SnowLumaImageSwitchJobState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}
        self._start_lock: asyncio.Lock | None = None
        self._start_lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_start_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._start_lock is None or self._start_lock_loop is not loop:
            if self._start_lock is not None and self._start_lock.locked():
                raise RuntimeError("镜像切换协调器不能跨事件循环复用运行中的任务")
            self._start_lock = asyncio.Lock()
            self._start_lock_loop = loop
        return self._start_lock

    def get_job(self, job_id: str) -> SnowLumaImageSwitchJobState | None:
        return self._jobs.get(job_id)

    def job_to_dict(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        return job.to_dict() if job else None

    def emit(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        event = f"event: progress\ndata: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n"
        for queue in list(self._subscribers.get(job_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def running_job_id(self) -> str | None:
        for job_id, job in self._jobs.items():
            if job.status == "running":
                return job_id
        return None

    async def start_job(
        self,
        *,
        image: str,
        apply_mode: str,
        run_fn: Callable[[SnowLumaImageSwitchJobState], Awaitable[None]],
    ) -> str:
        async with self._get_start_lock():
            if self.running_job_id() is not None:
                raise ValueError("已有 SnowLuma 镜像切换任务正在运行")
            finished = [job for job in self._jobs.values() if job.status != "running"]
            finished.sort(key=lambda item: item.finished_at or item.started_at)
            for job in finished[: max(0, len(self._jobs) - self.MAX_JOBS + 1)]:
                self._jobs.pop(job.job_id, None)
                self._tasks.pop(job.job_id, None)
                self._subscribers.pop(job.job_id, None)
            job_id = uuid.uuid4().hex[:12]
            job = SnowLumaImageSwitchJobState(
                job_id=job_id,
                image=image,
                apply_mode=apply_mode,
                started_at=datetime.now(UTC).isoformat(),
            )
            self._jobs[job_id] = job
            self.emit(job_id)

            async def runner() -> None:
                try:
                    await run_fn(job)
                except Exception as exc:
                    job.status = "failed"
                    job.message = f"镜像切换任务异常：{exc}"
                finally:
                    if job.finished_at is None:
                        job.finished_at = datetime.now(UTC).isoformat()
                    self.emit(job_id)
                    self._tasks.pop(job_id, None)

            self._tasks[job_id] = asyncio.create_task(runner(), name=f"snowluma-image-switch:{job_id}")
            return job_id

    async def subscribe_sse(self, job_id: str) -> AsyncIterator[str]:
        job = self.get_job(job_id)
        if job is None:
            yield 'event: error\ndata: {"detail": "job not found"}\n\n'
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=128)
        self._subscribers.setdefault(job_id, []).append(queue)
        try:
            yield f"event: snapshot\ndata: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n"
            while job.status == "running":
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    yield ": keepalive\n\n"
                job = self.get_job(job_id)
                if job is None:
                    return
            yield f"event: snapshot\ndata: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n"
        finally:
            subscribers = self._subscribers.get(job_id, [])
            if queue in subscribers:
                subscribers.remove(queue)
