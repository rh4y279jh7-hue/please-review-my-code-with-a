"""Orquestração limitada com LangGraph e contribuições assíncronas isoladas."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from hivemind.agents import BaseAgent, DemoAgent
from hivemind.agents.limits import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_ENTRIES,
    MAX_CONTEXT_ENTRY_CHARS,
    bounded_context,
    bounded_text,
)
from hivemind.feedback import Feedback
from hivemind.memory import KnowledgeStore

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class _State(TypedDict):
    run_id: str
    task: str
    target_cycles: int
    cycle: int
    context: list[str]
    answer: str
    responses: dict[str, str]
    failures: list[str]
    memory_ids: list[str]
    on_event: EventCallback | None


class Orchestrator:
    """Executa ciclos de busca, distribuição, síntese e aprendizado limitado.

    As execuções são serializadas para manter o ciclo de vida e a retroalimentação
    determinísticos, com no máximo dez ciclos. Eventos são enviados como melhor
    esforço; respostas de agentes e memórias recuperadas continuam não confiáveis.
    """

    def __init__(
        self,
        agents: list[BaseAgent],
        memory: KnowledgeStore,
        *,
        max_cycles: int = 2,
        timeout_seconds: float = 30,
        memory_top_k: int = 3,
        max_provider_calls: int = 0,
    ) -> None:
        if type(max_cycles) is not int or not 1 <= max_cycles <= 10:
            raise ValueError("max_cycles must be an integer between 1 and 10")
        if type(memory_top_k) is not int or memory_top_k < 1:
            raise ValueError("memory_top_k must be a positive integer")
        if type(max_provider_calls) is not int or not 0 <= max_provider_calls <= 10000:
            raise ValueError(
                "max_provider_calls must be an integer between 0 and 10000"
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        names = [agent.name for agent in agents]
        if (
            not agents
            or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError("Agents must have nonempty unique names")
        self._agents = list(agents)
        self._memory = memory
        self._max_cycles = max_cycles
        self._timeout = timeout_seconds
        self._memory_top_k = memory_top_k
        self._max_provider_calls = max_provider_calls
        self._provider_calls = 0
        self._budget_lock = Lock()
        self._run_lock = asyncio.Lock()
        self._closed = False
        self.feedback = Feedback()
        graph = StateGraph(_State)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("dispatch", self._dispatch)
        graph.add_node("aggregate", self._aggregate)
        graph.add_node("learn", self._learn)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "dispatch")
        graph.add_edge("dispatch", "aggregate")
        graph.add_edge("aggregate", "learn")
        graph.add_conditional_edges(
            "learn",
            lambda state: (
                "retrieve" if state["cycle"] < state["target_cycles"] else END
            ),
            {"retrieve": "retrieve", END: END},
        )
        self._graph = graph.compile()

    @property
    def memory(self) -> KnowledgeStore:
        """Expõe o contrato assíncrono da memória para verificações do serviço."""
        return self._memory

    async def _emit(self, state: _State, event: dict[str, Any]) -> None:
        callback = state["on_event"]
        if callback is not None:
            try:
                await callback(event)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
            except Exception:
                # Um observador desconectado não pode invalidar o trabalho útil.
                pass

    async def _retrieve(self, state: _State) -> dict[str, Any]:
        cycle = state["cycle"] + 1
        await self._emit(state, {"type": "cycle_started", "cycle": cycle})
        previous = (
            bounded_text(
                f"Previous cycle contribution summary:\n{state['answer']}",
                MAX_CONTEXT_ENTRY_CHARS,
            )
            if state["answer"]
            else ""
        )
        max_entries = MAX_CONTEXT_ENTRIES - int(bool(previous))
        try:
            context = list(
                await self._memory.search(
                    state["task"], limit=min(self._memory_top_k, max_entries)
                )
            )
        except Exception:
            raise RuntimeError("Knowledge retrieval failed") from None
        context = bounded_context(
            context,
            max_entries=max_entries,
            max_chars=MAX_CONTEXT_CHARS - len(previous),
        )
        if previous:
            context.append(previous)
        return {"cycle": cycle, "context": context}

    async def _dispatch(self, state: _State) -> dict[str, Any]:
        # Reserva o ciclo inteiro antes de iniciar qualquer chamada. Falhas e
        # cancelamentos consomem a reserva; ela não estima nem limita reais gastos.
        required = sum(not isinstance(agent, DemoAgent) for agent in self._agents)
        with self._budget_lock:
            if self._provider_calls + required > self._max_provider_calls:
                raise RuntimeError("Provider call budget exhausted")
            self._provider_calls += required

        async def generate(agent: BaseAgent) -> tuple[str, str | None]:
            try:
                async with asyncio.timeout(self._timeout):
                    answer = await agent.generate(state["task"], list(state["context"]))
                if not isinstance(answer, str) or not answer.strip():
                    return agent.name, None
                return agent.name, bounded_text(answer.strip())
            except (Exception, asyncio.CancelledError):
                return agent.name, None

        pending = [asyncio.create_task(generate(agent)) for agent in self._agents]
        responses: dict[str, str] = {}
        failed: set[str] = set()
        try:
            for future in asyncio.as_completed(pending):
                name, answer = await future
                if answer is None:
                    failed.add(name)
                    self.feedback.increment(agent_failures=1)
                else:
                    responses[name] = answer
                    await self._emit(
                        state,
                        {
                            "type": "agent_response",
                            "cycle": state["cycle"],
                            "agent": name,
                            "response": answer,
                        },
                    )
        finally:
            for future in pending:
                if not future.done():
                    future.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if not responses:
            raise RuntimeError("All agents failed to produce an answer")
        failures = set(state["failures"]) | failed
        return {
            "responses": {
                agent.name: responses[agent.name]
                for agent in self._agents
                if agent.name in responses
            },
            "failures": [
                agent.name for agent in self._agents if agent.name in failures
            ],
        }

    async def _aggregate(self, state: _State) -> dict[str, str]:
        answer = "Attributed agent contributions (not a claim of consensus):\n\n"
        answer += "\n\n".join(
            f"[{name}]\n{response}" for name, response in state["responses"].items()
        )
        return {"answer": answer}

    async def _learn(self, state: _State) -> dict[str, list[str]]:
        contributors = [
            agent for agent in self._agents if agent.name in state["responses"]
        ]
        source_kind = (
            "demo_output"
            if all(isinstance(agent, DemoAgent) for agent in contributors)
            else "agent_output"
        )
        try:
            identifier = await self._memory.remember(
                state["task"],
                state["answer"],
                source_id=f"{state['run_id']}:cycle:{state['cycle']}",
                source_kind=source_kind,
                providers=tuple(state["responses"]),
            )
            count = await self._memory.count()
        except Exception:
            raise RuntimeError("Knowledge persistence failed") from None
        self.feedback.set_knowledge_entries(count)
        self.feedback.increment(cycles=1)
        await self._emit(
            state,
            {
                "type": "cycle_completed",
                "cycle": state["cycle"],
                "answer": state["answer"],
            },
        )
        return {"memory_ids": [*state["memory_ids"], identifier]}

    async def run(
        self,
        task: str,
        cycles: int | None = None,
        on_event: EventCallback | None = None,
    ) -> dict[str, Any]:
        """Retorna respostas finais, falhas acumuladas e identificadores salvos.

        Tentativas válidas são contadas uma única vez, inclusive cancelamentos
        enquanto aguardam outra execução. Entradas inválidas não são contabilizadas.
        """
        if not isinstance(task, str) or not task.strip():
            raise ValueError("Task must be a nonempty string")
        target = self._max_cycles if cycles is None else cycles
        if type(target) is not int or not 1 <= target <= self._max_cycles:
            raise ValueError("cycles must be between 1 and max_cycles")
        try:
            async with self._run_lock:
                if self._closed:
                    raise RuntimeError("Orchestrator is closed")
                state: _State = {
                    "run_id": uuid4().hex,
                    "task": task,
                    "target_cycles": target,
                    "cycle": 0,
                    "context": [],
                    "answer": "",
                    "responses": {},
                    "failures": [],
                    "memory_ids": [],
                    "on_event": on_event,
                }
                await self._emit(state, {"type": "started"})
                result = await self._graph.ainvoke(
                    state, config={"recursion_limit": target * 4 + 2}
                )
                final = {
                    "answer": result["answer"],
                    "responses": result["responses"],
                    "failures": result["failures"],
                    "cycles": result["cycle"],
                    "memory_ids": result["memory_ids"],
                }
                await self._emit(state, {"type": "completed", **final})
                self.feedback.increment(completed_tasks=1)
                return final
        except BaseException:
            self.feedback.increment(failed_tasks=1)
            raise

    async def close(self) -> None:
        """Aguarda uma execução e fecha todos os agentes, mesmo após uma falha."""
        async with self._run_lock:
            if self._closed:
                return
            self._closed = True
            results = await asyncio.gather(
                *(agent.close() for agent in self._agents), return_exceptions=True
            )
            if any(isinstance(result, BaseException) for result in results):
                raise RuntimeError("One or more agents could not be closed")


__all__ = ["Orchestrator"]
