"""Bounded graph, lifecycle, failure isolation, and metrics contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from hivemind.agents import BaseAgent
from hivemind.agents.limits import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_ENTRIES,
    MAX_CONTEXT_ENTRY_CHARS,
    MAX_OUTPUT_CHARS,
)
from hivemind.core import Orchestrator
from hivemind.feedback import Feedback
from hivemind.memory import KnowledgeStore


class StubAgent(BaseAgent):
    """Capture context and simulate successful, failed, or slow providers."""

    def __init__(
        self,
        name: str,
        *,
        delay: float = 0,
        fail: bool = False,
        answer: str | None = None,
    ) -> None:
        self.name = name
        self.delay = delay
        self.fail = fail
        self.answer = answer if answer is not None else f"{name} answer"
        self.contexts: list[list[str]] = []
        self.closed = False
        self.cancelled = False

    async def generate(self, task: str, context: list[str]) -> str:
        self.contexts.append(list(context))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail:
            raise RuntimeError("secret provider credentials")
        return self.answer

    async def close(self) -> None:
        self.closed = True


def fake_memory() -> KnowledgeStore:
    """Use the store's async contract without touching a database."""
    memory = AsyncMock(spec=KnowledgeStore)
    memory.search.return_value = ["prior knowledge"]
    memory.remember.side_effect = ["id-1", "id-2", "id-3", "id-4"]
    memory.count.return_value = 2
    return cast(KnowledgeStore, memory)


@pytest.mark.asyncio
async def test_bounded_feedback_loop_and_memory_reuse() -> None:
    agents = [StubAgent("first"), StubAgent("second")]
    memory = fake_memory()
    engine = Orchestrator(list(agents), memory)
    events: list[dict[str, Any]] = []

    async def event_callback(event: dict[str, Any]) -> None:
        events.append(event)

    result = await engine.run("task", on_event=event_callback)
    assert result["cycles"] == 2
    assert result["memory_ids"] == ["id-1", "id-2"]
    assert result["failures"] == []
    assert list(result["responses"]) == ["first", "second"]
    assert "not a claim of consensus" in result["answer"]
    assert "[first]\nfirst answer" in result["answer"]
    assert agents[0].contexts[0] == [
        "[UNVERIFIED_MEMORY: verify before relying on claims]\nprior knowledge"
    ]
    assert "Previous cycle" in agents[0].contexts[1][-1]
    assert result["answer"] in agents[0].contexts[1][-1]
    assert [event["type"] for event in events] == [
        "started",
        "cycle_started",
        "agent_response",
        "agent_response",
        "cycle_completed",
        "cycle_started",
        "agent_response",
        "agent_response",
        "cycle_completed",
        "completed",
    ]
    assert engine.feedback.snapshot() == {
        "completed_tasks": 1,
        "failed_tasks": 0,
        "cycles": 2,
        "agent_failures": 0,
        "knowledge_entries": 2,
    }
    assert engine.memory is memory
    with pytest.raises(AttributeError):
        engine.memory = fake_memory()  # type: ignore[misc]
    await engine.run("task again", cycles=1)
    assert agents[0].contexts[-1] == [
        "[UNVERIFIED_MEMORY: verify before relying on claims]\nprior knowledge"
    ]
    await engine.close()
    await engine.close()
    assert all(agent.closed for agent in agents)
    with pytest.raises(RuntimeError, match="closed"):
        await engine.run("task")


@pytest.mark.asyncio
async def test_unverified_memory_cannot_silently_become_independent_evidence() -> None:
    memory = fake_memory()
    agent = StubAgent(
        "unverified_agent",
        answer="A strong-looking claim without independent corroboration.",
    )
    engine = Orchestrator([agent], memory, max_cycles=2)
    await engine.run("Investigate the claim", cycles=2)

    assert len(agent.contexts) == 2
    assert agent.contexts[0] == [
        "[UNVERIFIED_MEMORY: verify before relying on claims]\nprior knowledge"
    ]
    assert "UNVERIFIED_AGENT_OUTPUT" in agent.contexts[1][-1]
    assert all(
        call.args[1].startswith("[UNVERIFIED_AGENT_OUTPUT: not independent evidence]\n")
        for call in memory.remember.await_args_list
    )


