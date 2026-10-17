"""Recuperação persistente local com embeddings determinísticos e sem downloads."""

import asyncio
import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

import chromadb
import numpy as np
from chromadb.api.types import Embeddable, EmbeddingFunction, Embeddings
from chromadb.config import Settings

from hivemind.memory.knowledge_graph import KnowledgeGraph


class LocalEmbedding(EmbeddingFunction[Embeddable]):
    """Distribui tokens em 256 posições normalizadas, sem downloads ou rede.

    Este embedding lexical simples permite demonstrações offline, não raciocínio
    semântico. Persistência e configuração devem ser controladas por quem opera;
    não há suporte para abrir diretórios Chroma de origem não confiável.
    """

    def __init__(self) -> None:
        """Usa somente o algoritmo fixo incluído no projeto."""

    def __call__(self, input: Embeddable) -> Embeddings:
        """Gera embeddings determinísticos, independentes da semente do Python."""
        vectors: Embeddings = []
        for text in input:
            if not isinstance(text, str):
                raise ValueError("Only text embeddings are supported")
            vector = [0.0] * 256
            for token, count in Counter(re.findall(r"\w+", text.casefold())).items():
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % 256] += count
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            else:
                vector[0] = 1.0
            vectors.append(np.asarray(vector, dtype=np.float32))
        return vectors

    @staticmethod
    def name() -> str:
        """Retorna a identidade estável do embedding salva na coleção."""
        return "hivemind-local-token-hash-v1"

    def get_config(self) -> dict[str, Any]:
        """Não expõe configuração executável ou fornecida por usuários."""
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "LocalEmbedding":
        """Reconstrói somente o algoritmo local fixo, nunca código externo."""
        if config:
            raise ValueError("Custom embedding configuration is unsupported")
        return LocalEmbedding()


class KnowledgeStore:
    """Gerencia uma coleção Chroma local em um caminho confiável.

    Não exponha ``path`` a entradas de requisição nem aponte para um banco não
    confiável. Esta classe não usa servidor HTTP Chroma, desativa telemetria e
    fornece somente sua função de embedding local e fixa.
    """

    def __init__(self, path: str) -> None:
        if not isinstance(path, str) or not path.strip() or "://" in path:
            raise ValueError("Memory path must be a local filesystem directory")
        self._lock = RLock()
        self._embedding = LocalEmbedding()
        self._client = chromadb.PersistentClient(
            path=str(Path(path).expanduser().resolve()),
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="hivemind_knowledge",
            embedding_function=self._embedding,
        )

    async def remember(self, task: str, answer: str) -> str:
        """Persiste uma tarefa e sua resposta fora da thread do ciclo de eventos."""
        if not task.strip() or not answer.strip():
            raise ValueError("Task and answer must be nonempty")
        identifier = uuid4().hex

        def write() -> str:
            with self._lock:
                self._collection.add(
                    ids=[identifier],
                    documents=[f"Task: {task}\nAnswer:\n{answer}"],
                )
            return identifier

        return await asyncio.to_thread(write)

    async def search(self, query: str, limit: int = 3) -> list[str]:
        """Busca documentos próximos sem baixar modelos de embedding."""
        if type(limit) is not int or limit < 1:
            raise ValueError("Search limit must be a positive integer")
        if not query.strip():
            raise ValueError("Search query must be nonempty")

        def read() -> list[str]:
            with self._lock:
                count = self._collection.count()
                if not count:
                    return []
                result = self._collection.query(
                    query_embeddings=self._embedding([query]),
                    n_results=min(limit, count),
                    include=["documents"],
                )
                documents = result["documents"]
                return list(documents[0]) if documents else []

        return await asyncio.to_thread(read)

    async def count(self) -> int:
        """Conta registros persistidos sem bloquear o ciclo de eventos."""

        def read_count() -> int:
            with self._lock:
                return self._collection.count()

        return await asyncio.to_thread(read_count)


__all__ = ["KnowledgeGraph", "KnowledgeStore"]
