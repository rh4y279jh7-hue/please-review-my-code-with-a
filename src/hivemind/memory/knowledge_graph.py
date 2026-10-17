"""Grafo local de conhecimento com feromônios digitais persistidos em JSON."""

import hashlib
import json
import math
import os
import re
from pathlib import Path
from threading import RLock

_STOP_WORDS = {
    "a",
    "as",
    "com",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "o",
    "os",
    "para",
    "por",
    "que",
    "um",
    "uma",
}
_MAX_NODES = 10_000
_MAX_EDGES = 50_000
_MAX_FRAGMENT_CHARS = 4_000


class KnowledgeGraph:
    """Armazena fragmentos e trilhas reforçáveis sem serviços externos."""

    def __init__(self, path: str | Path) -> None:
        """Carrega um arquivo JSON local controlado por quem executa o HiveMind."""
        self._path = Path(path).expanduser()
        self._lock = RLock()
        self._nodes: dict[str, str] = {}
        self._edges: dict[tuple[str, str], float] = {}
        if self._path.exists():
            self._load()

    @property
    def nodes(self) -> dict[str, str]:
        """Retorna uma cópia dos fragmentos, indexados por identificador estável."""
        with self._lock:
            return dict(self._nodes)

    @property
    def edges(self) -> dict[tuple[str, str], float]:
        """Retorna uma cópia das trilhas e de seus pesos atuais."""
        with self._lock:
            return dict(self._edges)

    def add_fragment(self, content: str) -> tuple[str, bool, int]:
        """Inclui um fragmento e conecta termos relevantes já conhecidos."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("O fragmento de conhecimento não pode estar vazio")
        content = content.strip()
        if len(content) > _MAX_FRAGMENT_CHARS:
            raise ValueError("O fragmento excede o limite de caracteres")
        identifier = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if identifier in self._nodes:
                return identifier, False, 0
            if len(self._nodes) >= _MAX_NODES:
                raise ValueError("O grafo atingiu o limite de fragmentos")
            terms = self._terms(content)
            self._nodes[identifier] = content
            reinforced = 0
            for other_id, other_content in tuple(self._nodes.items()):
                if other_id == identifier:
                    continue
                if terms.intersection(self._terms(other_content)):
                    if len(self._edges) >= _MAX_EDGES:
                        break
                    if self._link(identifier, other_id, 1.0):
                        reinforced += 1
            self._save()
            return identifier, True, reinforced

    def reinforce(self, source: str, target: str, amount: float = 1.0) -> float:
        """Aumenta o feromônio de uma trilha existente entre dois fragmentos."""
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise ValueError("O reforço deve ser um número positivo e finito")
        with self._lock:
            edge = self._edge_key(source, target)
            if edge not in self._edges:
                raise ValueError("A trilha informada não existe")
            self._edges[edge] += float(amount)
            self._save()
            return self._edges[edge]

    def reinforce_node(self, identifier: str, amount: float = 0.2) -> int:
        """Reforça todas as trilhas ligadas a um fragmento confirmado."""
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise ValueError("O reforço deve ser um número positivo e finito")
        with self._lock:
            if identifier not in self._nodes:
                raise ValueError("O fragmento informado não existe")
            connected = [edge for edge in self._edges if identifier in edge]
            for edge in connected:
                self._edges[edge] += amount
            if connected:
                self._save()
            return len(connected)

    def weaken_node(
        self, identifier: str, amount: float = 0.2, *, minimum: float = 0.05
    ) -> int:
        """Enfraquece trilhas de um fragmento e remove as que perderam relevância."""
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise ValueError("O enfraquecimento deve ser positivo e finito")
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or not math.isfinite(minimum)
            or minimum < 0
        ):
            raise ValueError("O limite mínimo deve ser finito e não negativo")
        with self._lock:
            if identifier not in self._nodes:
                raise ValueError("O fragmento informado não existe")
            connected = [edge for edge in self._edges if identifier in edge]
            for edge in connected:
                weight = self._edges[edge] - amount
                if weight <= minimum:
                    del self._edges[edge]
                else:
                    self._edges[edge] = weight
            if connected:
                self._save()
            return len(connected)

    def connections(self, identifier: str) -> list[tuple[str, float]]:
        """Lista fragmentos vizinhos e o feromônio das trilhas ligadas."""
        with self._lock:
            if identifier not in self._nodes:
                raise ValueError("O fragmento informado não existe")
            neighbors = [
                (self._nodes[next(node for node in edge if node != identifier)], weight)
                for edge, weight in self._edges.items()
                if identifier in edge
            ]
            return sorted(neighbors, key=lambda item: item[0])

    def evaporate(
        self, rate: float = 0.1, *, minimum: float = 0.05, prune_isolated: bool = True
    ) -> int:
        """Enfraquece trilhas antigas e opcionalmente remove nós sem conexões."""
        if (
            isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or not math.isfinite(rate)
            or not 0 <= rate <= 1
        ):
            raise ValueError("A taxa de evaporação deve estar entre zero e um")
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or not math.isfinite(minimum)
            or minimum < 0
        ):
            raise ValueError("O limite mínimo deve ser finito e não negativo")
        if type(prune_isolated) is not bool:
            raise ValueError("prune_isolated deve ser verdadeiro ou falso")
        with self._lock:
            evaporated = {
                edge: weight * (1 - rate) for edge, weight in self._edges.items()
            }
            had_edges = bool(self._edges)
            removed = {edge for edge, weight in evaporated.items() if weight <= minimum}
            self._edges = {
                edge: weight
                for edge, weight in evaporated.items()
                if edge not in removed
            }
            if prune_isolated:
                connected = {node for edge in self._edges for node in edge}
                self._nodes = {
                    identifier: content
                    for identifier, content in self._nodes.items()
                    if identifier in connected
                }
            if had_edges or prune_isolated:
                self._save()
            return len(removed)

    def _load(self) -> None:
        """Valida e restaura somente dados JSON simples do arquivo local."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            nodes = data["nodes"]
            edges = data["edges"]
            if not isinstance(nodes, dict) or not isinstance(edges, list):
                raise ValueError
            if len(nodes) > _MAX_NODES or len(edges) > _MAX_EDGES:
                raise ValueError
            restored_nodes = {
                identifier: content
                for identifier, content in nodes.items()
                if isinstance(identifier, str)
                and isinstance(content, str)
                and 0 < len(content) <= _MAX_FRAGMENT_CHARS
                and hashlib.sha256(content.encode("utf-8")).hexdigest() == identifier
            }
            if len(restored_nodes) != len(nodes):
                raise ValueError
            restored_edges: dict[tuple[str, str], float] = {}
            for item in edges:
                if not isinstance(item, dict):
                    raise ValueError
                source, target, weight = (
                    item["source"],
                    item["target"],
                    item["pheromone"],
                )
                if (
                    source not in restored_nodes
                    or target not in restored_nodes
                    or source == target
                    or isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not math.isfinite(weight)
                    or weight <= 0
                ):
                    raise ValueError
                restored_edges[self._edge_key(source, target)] = float(weight)
            self._nodes = restored_nodes
            self._edges = restored_edges
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("O arquivo local do grafo é inválido") from None

    def _save(self) -> None:
        """Grava em arquivo temporário e substitui o JSON sem persistência parcial."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        data = {
            "nodes": self._nodes,
            "edges": [
                {"source": source, "target": target, "pheromone": weight}
                for (source, target), weight in sorted(self._edges.items())
            ],
        }
        try:
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _terms(content: str) -> set[str]:
        """Extrai palavras úteis sem pontuação e ignora conectivos frequentes."""
        return {
            term
            for term in re.findall(r"\w+", content.casefold())
            if len(term) > 2 and term not in _STOP_WORDS
        }

    @staticmethod
    def _edge_key(source: str, target: str) -> tuple[str, str]:
        """Normaliza a direção para que cada trilha exista uma única vez."""
        if source == target:
            raise ValueError("Um fragmento não pode formar uma trilha consigo mesmo")
        return (source, target) if source < target else (target, source)

    def _link(self, source: str, target: str, amount: float) -> bool:
        """Cria uma trilha ou reforça a já existente; informa se ela existia."""
        edge = self._edge_key(source, target)
        existed = edge in self._edges
        self._edges[edge] = self._edges.get(edge, 0.0) + amount
        return existed


__all__ = ["KnowledgeGraph"]
