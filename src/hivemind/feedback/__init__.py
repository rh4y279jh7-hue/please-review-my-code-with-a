"""Métricas locais do mecanismo, protegidas para uso entre threads."""

import math
import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class OpenQuestion:
    """Uma pergunta aberta e a profundidade em que ela foi descoberta."""

    question: str
    depth: int


class QuestionQueue:
    """Fila local de perguntas com limites e deduplicação aproximada."""

    def __init__(
        self,
        *,
        max_size: int = 100,
        max_depth: int = 5,
        similarity_threshold: float = 0.8,
    ) -> None:
        """Define limites explícitos para impedir propagação sem controle."""
        if type(max_size) is not int or not 1 <= max_size <= 10_000:
            raise ValueError("max_size deve ser um inteiro entre 1 e 10000")
        if type(max_depth) is not int or not 0 <= max_depth <= 100:
            raise ValueError("max_depth deve ser um inteiro entre 0 e 100")
        if (
            isinstance(similarity_threshold, bool)
            or not isinstance(similarity_threshold, (int, float))
            or not math.isfinite(similarity_threshold)
            or not 0 < similarity_threshold <= 1
        ):
            raise ValueError("O limite de similaridade deve estar entre zero e um")
        self._max_size = max_size
        self._max_depth = max_depth
        self._threshold = float(similarity_threshold)
        self._pending: deque[OpenQuestion] = deque()
        self._known: deque[frozenset[str]] = deque(maxlen=max_size * 10)

    def enqueue(self, question: str, depth: int = 0) -> bool:
        """Adiciona uma pergunta nova, se ainda houver espaço e profundidade."""
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError("A pergunta deve conter de 1 a 500 caracteres")
        if type(depth) is not int or depth < 0:
            raise ValueError("A profundidade deve ser um inteiro não negativo")
        if depth > self._max_depth or len(self._pending) >= self._max_size:
            return False
        terms = self._terms(question)
        if not terms or any(self._similar(terms, known) for known in self._known):
            return False
        self._pending.append(OpenQuestion(question.strip(), depth))
        self._known.append(terms)
        return True

    def dequeue(self) -> OpenQuestion | None:
        """Retira a pergunta mais antiga ou retorna vazio quando a fila se esgota."""
        return self._pending.popleft() if self._pending else None

    def __len__(self) -> int:
        """Retorna a quantidade de perguntas aguardando consumo."""
        return len(self._pending)

    @staticmethod
    def _terms(question: str) -> frozenset[str]:
        """Normaliza acentos, caixa e pontuação para comparar perguntas."""
        normalized = unicodedata.normalize("NFKD", question.casefold())
        plain = "".join(char for char in normalized if not unicodedata.combining(char))
        return frozenset(re.findall(r"\w+", plain))

    def _similar(self, first: frozenset[str], second: frozenset[str]) -> bool:
        """Compara conjuntos de palavras com índice de Jaccard."""
        union = first | second
        return bool(union) and len(first & second) / len(union) >= self._threshold


class Feedback:
    """Mantém contadores seguros entre threads e ciclos de eventos."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._metrics = dict.fromkeys(
            (
                "completed_tasks",
                "failed_tasks",
                "cycles",
                "agent_failures",
                "knowledge_entries",
            ),
            0,
        )

    def increment(self, **counts: int) -> None:
        """Aumenta contadores conhecidos por quantidades inteiras não negativas."""
        if any(
            key not in self._metrics or type(value) is not int or value < 0
            for key, value in counts.items()
        ):
            raise ValueError("Invalid feedback counter update")
        with self._lock:
            for key, value in counts.items():
                self._metrics[key] += value

    def set_knowledge_entries(self, count: int) -> None:
        """Atualiza a quantidade persistida de conhecimentos, sem incrementá-la."""
        if type(count) is not int or count < 0:
            raise ValueError("Knowledge count must be a nonnegative integer")
        with self._lock:
            self._metrics["knowledge_entries"] = count

    def snapshot(self) -> dict[str, int | float]:
        """Retorna uma cópia consistente das métricas, serializável em JSON."""
        with self._lock:
            return {key: value for key, value in self._metrics.items()}


__all__ = ["Feedback", "OpenQuestion", "QuestionQueue"]
