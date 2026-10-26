"""Recuperação persistente local com embeddings determinísticos e sem downloads."""

import asyncio
import hashlib
import json
import math
import re
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import chromadb
import numpy as np
from chromadb.api.types import Embeddable, EmbeddingFunction, Embeddings, Where
from chromadb.base_types import LiteralValue, LogicalOperator, WhereOperator
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

    @staticmethod
    def _label(value: str, name: str, maximum: int = 256) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(
                f"{name} must be nonempty and at most {maximum} characters"
            )
        return value

    def _record(self, identifier: str) -> dict[str, Any]:
        """Lê sob o lock do chamador; o banco local não é uma fonte de instruções."""
        self._label(identifier, "Identifier", 128)
        result = self._collection.get(
            ids=[identifier], include=["documents", "metadatas"]
        )
        documents = result["documents"]
        metadatas = result["metadatas"]
        if not result["ids"] or not documents or not metadatas:
            raise KeyError("Knowledge record not found")
        return {
            "id": identifier,
            "document": documents[0],
            "metadata": dict(metadatas[0] or {}),
        }

    async def remember(
        self,
        task: str,
        answer: str,
        *,
        source_id: str = "unspecified",
        source_kind: str = "unverified_input",
        providers: Sequence[str] = (),
        scope: str = "local",
        expires_at: datetime | None = None,
    ) -> str:
        """Registra em quarentena; persistência nunca é aprovação factual.

        Conteúdo idêntico da mesma origem é idempotente. Origens diferentes
        mantêm registros próprios; metadados antigos nunca são sobrescritos.
        ``scope`` é um filtro local, não autenticação nem isolamento de usuários.
        """
        if (
            not isinstance(task, str)
            or not isinstance(answer, str)
            or not task.strip()
            or not answer.strip()
        ):
            raise ValueError("Task and answer must be nonempty")
        if len(task) > 16_000 or len(answer) > 100_000:
            raise ValueError("Knowledge content exceeds the storage size limit")
        self._label(source_id, "Source ID")
        self._label(scope, "Scope", 128)
        if not isinstance(source_kind, str) or source_kind not in {
            "unverified_input",
            "model_output",
            "demo_output",
            "agent_output",
        }:
            raise ValueError("Unsupported source kind")
        if (
            not isinstance(providers, Sequence)
            or isinstance(providers, (str, bytes))
            or len(providers) > 20
        ):
            raise ValueError("Providers must be a bounded sequence of names")
        provider_names = sorted(
            {self._label(provider, "Provider", 128) for provider in providers}
        )
        expiry = 0.0
        if expires_at is not None:
            if not isinstance(expires_at, datetime):
                raise ValueError("Expiry must be a timezone-aware datetime")
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ValueError("Expiry must include a timezone")
            expiry = expires_at.timestamp()
            if expiry <= time.time():
                raise ValueError("Expiry must be in the future")
        document = f"Task: {task}\nAnswer:\n{answer}"
        digest = hashlib.sha256(document.encode()).hexdigest()
        origins = json.dumps(provider_names, ensure_ascii=True)
        identity = json.dumps([scope, source_kind, source_id, origins, digest])
        identifier = hashlib.sha256(identity.encode()).hexdigest()
        metadata: dict[str, Any] = {
            "schema_version": 1,
            "state": "quarantined",
            "source_kind": source_kind,
            "source_id": source_id,
            "providers": origins,
            "scope": scope,
            "content_sha256": digest,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": expiry,
        }

        def write() -> str:
            with self._lock:
                existing = self._collection.get(ids=[identifier], include=[])
                if not existing["ids"]:
                    self._collection.add(
                        ids=[identifier], documents=[document], metadatas=[metadata]
                    )
            return identifier

        return await asyncio.to_thread(write)

    async def inspect(self, identifier: str) -> dict[str, Any]:
        """Expõe texto e envelope para curadoria local explícita, sem promover."""

        def read() -> dict[str, Any]:
            with self._lock:
                return self._record(identifier)

        return await asyncio.to_thread(read)

    async def approve(
        self,
        identifier: str,
        *,
        reviewer: str,
        evidence: str,
        expected_hash: str,
    ) -> None:
        """Promove após revisão local vinculada ao hash, nunca por consenso de IA.

        Este método administrativo não tem endpoint REST nem ferramenta de agente.
        O operador deve conferir fonte e evidências antes de chamá-lo; uma string
        de evidência, por si só, não comprova veracidade ou identidade humana.
        """
        self._label(reviewer, "Reviewer")
        self._label(evidence, "Review evidence", 2000)
        if not isinstance(expected_hash, str) or not re.fullmatch(
            "[0-9a-f]{64}", expected_hash
        ):
            raise ValueError("Expected hash must be a SHA-256 digest")

        def promote() -> None:
            with self._lock:
                record = self._record(identifier)
                metadata = record["metadata"]
                digest = hashlib.sha256(record["document"].encode()).hexdigest()
                if (
                    metadata.get("schema_version") != 1
                    or metadata.get("state") != "quarantined"
                    or metadata.get("source_id") in {None, "unspecified"}
                    or metadata.get("content_sha256") != digest
                    or expected_hash != digest
                ):
                    raise ValueError("Record is not eligible for reviewed promotion")
                expiry = metadata.get("expires_at")
                if not isinstance(expiry, (int, float)) or (
                    expiry != 0 and expiry <= time.time()
                ):
                    raise ValueError("Expired records cannot be promoted")
                metadata.update(
                    state="approved",
                    reviewed_by=reviewer,
                    review_evidence=evidence,
                    reviewed_at=datetime.now(UTC).isoformat(),
                )
                self._collection.update(ids=[identifier], metadatas=[metadata])

        await asyncio.to_thread(promote)

    async def reject(self, identifier: str, *, reviewer: str, reason: str) -> None:
        """Retira um registro da recuperação e registra o motivo local da decisão."""
        self._label(reviewer, "Reviewer")
        self._label(reason, "Rejection reason", 2000)

        def reject_record() -> None:
            with self._lock:
                metadata = self._record(identifier)["metadata"]
                metadata.update(
                    state="rejected",
                    reviewed_by=reviewer,
                    review_evidence=reason,
                    reviewed_at=datetime.now(UTC).isoformat(),
                )
                self._collection.update(ids=[identifier], metadatas=[metadata])

        await asyncio.to_thread(reject_record)

    async def delete(self, identifier: str) -> bool:
        """Exclui somente o identificador confirmado pelo operador local."""
        self._label(identifier, "Identifier", 128)

        def delete_record() -> bool:
            with self._lock:
                existing = self._collection.get(ids=[identifier], include=[])
                if not existing["ids"]:
                    return False
                self._collection.delete(ids=[identifier])
                return True

        return await asyncio.to_thread(delete_record)

    async def search(
        self, query: str, limit: int = 3, *, scope: str = "local"
    ) -> list[str]:
        """Recupera apenas registros aprovados, íntegros e não expirados.

        Registros legados sem envelope continuam armazenados, mas ficam fora da
        recuperação padrão. Aprovação não transforma o contexto em instruções.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("Search limit must be a positive integer")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Search query must be nonempty")
        self._label(scope, "Scope", 128)

        def read() -> list[str]:
            with self._lock:
                count = self._collection.count()
                if not count:
                    return []
                comparison: dict[WhereOperator | LogicalOperator, LiteralValue] = {
                    "$gt": time.time()
                }
                not_expired: Where = {"expires_at": comparison}
                expiry_filter: Where = {"$or": [{"expires_at": 0}, not_expired]}
                conditions: list[Where] = [
                    {"schema_version": 1},
                    {"state": "approved"},
                    {"scope": scope},
                    expiry_filter,
                ]
                result = self._collection.query(
                    query_embeddings=self._embedding([query]),
                    n_results=min(limit, count),
                    where={"$and": conditions},
                    include=["documents", "metadatas"],
                )
                documents = result["documents"]
                metadatas = result["metadatas"]
                if not documents or not metadatas:
                    return []
                approved: list[str] = []
                for identifier, document, metadata in zip(
                    result["ids"][0], documents[0], metadatas[0], strict=True
                ):
                    if (
                        metadata.get("content_sha256")
                        != hashlib.sha256(document.encode()).hexdigest()
                    ):
                        continue
                    provenance = json.dumps(
                        {
                            "id": identifier,
                            "source_kind": metadata["source_kind"],
                            "source_id": metadata["source_id"],
                            "content_sha256": metadata["content_sha256"],
                        },
                        ensure_ascii=True,
                    )
                    approved.append(
                        f"UNTRUSTED_KNOWLEDGE_SOURCE: {provenance}\n{document}"
                    )
                return approved

        return await asyncio.to_thread(read)

    async def count(self) -> int:
        """Conta registros persistidos sem bloquear o ciclo de eventos."""

        def read_count() -> int:
            with self._lock:
                return self._collection.count()

        return await asyncio.to_thread(read_count)


__all__ = ["KnowledgeGraph", "KnowledgeStore"]
