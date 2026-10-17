"""Bounded, single-process task service with authenticated event streaming."""

import asyncio
import hmac
import logging
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from starlette.websockets import WebSocketDisconnect

from hivemind.agents import build_agents
from hivemind.config import Settings
from hivemind.core import Orchestrator
from hivemind.logging import configure_logging
from hivemind.memory import KnowledgeStore

logger = logging.getLogger("hivemind.api")
bearer = HTTPBearer(auto_error=False)
TERMINAL = {"completed", "failed", "cancelled"}


class TaskRequest(BaseModel):
    """A bounded task, optionally overriding the default feedback cycle count."""

    task: str = Field(min_length=1, max_length=16000)
    cycles: int | None = Field(default=None, ge=1, le=10)

    @field_validator("task")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """Reject whitespace-only tasks before scheduling any work."""
        if not value.strip():
            raise ValueError("A tarefa não pode estar vazia")
        return value.strip()


class TaskResponse(BaseModel):
    """Current task state; provider exception details are never returned."""

    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class Job:
    """A bounded event journal and live subscribers for one task."""

    view: TaskResponse
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    worker: asyncio.Task[None] | None = None

    async def publish(self, event: dict[str, Any]) -> None:
        """Publish without blocking model execution on slow consumers."""
        self.events.append(event)
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


def create_app(
    settings: Settings | None = None, orchestrator: Orchestrator | None = None
) -> FastAPI:
    """Build an app; injectable dependencies permit offline integration tests."""
    config = settings if settings is not None else Settings()
    jobs: dict[str, Job] = {}

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        engine = orchestrator
        if engine is None:
            agents = build_agents(config)
            try:
                memory = KnowledgeStore(config.memory_path)
                engine = Orchestrator(
                    agents,
                    memory,
                    max_cycles=config.max_cycles,
                    timeout_seconds=config.agent_timeout_seconds,
                    memory_top_k=config.memory_top_k,
                )
            except Exception:
                await asyncio.gather(
                    *(agent.close() for agent in agents), return_exceptions=True
                )
                raise
        application.state.orchestrator = engine
        logger.info("service_started")
        try:
            yield
        finally:
            pending = [
                job.worker
                for job in jobs.values()
                if job.worker is not None and not job.worker.done()
            ]
            for worker in pending:
                worker.cancel()
            if pending:
                await asyncio.wait(pending, timeout=config.shutdown_timeout_seconds)
            await engine.close()
            logger.info("service_stopped")

    application = FastAPI(
        title="HiveMind",
        version="0.1.0",
        description="Orquestração multiagente com memória coletiva local.",
        lifespan=lifespan,
    )

    def authorized(value: str | None) -> bool:
        token = config.api_token
        return token is None or (
            value is not None
            and hmac.compare_digest(
                value.encode("utf-8"), token.get_secret_value().encode("utf-8")
            )
        )

    async def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if not authorized(credentials.credentials if credentials else None):
            raise HTTPException(
                401, "Autenticação necessária", headers={"WWW-Authenticate": "Bearer"}
            )

    def find_job(task_id: str) -> Job:
        if task_id not in jobs:
            raise HTTPException(404, "Tarefa não encontrada ou expirada")
        return jobs[task_id]

    async def execute(job: Job, payload: TaskRequest, engine: Orchestrator) -> None:
        job.view.status = "running"
        try:
            job.view.result = await engine.run(
                payload.task, cycles=payload.cycles, on_event=job.publish
            )
            job.view.status = "completed"
        except asyncio.CancelledError:
            job.view.status = "cancelled"
            raise
        except Exception:
            job.view.status = "failed"
            job.view.error = "Não foi possível concluir a tarefa"
            logger.warning("task_failed")
        finally:
            await job.publish({"type": "task_finished", "task": job.view.model_dump()})

    @application.get("/health")
    async def health() -> dict[str, str]:
        """Report process liveness without disclosing configuration."""
        return {"status": "ok"}

    @application.post("/tasks", status_code=202, dependencies=[Depends(authenticate)])
    async def submit(payload: TaskRequest, request: Request) -> TaskResponse:
        """Accept a task unless the bounded execution capacity is exhausted."""
        if payload.cycles is not None and payload.cycles > config.max_cycles:
            raise HTTPException(422, "Número de ciclos excede o limite configurado")
        active = sum(job.view.status not in TERMINAL for job in jobs.values())
        if active >= config.max_concurrent_tasks:
            raise HTTPException(
                429, "Capacidade esgotada", headers={"Retry-After": "5"}
            )
        if len(jobs) >= config.max_tasks:
            expired = next(
                (key for key, job in jobs.items() if job.view.status in TERMINAL), None
            )
            if expired is None:
                raise HTTPException(429, "Capacidade esgotada")
            del jobs[expired]
        job = Job(TaskResponse(id=str(uuid4()), status="queued"))
        jobs[job.view.id] = job
        job.worker = asyncio.create_task(
            execute(job, payload, request.app.state.orchestrator)
        )
        return job.view.model_copy(deep=True)

    @application.get("/tasks/{task_id}", dependencies=[Depends(authenticate)])
    async def status(task_id: str) -> TaskResponse:
        """Read task status until it is evicted or the process restarts."""
        return find_job(task_id).view

    @application.get("/metrics", dependencies=[Depends(authenticate)])
    async def metrics(request: Request) -> dict[str, Any]:
        """Expose growth counters, not a claim of measured model intelligence."""
        engine = request.app.state.orchestrator
        return {
            **engine.feedback.snapshot(),
            "knowledge_entries": await engine.memory.count(),
            "retained_tasks": len(jobs),
            "active_tasks": sum(
                job.view.status not in TERMINAL for job in jobs.values()
            ),
        }

    @application.websocket("/ws/tasks/{task_id}")
    async def stream(websocket: WebSocket, task_id: str) -> None:
        """Replay retained events and stream new agent responses, not token deltas."""
        header = websocket.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if not authorized(value if scheme.lower() == "bearer" else None):
            await websocket.close(code=1008)
            return
        # Browser cookies and query-string secrets are deliberately not accepted.
        if websocket.headers.get("origin"):
            await websocket.close(code=1008)
            return
        job = jobs.get(task_id)
        if job is None:
            await websocket.close(code=1008)
            return
        if len(job.subscribers) >= 10:
            await websocket.close(code=1013)
            return
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        history = list(job.events)
        job.subscribers.add(queue)
        try:
            await websocket.accept()
            for event in history:
                await asyncio.wait_for(websocket.send_json(event), timeout=15)
            if any(event.get("type") == "task_finished" for event in history):
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    event = {"type": "heartbeat"}
                await asyncio.wait_for(websocket.send_json(event), timeout=15)
                if event.get("type") == "task_finished":
                    break
        except (WebSocketDisconnect, TimeoutError, OSError):
            pass
        finally:
            job.subscribers.discard(queue)
            try:
                await websocket.close()
            except (RuntimeError, OSError):
                pass

    return application
