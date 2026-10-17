"""Local-only persistent retrieval with deterministic, download-free embeddings."""

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


class LocalEmbedding(EmbeddingFunction[Embeddable]):
    """Hash tokens into 256 normalized bins; no model downloads or network calls.

    This small lexical embedding supports offline demos, not semantic reasoning.
    Persistence and embedding configuration must be trusted operator-controlled
    data; loading an untrusted Chroma directory is unsupported.
    """

    def __init__(self) -> None:
        """Use only the fixed built-in embedding algorithm."""

    def __call__(self, input: Embeddable) -> Embeddings:
        """Embed each input deterministically, independently of Python hash seeds."""
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
        """Return the stable embedding identity stored with the collection."""
        return "hivemind-local-token-hash-v1"

    def get_config(self) -> dict[str, Any]:
        """Expose no executable or user-supplied embedding configuration."""
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "LocalEmbedding":
        """Reconstruct only the fixed local algorithm, never external code."""
        if config:
            raise ValueError("Custom embedding configuration is unsupported")
        return LocalEmbedding()


class KnowledgeStore:
    """Own a local Chroma collection at a trusted operator-controlled path.

    Never expose ``path`` to request input or point it at an untrusted database.
    This class uses no Chroma HTTP/server client, disables product telemetry,
    and supplies only its fixed, local embedding function.
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
        """Persist a task and its attributed answer off the event-loop thread."""
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
        """Retrieve nearest stored documents without downloading embeddings."""
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
        """Count persisted entries without blocking the event loop."""

        def read_count() -> int:
            with self._lock:
                return self._collection.count()

        return await asyncio.to_thread(read_count)


__all__ = ["KnowledgeStore"]
