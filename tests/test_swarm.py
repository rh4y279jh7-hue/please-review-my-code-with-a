"""Testes do grafo de feromônios, fila e ciclos offline da colônia."""

import json
from pathlib import Path

import pytest

from hivemind.agents.swarm import CuratorAgent, Discovery, SynthesizerAgent
from hivemind.feedback import QuestionQueue
from hivemind.memory import KnowledgeGraph


def test_graph_connects_reinforces_evaporates_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "grafo.json"
    graph = KnowledgeGraph(path)
    first, created, links = graph.add_fragment(
        "Conhecimento coletivo organiza evidências"
    )
    assert created and links == 0
    second, created, links = graph.add_fragment(
        "Evidências conectam conhecimento compartilhado"
    )
    assert created and links == 0
    edge = next(iter(graph.edges))
    assert graph.edges[edge] == 1.0
    assert graph.reinforce(first, second, amount=0.5) == 1.5
    assert graph.reinforce_node(first) == 1
    assert graph.edges[edge] == pytest.approx(1.7)

    reopened = KnowledgeGraph(path)
    assert reopened.nodes == graph.nodes
    assert reopened.edges == graph.edges
    assert reopened.evaporate(rate=0.5, minimum=0.4) == 0
    assert reopened.edges[edge] == pytest.approx(0.85)
    assert KnowledgeGraph(path).edges[edge] == pytest.approx(0.85)
    assert reopened.evaporate(rate=0.9, minimum=0.2) == 1
    assert reopened.edges == {}
    assert reopened.nodes == {}


def test_graph_rejects_invalid_data_and_prunes_isolated_nodes(tmp_path: Path) -> None:
    graph = KnowledgeGraph(tmp_path / "grafo.json")
    identifier, created, _ = graph.add_fragment("Fragmento sem conexão")
    assert created
    assert not graph.add_fragment("Fragmento sem conexão")[1]
    assert graph.evaporate() == 0
    assert identifier not in graph.nodes
    with pytest.raises(ValueError, match="vazio"):
        graph.add_fragment(" ")
    with pytest.raises(ValueError, match="caracteres"):
        graph.add_fragment("x" * 4_001)
    with pytest.raises(ValueError, match="evaporação"):
        graph.evaporate(rate=1.5)
    with pytest.raises(ValueError, match="inválido"):
        (tmp_path / "invalido.json").write_text("{}", encoding="utf-8")
        KnowledgeGraph(tmp_path / "invalido.json")


def test_synthesizer_detects_patterns_and_curator_weakens_low_quality(
    tmp_path: Path,
) -> None:
    graph = KnowledgeGraph(tmp_path / "grafo.json")
    synthesizer = SynthesizerAgent()
    curator = CuratorAgent()
    queue = QuestionQueue()
    first = Discovery(
        "Pergunta inicial?",
        "Memória coletiva registra evidências úteis.",
        "Nova pergunta?",
    )
    first_synthesis = synthesizer.synthesize(first, graph)
    assert first_synthesis.gaps
    assert curator.curate(first, first_synthesis.identifier, 0, graph, queue) == (
        0,
        True,
    )

    second = Discovery(
        "Pergunta seguinte?",
        "Memória coletiva organiza evidências úteis.",
        "Outra pergunta de continuação?",
    )
    second_synthesis = synthesizer.synthesize(second, graph)
    assert second_synthesis.patterns == (first.content,)
    assert not second_synthesis.gaps
    curator.curate(second, second_synthesis.identifier, 0, graph, queue)
    weak = Discovery("Ruído?", "x", "Pergunta ignorada?")
    curator.curate(weak, second_synthesis.identifier, 0, graph, queue)
    assert graph.connections(second_synthesis.identifier)[0][1] == pytest.approx(1.0)


def test_question_queue_deduplicates_and_enforces_limits() -> None:
    queue = QuestionQueue(max_size=2, max_depth=1)
    assert queue.enqueue("Como analisar a memória coletiva?")
    assert not queue.enqueue("como analisar a memoria coletiva!")
    assert queue.enqueue("Quais evidências sustentam a aprendizagem?")
    first = queue.dequeue()
    assert first is not None and first.depth == 0
    assert first.question == "Como analisar a memória coletiva?"
    assert not queue.enqueue("Quais evidências sustentam aprendizagem?")
    assert queue.enqueue("Uma terceira pergunta diferente?")
    assert not queue.enqueue("Limite da fila atingido?")
    assert queue.dequeue() is not None
    assert queue.dequeue() is not None
    assert not queue.enqueue("Propagação além do limite?", depth=2)
    assert queue.dequeue() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_size": 0},
        {"max_size": True},
        {"max_depth": -1},
        {"max_depth": True},
        {"similarity_threshold": 0},
        {"similarity_threshold": float("nan")},
    ],
)
def test_question_queue_rejects_invalid_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        QuestionQueue(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_swarm_cycles_grow_the_shared_graph_with_bounded_feedback(
    tmp_path: Path,
) -> None:
    from hivemind.core.swarm import SwarmOrchestrator

    graph = KnowledgeGraph(tmp_path / "grafo.json")
    result = await SwarmOrchestrator(graph).run(
        "Como um formigueiro digital compartilha conhecimento?", cycles=5
    )
    assert len(result["cycles"]) == 5
    assert all(cycle["nodes_created"] == 1 for cycle in result["cycles"])
    assert result["metrics"] == {
        "nodes_created": 5,
        "trails_reinforced": 10,
        "tasks_generated": 6,
        "tasks_consumed": 5,
        "queue_pending": 1,
    }
    assert len(KnowledgeGraph(tmp_path / "grafo.json").nodes) == 5


def test_cli_runs_offline_and_prints_metrics(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from hivemind.__main__ import main

    graph_path = tmp_path / "cli.json"
    monkeypatch.setenv("HIVEMIND_GRAPH_PATH", str(graph_path))
    main(["run", "--cycles", "2"])
    output = capsys.readouterr().out
    assert "Geração 1:" in output
    assert "Geração 2:" in output
    assert "tarefas consumidas=1" in output
    assert json.loads(graph_path.read_text(encoding="utf-8"))["nodes"]
