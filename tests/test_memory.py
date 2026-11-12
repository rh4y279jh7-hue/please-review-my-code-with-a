"""Local-only Chroma persistence and deterministic embedding tests."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from hivemind.memory import KnowledgeStore, LocalEmbedding


@pytest.fixture
def local_path() -> Iterator[str]:
    """Keep test databases inside the repository, not system temp directories."""
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_local_persistence_and_retrieval(local_path: str) -> None:
    store = KnowledgeStore(local_path)
    assert await store.count() == 0
    assert await store.search("python") == []
    identifier = await store.remember(
        "python asyncio", "Use async tasks for concurrency", source_id="fixture:python"
    )
    assert identifier
    bread = await store.remember(
        "bread yeast", "Allow dough to rise", source_id="fixture:bread"
    )
    assert await store.count() == 2
    assert await store.search("python asyncio", limit=1) == []
    for record_id in (identifier, bread):
        record = await store.inspect(record_id)
        await store.approve(
            record_id,
            reviewer="fixture-reviewer",
            evidence="Static fixture reviewed against the expected test answer",
            expected_hash=record["metadata"]["content_sha256"],
        )
    matches = await store.search("python asyncio", limit=1)
    assert len(matches) == 1
    assert "Use async tasks" in matches[0]
    reopened = KnowledgeStore(local_path)
    assert await reopened.count() == 2
    assert await reopened.search("python asyncio", limit=1) == matches
    assert len(await reopened.search("python", limit=100)) == 2


def test_embeddings_are_local_and_deterministic() -> None:
    embedding = LocalEmbedding()
    vectors = embedding(["python python", "bread", ""])
    assert [vector.tolist() for vector in vectors] == [
        vector.tolist() for vector in LocalEmbedding()(["python python", "bread", ""])
    ]
    assert len(vectors[0]) == 256
    assert vectors[0].tolist() != vectors[1].tolist()
    assert sum(value * value for value in vectors[0]) == pytest.approx(1)
    assert embedding.get_config() == {}
    with pytest.raises(ValueError, match="unsupported"):
        LocalEmbedding.build_from_config({"url": "https://example.invalid"})


def test_local_client_telemetry_disabled(local_path: str) -> None:
    with patch("hivemind.memory.chromadb.PersistentClient") as factory:
        factory.return_value.get_or_create_collection.return_value = MagicMock()
        KnowledgeStore(local_path)
        kwargs = factory.call_args.kwargs
        assert kwargs["path"] == local_path
        assert kwargs["settings"].anonymized_telemetry is False
        assert kwargs["settings"].allow_reset is False
        collection = factory.return_value.get_or_create_collection.call_args.kwargs
        assert isinstance(collection["embedding_function"], LocalEmbedding)


@pytest.mark.parametrize("path", ["", "  ", "https://example.invalid", "file://data"])
def test_invalid_paths(path: str) -> None:
    with pytest.raises(ValueError, match="local"):
        KnowledgeStore(path)


@pytest.mark.asyncio
async def test_invalid_memory_inputs(local_path: str) -> None:
    store = KnowledgeStore(local_path)
    for limit in (0, -1, True):
        with pytest.raises(ValueError):
            await store.search("query", limit=limit)
    with pytest.raises(ValueError):
        await store.search("")
    with pytest.raises(ValueError):
        await store.remember("", "answer")
    with pytest.raises(ValueError):
        await store.remember("task", "")