@pytest.mark.asyncio
async def test_timeouts_partial_failures_and_completion_order() -> None:
    slow = StubAgent("slow", delay=1)
    broken = StubAgent("broken", fail=True)
    fast = StubAgent("fast")
    engine = Orchestrator(
        [slow, broken, fast],
        fake_memory(),
        max_cycles=1,
        timeout_seconds=0.02,
    )
    events: list[dict[str, Any]] = []

    async def callback(event: dict[str, Any]) -> None:
        events.append(event)
        if event["type"] == "agent_response":
            assert slow.cancelled is False

    result = await engine.run("task", on_event=callback)
    assert result["responses"] == {"fast": "fast answer"}
    assert result["failures"] == ["slow", "broken"]
    assert "secret" not in str(result)
    assert slow.cancelled
    assert engine.feedback.snapshot()["agent_failures"] == 2


@pytest.mark.asyncio
async def test_total_failure_does_not_learn() -> None:
    memory = fake_memory()
    engine = Orchestrator([StubAgent("bad", fail=True)], memory, max_cycles=1)
    with pytest.raises(RuntimeError, match="All agents failed") as caught:
        await engine.run("task")
    assert "secret" not in str(caught.value)
    cast(AsyncMock, memory.remember).assert_not_awaited()
    assert engine.feedback.snapshot()["failed_tasks"] == 1


@pytest.mark.asyncio
async def test_empty_answers_are_failures() -> None:
    engine = Orchestrator(
        [StubAgent("empty", answer="  ")], fake_memory(), max_cycles=1
    )
    with pytest.raises(RuntimeError, match="All agents failed"):
        await engine.run("task")


@pytest.mark.asyncio
async def test_observer_errors_do_not_poison_work() -> None:
    engine = Orchestrator([StubAgent("ok")], fake_memory(), max_cycles=1)

    async def disconnected(event: dict[str, Any]) -> None:
        raise ConnectionError("client disconnected")

    assert (await engine.run("task", on_event=disconnected))["cycles"] == 1


@pytest.mark.asyncio
async def test_observer_cancelled_error_does_not_cancel_work() -> None:
    engine = Orchestrator([StubAgent("ok")], fake_memory(), max_cycles=1)

    async def disconnected(event: dict[str, Any]) -> None:
        raise asyncio.CancelledError

    assert (await engine.run("task", on_event=disconnected))["cycles"] == 1
    assert engine.feedback.snapshot()["completed_tasks"] == 1


@pytest.mark.asyncio
async def test_cancelled_provider_is_an_isolated_failure() -> None:
    class CancelledAgent(StubAgent):
        async def generate(self, task: str, context: list[str]) -> str:
            raise asyncio.CancelledError

    engine = Orchestrator(
        [CancelledAgent("cancelled"), StubAgent("ok")],
        fake_memory(),
        max_cycles=1,
    )
    result = await engine.run("task")
    assert result["failures"] == ["cancelled"]
    assert result["responses"] == {"ok": "ok answer"}
    assert engine.feedback.snapshot()["agent_failures"] == 1


@pytest.mark.asyncio
async def test_persistence_failure_is_explicit_and_sanitized() -> None:
    memory = fake_memory()
    cast(AsyncMock, memory.remember).side_effect = OSError("secret filesystem path")
    engine = Orchestrator([StubAgent("ok")], memory, max_cycles=1)
    with pytest.raises(RuntimeError, match="Knowledge persistence failed") as caught:
        await engine.run("task")
    assert "secret" not in str(caught.value)
    assert engine.feedback.snapshot()["completed_tasks"] == 0


@pytest.mark.asyncio
async def test_retrieval_failure_is_explicit_and_sanitized() -> None:
    memory = fake_memory()
    cast(AsyncMock, memory.search).side_effect = OSError("secret filesystem path")
    agent = StubAgent("ok")
    engine = Orchestrator([agent], memory, max_cycles=1)
    with pytest.raises(RuntimeError, match="Knowledge retrieval failed"):
        await engine.run("task")
    assert agent.contexts == []


@pytest.mark.asyncio
async def test_cancellation_cleans_up_workers() -> None:
    agent = StubAgent("slow", delay=10)
    memory = fake_memory()
    engine = Orchestrator([agent], memory, max_cycles=1)
    task = asyncio.create_task(engine.run("task"))
    for _ in range(100):
        if agent.contexts:
            break
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.cancelled
    cast(AsyncMock, memory.remember).assert_not_awaited()
    assert engine.feedback.snapshot()["failed_tasks"] == 1


