"""Papéis offline para explorar, sintetizar e cuidar do conhecimento coletivo."""

import re
from dataclasses import dataclass
from typing import Protocol

from hivemind.feedback import OpenQuestion, QuestionQueue
from hivemind.memory import KnowledgeGraph

_QUESTION_FILLER = {
    "a",
    "as",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "etapa",
    "evidencias",
    "na",
    "no",
    "o",
    "os",
    "para",
    "sobre",
    "um",
    "uma",
    "verificar",
}


@dataclass(frozen=True)
class Discovery:
    """Fragmento encontrado e pergunta que pode orientar a próxima exploração."""

    question: str
    content: str
    follow_up: str


@dataclass(frozen=True)
class Synthesis:
    """Resultado da conexão de uma descoberta e do diagnóstico de suas lacunas."""

    identifier: str
    created: bool
    patterns: tuple[str, ...]
    gaps: tuple[str, ...]


class SearchProvider(Protocol):
    """Contrato plugável para provedores locais ou fontes externas confiáveis."""

    async def search(self, question: str) -> str:
        """Investiga uma pergunta e devolve um fragmento textual de conhecimento."""


class LocalSearchProvider:
    """Simula uma fonte de conhecimento sem rede, chaves ou dependências."""

    async def search(self, question: str) -> str:
        """Gera uma descoberta reproduzível e identificada como simulação local."""
        focus = question.strip().rstrip(" ?")
        return (
            f"Descoberta simulada sobre {focus}: observações organizadas em uma "
            "memória coletiva ajudam a comparar evidências, reconhecer padrões "
            "e registrar conhecimento útil para novas investigações."
        )


class ExplorerAgent:
    """Busca fragmentos usando uma interface de provedor substituível."""

    def __init__(self, provider: SearchProvider) -> None:
        """Recebe a fonte de dados sem assumir acesso a rede ou credenciais."""
        self._provider = provider

    async def explore(self, task: OpenQuestion) -> Discovery:
        """Produz uma descoberta e uma pergunta de continuação controlada."""
        content = await self._provider.search(task.question)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("O provedor não retornou uma descoberta válida")
        terms = re.findall(r"\w+", task.question.casefold())
        focus = next(
            (
                term
                for term in terms
                if term not in _QUESTION_FILLER and not term.isdigit()
            ),
            "conhecimento",
        )
        follow_up = (
            f"Como verificar evidências sobre {focus} na etapa {task.depth + 1}?"
        )
        return Discovery(task.question, content.strip(), follow_up)


class SynthesizerAgent:
    """Adiciona descobertas ao grafo e liga fragmentos que compartilham termos."""

    def synthesize(self, discovery: Discovery, graph: KnowledgeGraph) -> Synthesis:
        """Conecta a descoberta e aponta relações encontradas ou ainda ausentes."""
        identifier, created, _ = graph.add_fragment(discovery.content)
        connections = graph.connections(identifier)
        patterns = tuple(content for content, _ in connections)
        gaps = () if patterns else ("O fragmento ainda não possui relações.",)
        return Synthesis(identifier, created, patterns, gaps)


class CuratorAgent:
    """Avalia descobertas e decide se reforça trilhas ou cria novas tarefas."""

    def curate(
        self,
        discovery: Discovery,
        identifier: str,
        depth: int,
        graph: KnowledgeGraph,
        queue: QuestionQueue,
    ) -> tuple[int, bool]:
        """Reforça evidências úteis e propaga uma pergunta dentro dos limites."""
        quality = len(discovery.content.strip()) >= 40
        if quality:
            reinforced = graph.reinforce_node(identifier)
        else:
            reinforced = graph.weaken_node(identifier)
        spawned = queue.enqueue(discovery.follow_up, depth + 1) if quality else False
        return reinforced, spawned


__all__ = [
    "CuratorAgent",
    "Discovery",
    "ExplorerAgent",
    "LocalSearchProvider",
    "SearchProvider",
    "Synthesis",
    "SynthesizerAgent",
]
