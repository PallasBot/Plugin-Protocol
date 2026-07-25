"""Docker 镜像拉取 / SnowLuma 派生重建任务与 SSE 进度。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_EMIT_MIN_INTERVAL_S = 0.2
_IDLE_HEARTBEAT_S = 8.0


@dataclass
class DockerPullJobState:
    job_id: str
    protocol: str
    image: str
    phase: str = "pending"
    status: str = "running"
    message: str = ""
    output: str = ""
    rebuild_image: str | None = None
    rebuild_ok: bool | None = None
    progress_percent: int = 0
    code: int | None = None
    started_at: str = ""
    finished_at: str | None = None
    lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("lines", None)
        return data


class DockerPullCoordinator:
    MAX_JOBS = 16
    MAX_OUTPUT_CHARS = 12000
    MAX_LINES = 400

    def __init__(self) -> None:
        self._jobs: dict[str, DockerPullJobState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}
        self._last_emit_at: dict[str, float] = {}

    def get_job(self, job_id: str) -> DockerPullJobState | None:
        return self._jobs.get(job_id)

    def find_running_job(self, *, image: str, protocol: str) -> str | None:
        """同镜像同协议只跑一个拉取任务，避免并发 docker pull 互相卡住。"""
        want_img = str(image or "").strip()
        want_proto = str(protocol or "").strip().lower()
        for jid, job in self._jobs.items():
            if job.status != "running":
                continue
            if str(job.image).strip() == want_img and str(job.protocol).strip().lower() == want_proto:
                return jid
        return None

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

    def emit(self, job_id: str) -> None:
        self._emit(job_id)

    def append_line(
        self,
        job: DockerPullJobState,
        line: str,
        *,
        percent: int | None = None,
        force_emit: bool = False,
    ) -> None:
        text = line.rstrip("\r\n")
        if not text:
            return
        prev_pct = job.progress_percent
        # docker pull 常用 \r 刷新同一行：相同内容不重复塞进日志
        if job.lines and job.lines[-1] == text:
            if percent is not None:
                job.progress_percent = max(prev_pct, max(0, min(100, percent)))
            job.message = text[-240:]
            self._emit_throttled(job.job_id, force=force_emit or job.progress_percent != prev_pct)
            return
        job.lines.append(text)
        if len(job.lines) > self.MAX_LINES:
            job.lines = job.lines[-self.MAX_LINES :]
        job.output = "\n".join(job.lines)[-self.MAX_OUTPUT_CHARS :]
        job.message = text[-240:]
        if percent is not None:
            job.progress_percent = max(0, min(100, percent))
        self._emit_throttled(
            job.job_id,
            force=force_emit or job.progress_percent != prev_pct or job.status != "running",
        )

    def _emit_throttled(self, job_id: str, *, force: bool = False) -> None:
        now = time.monotonic()
        last = self._last_emit_at.get(job_id, 0.0)
        if not force and (now - last) < _EMIT_MIN_INTERVAL_S:
            return
        self._last_emit_at[job_id] = now
        self._emit(job_id)

    async def subscribe_sse(self, job_id: str) -> AsyncIterator[str]:
        job = self.get_job(job_id)
        if job is None:
            yield f"event: error\ndata: {json.dumps({'detail': 'job not found'}, ensure_ascii=False)}\n\n"
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=128)
        self._subscribers.setdefault(job_id, []).append(queue)
        try:
            yield f"event: snapshot\ndata: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n"
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
        *,
        image: str,
        protocol: str,
        run_fn: Callable[[DockerPullJobState], Awaitable[None]],
    ) -> str:
        self._prune_old_jobs()
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        job = DockerPullJobState(
            job_id=job_id,
            protocol=protocol,
            image=image,
            phase="pending",
            status="running",
            message="排队中…",
            started_at=now,
            progress_percent=0,
        )
        self._jobs[job_id] = job
        self._emit(job_id)

        async def _runner() -> None:
            try:
                await run_fn(job)
            except Exception as exc:
                job.status = "failed"
                job.phase = "failed"
                job.code = -1
                job.message = f"拉取任务异常：{exc}"
                self.append_line(job, job.message, percent=100, force_emit=True)
                job.finished_at = datetime.now(UTC).isoformat()
                self._emit(job_id)
            finally:
                self._tasks.pop(job_id, None)

        self._tasks[job_id] = asyncio.create_task(_runner(), name=f"docker-pull:{job_id}")
        return job_id


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass


async def stream_subprocess_lines(
    *argv: str,
    stdin_text: str | None = None,
    on_line: Callable[[str], None],
    on_idle: Callable[[], None] | None = None,
    idle_s: float = _IDLE_HEARTBEAT_S,
) -> int:
    """按行回调；按 ``\\n`` / ``\\r`` 切分。独立进程组便于取消时杀掉 docker 子进程。"""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert proc.stdout is not None
    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    buf = b""
    idle = max(1.0, float(idle_s))

    def flush_pieces() -> None:
        nonlocal buf
        while True:
            n_idx = buf.find(b"\n")
            r_idx = buf.find(b"\r")
            if n_idx < 0 and r_idx < 0:
                break
            if n_idx >= 0 and (r_idx < 0 or n_idx <= r_idx):
                piece, buf = buf[:n_idx], buf[n_idx + 1 :]
            else:
                piece, buf = buf[:r_idx], buf[r_idx + 1 :]
                if buf.startswith(b"\n"):
                    buf = buf[1:]
            on_line(piece.decode("utf-8", errors="replace"))

    try:
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=idle)
            except TimeoutError:
                if proc.returncode is not None:
                    break
                if on_idle is not None:
                    on_idle()
                continue
            if not chunk:
                break
            buf += chunk
            flush_pieces()
        if buf:
            on_line(buf.decode("utf-8", errors="replace"))
            buf = b""
        return await proc.wait()
    except asyncio.CancelledError:
        if proc.returncode is None and proc.pid:
            _kill_process_group(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                if proc.pid:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
        raise


def estimate_pull_percent(line: str, fallback: int) -> int:
    text = line.strip()
    lower = text.lower()
    pct_match = _PERCENT_RE.search(text)
    if pct_match:
        try:
            parsed = int(float(pct_match.group(1)))
        except ValueError:
            parsed = fallback
        else:
            # docker 层内 0–100% 映射到总进度 15–88
            mapped = 15 + int(parsed * 0.73)
            return max(fallback, min(88, mapped))
    # 单层 Download complete 很常见，不能直接跳到 70%
    if "download complete" in lower:
        return max(fallback, min(fallback + 3, 55))
    if "pull complete" in lower:
        return max(fallback, min(fallback + 8, 72))
    if "extracting" in lower:
        return max(fallback, 75)
    if "pulling fs layer" in lower:
        return max(fallback, 15)
    if "downloading" in lower:
        return max(fallback, 25)
    if "verifying" in lower or "checksum" in lower:
        return max(fallback, 85)
    if "status: downloaded newer image" in lower or "status: image is up to date" in lower:
        return max(fallback, 90)
    return fallback