@pytest.mark.asyncio
async def test_queued_task_cancellation_is_counted() -> None:
    agent = StubAgent("slow", delay=10)
    engine = Orchestrator([agent], fake_memory(), max_cycles=1)
    active = asyncio.create_task(engine.run("active"))
    for _ in range(100):
        if agent.contexts:
            break
        await asyncio.sleep(0.001)
    queued = asyncio.create_task(engine.run("queued"))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert engine.feedback.snapshot()["failed_tasks"] == 1
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert engine.feedback.snapshot()["failed_tasks"] == 2


@pytest.mark.asyncio
async def test_cancellation_during_final_event_is_counted_once() -> None:
    engine = Orchestrator([StubAgent("ok")], fake_memory(), max_cycles=1)
    final_event = asyncio.Event()

    async def callback(event: dict[str, Any]) -> None:
        if event["type"] == "completed":
            final_event.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(engine.run("task", on_event=callback))
    await asyncio.wait_for(final_event.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    metrics = engine.feedback.snapshot()
    assert metrics["failed_tasks"] == 1
    assert metrics["completed_tasks"] == 0
    assert metrics["cycles"] == 1


@pytest.mark.asyncio
async def test_close_attempts_every_provider_and_sanitizes_errors() -> None:
    class BrokenCloseAgent(StubAgent):
        async def close(self) -> None:
            raise OSError("private provider connection")

    healthy = StubAgent("ok")
    engine = Orchestrator(
        [BrokenCloseAgent("bad"), healthy], fake_memory(), max_cycles=1
    )
    with pytest.raises(RuntimeError, match="could not be closed") as caught:
        await engine.close()
    assert healthy.closed
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_ten_cycles_cannot_recursively_expand_reference_data() -> None:
    class EchoAgent(StubAgent):
        async def generate(self, task: str, context: list[str]) -> str:
            self.contexts.append(list(context))
            return task + "\n".join(context)

    memory = fake_memory()
    cast(AsyncMock, memory.search).return_value = ["large memory" * 4000] * 100
    cast(AsyncMock, memory.remember).side_effect = None
    cast(AsyncMock, memory.remember).return_value = "saved"
    agent = EchoAgent("echo")
    engine = Orchestrator([agent], memory, max_cycles=10, memory_top_k=100)
    result = await engine.run("task")
    assert result["cycles"] == 10
    assert len(result["responses"]["echo"]) <= MAX_OUTPUT_CHARS
    assert len(result["answer"]) <= MAX_OUTPUT_CHARS + 100
    assert len(agent.contexts) == 10
    for context in agent.contexts:
        assert len(context) <= MAX_CONTEXT_ENTRIES
        assert all(len(entry) <= MAX_CONTEXT_ENTRY_CHARS for entry in context)
        assert sum(map(len, context)) <= MAX_CONTEXT_CHARS
    assert all("Previous cycle" in context[-1] for context in agent.contexts[1:])
    assert all(
        call.kwargs["limit"] <= MAX_CONTEXT_ENTRIES
        for call in cast(AsyncMock, memory.search).await_args_list
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_cycles": 0},
        {"max_cycles": 11},
        {"max_cycles": True},
        {"memory_top_k": 0},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"timeout_seconds": "30"},
        {"timeout_seconds": None},
    ],
)
def test_invalid_constructor_limits(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Orchestrator([StubAgent("ok")], fake_memory(), **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("cycles", [0, -1, 3, True, 1.5])
async def test_invalid_cycle_requests(cycles: Any) -> None:
    engine = Orchestrator([StubAgent("ok")], fake_memory())
    with pytest.raises(ValueError):
        await engine.run("task", cycles=cycles)


def test_invalid_agent_lists() -> None:
    for agents in ([], [StubAgent("")], [StubAgent("same"), StubAgent("same")]):
        with pytest.raises(ValueError):
            Orchestrator(list(agents), fake_memory())


def test_feedback_thread_safety_and_snapshot_isolation() -> None:
    feedback = Feedback()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: feedback.increment(completed_tasks=1), range(1000)))
    snapshot: dict[str, int | float] = feedback.snapshot()
    assert snapshot["completed_tasks"] == 1000
    snapshot["completed_tasks"] = 0
    assert feedback.snapshot()["completed_tasks"] == 1000
    with pytest.raises(ValueError):
        feedback.increment(completed_tasks=-1)
