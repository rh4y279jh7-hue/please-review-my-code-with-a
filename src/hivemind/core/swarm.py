"""Ciclos limitados de uma colônia de agentes que compartilha conhecimento."""

from typing import Any

from hivemind.agents.swarm import (
    CuratorAgent,
    ExplorerAgent,
    LocalSearchProvider,
    SynthesizerAgent,
)
from hivemind.feedback import QuestionQueue
from hivemind.memory import KnowledgeGraph


class SwarmOrchestrator:
    """Executa gerações offline e mede o crescimento controlado do formigueiro."""

    def __init__(self, graph: KnowledgeGraph, *, queue_limit: int = 100) -> None:
        """Monta os papéis locais mantendo a fila e o grafo compartilhados."""
        self._graph = graph
        self._explorer = ExplorerAgent(LocalSearchProvider())
        self._synthesizer = SynthesizerAgent()
        self._curator = CuratorAgent()
        self._queue_limit = queue_limit

    async def run(self, task: str, cycles: int) -> dict[str, Any]:
        """Processa até o número pedido de gerações sem chamadas externas."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A pergunta inicial não pode estar vazia")
        if type(cycles) is not int or not 1 <= cycles <= 100:
            raise ValueError("O número de ciclos deve estar entre 1 e 100")
        queue = QuestionQueue(max_size=self._queue_limit, max_depth=cycles)
        generated = int(queue.enqueue(task.strip()))
        consumed = 0
        nodes_created = 0
        trails_reinforced = 0
        history: list[dict[str, int]] = []
        previous = (generated, consumed, nodes_created, trails_reinforced)

        for _ in range(cycles):
            open_question = queue.dequeue()
            if open_question is not None:
                consumed += 1
                discovery = await self._explorer.explore(open_question)
                synthesis = self._synthesizer.synthesize(discovery, self._graph)
                nodes_created += int(synthesis.created)
                reinforced, spawned = self._curator.curate(
                    discovery,
                    synthesis.identifier,
                    open_question.depth,
                    self._graph,
                    queue,
                )
                trails_reinforced += reinforced
                generated += int(spawned)
            self._graph.evaporate(prune_isolated=False)
            current = (generated, consumed, nodes_created, trails_reinforced)
            history.append(
                {
                    "nodes_created": current[2] - previous[2],
                    "trails_reinforced": current[3] - previous[3],
                    "tasks_generated": current[0] - previous[0],
                    "tasks_consumed": current[1] - previous[1],
                }
            )
            previous = current

        return {
            "cycles": history,
            "metrics": {
                "nodes_created": nodes_created,
                "trails_reinforced": trails_reinforced,
                "tasks_generated": generated,
                "tasks_consumed": consumed,
                "queue_pending": len(queue),
            },
        }


__all__ = ["SwarmOrchestrator"]
