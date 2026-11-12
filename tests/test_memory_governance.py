"""Memory quarantine, explicit curation, provenance, and retention boundaries."""

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from hivemind.memory import KnowledgeStore


@pytest.fixture
def governed_path() -> Iterator[str]:
    """Keep isolated test data local and remove only the generated directory."""
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path)
        try:
            path.parent.rmdir()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_unreviewed_output_never_enters_default_retrieval(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    await store.remember("python asyncio", "Unverified model output")
    assert await store.count() == 1
    assert await store.search("python asyncio") == []


async def approve_fixture(store: KnowledgeStore, identifier: str) -> None:
    """Bind a test-only explicit review to the exact stored content hash."""
    record = await store.inspect(identifier)
    await store.approve(
        identifier,
        reviewer="fixture-reviewer",
        evidence="Checked against a fixed, offline test fixture",
        expected_hash=record["metadata"]["content_sha256"],
    )


@pytest.mark.asyncio
async def test_provenance_and_quarantine_survive_reopening(governed_path: str) -> None:
    store = KnowledgeStore(governed_path)
    identifier = await store.remember(
        "test question",
        "unreviewed answer",
        source_id="run-123:cycle:1",
        source_kind="model_output",
        providers=("openai", "claude"),
    )
    reopened = KnowledgeStore(governed_path)
    record = await reopened.inspect(identifier)
    metadata = record["metadata"]
    assert record["document"] == "Task: test question\nAnswer:\nunreviewed answer"
    assert metadata["state"] == "quarantined"
    assert metadata["source_kind"] == "model_output"
    assert metadata["source_id"] == "run-123:cycle:1"
    assert metadata["providers"] == '["claude", "openai"]'
    assert len(metadata["content_sha256"]) == 64
    assert datetime.fromisoformat(metadata["created_at"]).tzinfo is not None
    assert await reopened.search("test question") == []
    await approve_fixture(reopened, identifier)
    result = await store.search("test question")
    assert len(result) == 1
    assert identifier in result[0]
    assert "run-123:cycle:1" in result[0]
    assert "UNTRUSTED_KNOWLEDGE_SOURCE" in result[0]
    assert "fixture-reviewer" not in result[0]
    assert "Checked against" not in result[0]
    approved = (await store.inspect(identifier))["metadata"]
    assert approved["state"] == "approved"
    assert approved["reviewed_by"] == "fixture-reviewer"


@pytest.mark.asyncio
async def test_dedup_is_idempotent_without_merging_different_origins(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    first = await store.remember("q", "a", source_id="source:a")
    await approve_fixture(store, first)
    before = await store.inspect(first)
    assert await store.remember("q", "a", source_id="source:a") == first
    assert await store.inspect(first) == before
    second = await store.remember("q", "a", source_id="source:b")
    assert second != first
    assert await store.count() == 2
    assert len(await store.search("q")) == 1


@pytest.mark.asyncio
async def test_missing_origin_and_stale_review_hash_cannot_be_promoted(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    missing = await store.remember("question", "answer")
    with pytest.raises(ValueError, match="eligible"):
        await approve_fixture(store, missing)
    sourced = await store.remember("question", "answer", source_id="fixture:a")
    with pytest.raises(ValueError, match="eligible"):
        await store.approve(
            sourced, reviewer="reviewer", evidence="review", expected_hash="0" * 64
        )
    assert await store.search("question") == []


@pytest.mark.asyncio
async def test_scope_is_an_explicit_filter_without_claiming_user_authorization(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    local = await store.remember("question", "local answer", source_id="fixture:a")
    other = await store.remember(
        "question", "other answer", source_id="fixture:b", scope="other"
    )
    await approve_fixture(store, local)
    await approve_fixture(store, other)
    assert "local answer" in (await store.search("question"))[0]
    assert len(await store.search("question")) == 1
    assert "other answer" in (await store.search("question", scope="other"))[0]
    assert await store.search("question", scope="unknown") == []


@pytest.mark.asyncio
async def test_rejection_and_selective_delete_remove_only_the_target(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    first = await store.remember("question", "first", source_id="fixture:a")
    second = await store.remember("question", "second", source_id="fixture:b")
    for identifier in (first, second):
        await approve_fixture(store, identifier)
    await store.reject(first, reviewer="operator", reason="Source was contradicted")
    assert (await store.inspect(first))["metadata"]["state"] == "rejected"
    with pytest.raises(ValueError, match="eligible"):
        await approve_fixture(store, first)
    results = await store.search("question")
    assert len(results) == 1
    assert "second" in results[0]
    assert await store.delete(first) is True
    assert await store.delete(first) is False
    assert await store.count() == 1
    assert await store.search("question") == results
    with pytest.raises(KeyError, match="not found"):
        await store.inspect(first)


@pytest.mark.asyncio
async def test_legacy_records_are_excluded_without_deleting_them(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    store._collection.add(ids=["legacy"], documents=["Unreviewed legacy information"])
    assert await store.count() == 1
    assert await store.search("legacy information") == []
    assert (await store.inspect("legacy"))["metadata"] == {}
    with pytest.raises(ValueError, match="eligible"):
        await store.approve(
            "legacy", reviewer="r", evidence="e", expected_hash="0" * 64
        )
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_expiry_excludes_approved_records_and_prevents_late_promotion(
    governed_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KnowledgeStore(governed_path)
    deadline = datetime.now(UTC) + timedelta(hours=1)
    approved = await store.remember(
        "question", "approved", source_id="fixture:a", expires_at=deadline
    )
    pending = await store.remember(
        "question", "pending", source_id="fixture:b", expires_at=deadline
    )
    await approve_fixture(store, approved)
    assert len(await store.search("question")) == 1
    monkeypatch.setattr("hivemind.memory.time.time", lambda: deadline.timestamp() + 1)
    assert await store.search("question") == []
    with pytest.raises(ValueError, match="Expired"):
        await approve_fixture(store, pending)
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_altered_content_is_excluded_even_when_marked_approved(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    identifier = await store.remember("question", "answer", source_id="fixture:a")
    await approve_fixture(store, identifier)
    store._collection.update(ids=[identifier], documents=["Injected replacement"])
    assert await store.search("Injected replacement") == []


@pytest.mark.asyncio
async def test_governance_rejects_unbounded_or_invalid_metadata(
    governed_path: str,
) -> None:
    store = KnowledgeStore(governed_path)
    for text in ("", " " * 3, "x" * 257):
        with pytest.raises(ValueError):
            await store.remember("q", "a", source_id=text)
    with pytest.raises(ValueError, match="Providers"):
        await store.remember("q", "a", providers="agent")
    with pytest.raises(ValueError, match="Providers"):
        await store.remember("q", "a", providers=["agent"] * 21)
    with pytest.raises(ValueError, match="source kind"):
        await store.remember("q", "a", source_kind="verified_truth")
    with pytest.raises(ValueError, match="size limit"):
        await store.remember("q" * 16_001, "a")
    with pytest.raises(ValueError, match="size limit"):
        await store.remember("q", "a" * 100_001)
    with pytest.raises(ValueError, match="timezone"):
        await store.remember("q", "a", expires_at=datetime(2030, 1, 1))
    with pytest.raises(ValueError, match="future"):
        await store.remember("q", "a", expires_at=datetime(2000, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="Scope"):
        await store.search("q", scope="")
    identifier = await store.remember("q", "a", source_id="fixture:a")
    record = await store.inspect(identifier)
    for reviewer, evidence, digest in (
        ("", "e", record["metadata"]["content_sha256"]),
        ("r", "", record["metadata"]["content_sha256"]),
        ("r", "e", "not-a-digest"),
    ):
        with pytest.raises(ValueError):
            await store.approve(
                identifier, reviewer=reviewer, evidence=evidence, expected_hash=digest
            )
