"""Offline API tests with an injectable orchestrator and bounded task state."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from hivemind.api.app import create_app
from hivemind.config import Settings
from hivemind.core import Orchestrator
from hivemind.logging import JsonFormatter


def fake_engine(*, fail: bool = False, delay: float = 0) -> Any:
    """Create a minimal test double; never call AI APIs or the vector store."""
    engine = Mock()
    engine.memory.count = AsyncMock(return_value=3)
    engine.feedback.snapshot.return_value = {"completed_tasks": 1, "cycles": 2}
    engine.close = AsyncMock()

    async def run(task: str, *, cycles: int | None, on_event: Any) -> dict[str, Any]:
        await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("private provider failure")
        await on_event({"type": "agent_response", "agent": "demo", "response": task})
        return {
            "answer": "síntese demo",
            "responses": {"demo": task},
            "failures": [],
            "cycles": cycles or 2,
            "memory_ids": ["entry"],
        }

    engine.run = run
    return engine


def settings(**kwargs: Any) -> Settings:
    """Ignore developer .env files for deterministic tests."""
    return Settings(_env_file=None, **kwargs)


def test_api_roundtrip_and_websocket_replay() -> None:
    engine = fake_engine()
    with TestClient(create_app(settings(), cast(Orchestrator, engine))) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/tasks", json={"task": "Investigue", "cycles": 1})
        assert response.status_code == 202
        task_id = response.json()["id"]
        with client.websocket_connect(f"/ws/tasks/{task_id}") as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "task_finished":
                    break
        assert any(event["type"] == "agent_response" for event in events)
        view = client.get(f"/tasks/{task_id}").json()
        assert view["status"] == "completed"
        assert view["result"]["answer"] == "síntese demo"
        assert client.get("/metrics").json()["knowledge_entries"] == 3
        assert client.get("/tasks/absent").status_code == 404
    engine.close.assert_awaited_once()


def test_demo_factory_with_real_local_memory(tmp_path: Path) -> None:
    """Exercise the API/engine contract using only offline demo contributors."""
    config = settings(memory_path=str(tmp_path / "knowledge"))
    with TestClient(create_app(config)) as client:
        assert client.get("/metrics").json()["knowledge_entries"] == 0
        task_id = client.post("/tasks", json={"task": "Planeje uma pesquisa"}).json()[
            "id"
        ]
        with client.websocket_connect(f"/ws/tasks/{task_id}") as ws:
            while ws.receive_json()["type"] != "task_finished":
                pass
        view = client.get(f"/tasks/{task_id}").json()
        assert view["status"] == "completed"
        assert len(view["result"]["responses"]) == 3
        metrics = client.get("/metrics").json()
        assert metrics["knowledge_entries"] == 2
        assert metrics["completed_tasks"] == 1
    with TestClient(create_app(config)) as restarted:
        assert restarted.get("/metrics").json()["knowledge_entries"] == 2


async def test_completion_during_websocket_accept_delivers_terminal_event() -> None:
    """Events queued while accepting a connection must not be lost."""
    release = asyncio.Event()
    engine = fake_engine()
    generate = engine.run

    async def controlled_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await release.wait()
        return await generate(*args, **kwargs)

    engine.run = controlled_run
    app = create_app(settings(), engine)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            task_id = (await client.post("/tasks", json={"task": "a"})).json()["id"]
            sent: list[dict[str, Any]] = []

            async def accept_after_completion() -> None:
                release.set()
                for _ in range(100):
                    view = (await client.get(f"/tasks/{task_id}")).json()
                    if view["status"] == "completed":
                        return
                    await asyncio.sleep(0)
                pytest.fail("Task did not complete during the handshake")

            websocket = Mock(spec=WebSocket)
            websocket.headers = {}
            websocket.accept = AsyncMock(side_effect=accept_after_completion)
            websocket.send_json = AsyncMock(side_effect=sent.append)
            websocket.close = AsyncMock()
            endpoint = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/ws/tasks/{task_id}"
            )
            await endpoint(websocket, task_id)
            assert sent[-1]["type"] == "task_finished"
            assert sent[-1]["task"]["status"] == "completed"
            assert any(event["type"] == "agent_response" for event in sent)


@pytest.mark.parametrize("task", ["", "   ", "x" * 16001])
def test_invalid_task(task: str) -> None:
    with TestClient(create_app(settings(), fake_engine())) as client:
        assert client.post("/tasks", json={"task": task}).status_code == 422


def test_cycles_cannot_exceed_server_limit() -> None:
    with TestClient(create_app(settings(max_cycles=1), fake_engine())) as client:
        for cycles in (0, 2, 11):
            assert (
                client.post("/tasks", json={"task": "a", "cycles": cycles}).status_code
                == 422
            )


def test_authentication_on_rest_and_websocket() -> None:
    token = "offline-test-token-" * 3
    with TestClient(
        create_app(settings(api_token=SecretStr(token)), fake_engine())
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 401
        assert client.post("/tasks", json={"task": "a"}).status_code == 401
        assert (
            client.get(
                "/metrics", headers={"Authorization": "Bearer " + "wrong"}
            ).status_code
            == 401
        )
        # Non-ASCII input must not crash constant-time token comparison.
        assert (
            client.get(
                "/metrics",
                headers={"Authorization": b"Bearer " + b"\xc3\xa9"},
            ).status_code
            == 401
        )
        headers = {"Authorization": "Bearer " + token}
        task_id = client.post("/tasks", json={"task": "a"}, headers=headers).json()[
            "id"
        ]
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/tasks/{task_id}"):
                pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/ws/tasks/{task_id}",
                headers={**headers, "Origin": "https://evil.test"},
            ):
                pass
        with client.websocket_connect(f"/ws/tasks/{task_id}", headers=headers) as ws:
            while ws.receive_json()["type"] != "task_finished":
                pass


def test_provider_failure_is_sanitized() -> None:
    with TestClient(create_app(settings(), fake_engine(fail=True))) as client:
        task_id = client.post("/tasks", json={"task": "a"}).json()["id"]
        with client.websocket_connect(f"/ws/tasks/{task_id}") as ws:
            final = ws.receive_json()
        assert final["task"]["status"] == "failed"
        assert "private" not in json.dumps(final)
        assert client.get(f"/tasks/{task_id}").json()["result"] is None


def test_concurrency_limit_and_shutdown_cancellation() -> None:
    engine = fake_engine(delay=60)
    config = settings(max_tasks=1, max_concurrent_tasks=1)
    app = create_app(config, engine)
    with TestClient(app) as client:
        task_id = client.post("/tasks", json={"task": "a"}).json()["id"]
        assert client.post("/tasks", json={"task": "b"}).status_code == 429
        assert client.get(f"/tasks/{task_id}").json()["status"] in {"queued", "running"}
    engine.close.assert_awaited_once()


def test_completed_task_eviction() -> None:
    config = settings(max_tasks=1, max_concurrent_tasks=1)
    with TestClient(create_app(config, fake_engine())) as client:
        first = client.post("/tasks", json={"task": "a"}).json()["id"]
        with client.websocket_connect(f"/ws/tasks/{first}") as ws:
            while ws.receive_json()["type"] != "task_finished":
                pass
        second = client.post("/tasks", json={"task": "b"})
        assert second.status_code == 202
        assert client.get(f"/tasks/{first}").status_code == 404


@pytest.mark.parametrize(
    "kwargs",
    [
        {"demo_mode": False},
        {"api_token": SecretStr("short")},
        {"openai_api_key": SecretStr(" ")},
        {"max_cycles": 0},
        {"max_cycles": 11},
        {"max_tasks": 1, "max_concurrent_tasks": 2},
    ],
)
def test_configuration_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        settings(**kwargs)


def test_empty_optional_credentials_and_secret_representation() -> None:
    assert settings(api_token="").api_token is None
    config = settings(openai_api_key=SecretStr("private-test-value"))
    assert "private-test-value" not in repr(config)


def test_configuration_errors_do_not_disclose_credentials() -> None:
    with pytest.raises(ValidationError) as error:
        settings(demo_mode=False, openai_api_key="private-test-value")
    assert "private-test-value" not in str(error.value)


def test_logs_are_json_and_omit_exception_payload() -> None:
    import logging

    record = logging.LogRecord("hivemind", logging.INFO, "", 0, "task_failed", (), None)
    rendered = json.loads(JsonFormatter().format(record))
    assert rendered["event"] == "task_failed"
    assert rendered["level"] == "INFO"
