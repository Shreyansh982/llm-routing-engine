from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from providers.adapter import ProviderUnavailableError
from routers.http_router import RouterUnavailableError
from routing.engine import ProviderExecutionError, RoutingEngineError
from schemas.models import ChatRequest, FailureScope, ImageInput, TaskGroup
from tests.conftest import PNG_BASE64, FakeProvider, QueueRouter, decision, make_engine, provider


def group_providers(group: TaskGroup, count: int, backends: list[str] | None = None):
    return [
        provider(
            f"{group.value}_{index}",
            [group.value],
            backend=(backends or ["openrouter"] * count)[index - 1],
            task_group=group,
            priority=index,
            image=group == TaskGroup.MULTIMODAL,
        )
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize(
    ("group", "count"),
    [
        (TaskGroup.GENERAL, 4),
        (TaskGroup.CODING_REASONING, 4),
        (TaskGroup.CREATIVE, 2),
        (TaskGroup.MULTIMODAL, 2),
    ],
)
def test_each_task_chain_falls_through_in_priority_order(group: TaskGroup, count: int) -> None:
    providers = group_providers(group, count)
    engine, state, _ = make_engine(QueueRouter([decision(group)]), QueueRouter([]), providers)
    FakeProvider.failures = {
        candidate.id: ProviderUnavailableError("down", failure_reason="MODEL_UNAVAILABLE")
        for candidate in providers[:-1]
    }
    FakeProvider.responses = {providers[-1].id: "final answer"}
    request = ChatRequest(conversation_id="chain", message="work")
    if group == TaskGroup.MULTIMODAL:
        request = request.model_copy(
            update={"image": ImageInput(media_type="image/png", data_base64=PNG_BASE64)}
        )

    result = asyncio.run(engine.handle(request, developer_mode=True))

    assert result.response == "final answer"
    assert [provider_id for provider_id, _ in FakeProvider.calls] == [item.id for item in providers]
    assert state.load("chain").last_provider == providers[-1].id
    assert result.diagnostics is not None
    assert result.diagnostics.fallback_count == count - 1
    assert result.diagnostics.final_provider == providers[-1].id


@pytest.mark.parametrize("group,count", [(TaskGroup.GENERAL, 4), (TaskGroup.CODING_REASONING, 4), (TaskGroup.CREATIVE, 2), (TaskGroup.MULTIMODAL, 2)])
def test_task_chain_exhaustion_is_controlled(group: TaskGroup, count: int) -> None:
    providers = group_providers(group, count)
    engine, _, _ = make_engine(QueueRouter([decision(group)]), QueueRouter([]), providers)
    FakeProvider.fail = True
    request = ChatRequest(conversation_id="exhaust", message="work")
    if group == TaskGroup.MULTIMODAL:
        request = request.model_copy(
            update={"image": ImageInput(media_type="image/png", data_base64=PNG_BASE64)}
        )
    with pytest.raises(ProviderExecutionError):
        asyncio.run(engine.handle(request))
    assert len(FakeProvider.calls) == count


def test_auth_failure_skips_remaining_models_on_backend_for_current_request() -> None:
    providers = group_providers(TaskGroup.GENERAL, 3, ["google", "google", "groq"])
    engine, _, _ = make_engine(QueueRouter([decision()]), QueueRouter([]), providers)
    FakeProvider.failures = {
        providers[0].id: ProviderUnavailableError(
            "auth",
            http_status=401,
            failure_reason="HTTP_401_AUTHENTICATION_FAILED",
            failure_scope=FailureScope.NON_TRANSIENT,
            blocks_backend=True,
        )
    }
    FakeProvider.responses = {providers[2].id: "healthy fallback"}

    result = asyncio.run(engine.handle(ChatRequest(conversation_id="auth", message="hello"), developer_mode=True))

    assert result.response == "healthy fallback"
    assert [provider_id for provider_id, _ in FakeProvider.calls] == [providers[0].id, providers[2].id]
    assert result.diagnostics is not None
    assert result.diagnostics.fallback_path[1]["outcome"] == "skipped_auth_failure"


def test_router_auth_failure_blocks_same_backend_task_models_for_request() -> None:
    providers = group_providers(TaskGroup.GENERAL, 2, ["google", "groq"])
    primary = QueueRouter(
        [
            RouterUnavailableError(
                "auth",
                http_status=401,
                failure_reason="HTTP_401_AUTHENTICATION_FAILED",
                backend="google",
                blocks_backend=True,
            )
        ]
    )
    engine, _, _ = make_engine(primary, QueueRouter([decision()]), providers)
    FakeProvider.responses = {providers[1].id: "groq answer"}

    result = asyncio.run(
        engine.handle(ChatRequest(conversation_id="router-auth", message="hello"), developer_mode=True)
    )

    assert result.response == "groq answer"
    assert [provider_id for provider_id, _ in FakeProvider.calls] == [providers[1].id]
    assert result.diagnostics is not None
    assert result.diagnostics.fallback_path[0]["outcome"] == "skipped_auth_failure"


def test_router_input_contains_groups_but_no_provider_or_model_identifiers() -> None:
    primary = QueueRouter([decision()])
    providers = group_providers(TaskGroup.GENERAL, 2, ["google", "openrouter"])
    engine, _, _ = make_engine(primary, QueueRouter([]), providers)
    asyncio.run(engine.handle(ChatRequest(conversation_id="safe-router", message="hello")))
    serialized = primary.calls[0].model_dump_json()
    assert "general" in serialized
    for candidate in providers:
        assert candidate.id not in serialized
        assert candidate.model not in serialized
        assert candidate.backend not in serialized


def test_image_request_is_structured_and_never_stored_in_conversation() -> None:
    providers = group_providers(TaskGroup.MULTIMODAL, 1, ["nvidia"])
    engine, state, _ = make_engine(
        QueueRouter([decision(TaskGroup.MULTIMODAL)]), QueueRouter([]), providers
    )
    encoded = PNG_BASE64
    result = asyncio.run(
        engine.handle(
            ChatRequest(
                conversation_id="image",
                message="describe",
                image={"media_type": "image/png", "data_base64": encoded},
            )
        )
    )
    assert result.action == "ANSWER"
    assert FakeProvider.calls[0][1].image is not None
    assert encoded not in state.load("image").model_dump_json()


def test_invalid_images_are_rejected_before_router_or_provider() -> None:
    primary = QueueRouter([])
    providers = group_providers(TaskGroup.MULTIMODAL, 1)
    engine, _, _ = make_engine(primary, QueueRouter([]), providers)
    client = TestClient(create_app(engine=engine))
    invalid = [
        {"media_type": "image/gif", "data_base64": "aW1hZ2U="},
        {"media_type": "image/png", "data_base64": "not base64"},
        [
            {"media_type": "image/png", "data_base64": "aW1hZ2U="},
            {"media_type": "image/png", "data_base64": "aW1hZ2U="},
        ],
    ]
    for index, image in enumerate(invalid):
        response = client.post(
            "/api/v1/chat",
            json={"conversation_id": f"invalid-{index}", "message": "describe", "image": image},
        )
        assert response.status_code == 422
    assert primary.calls == [] and FakeProvider.calls == []


def test_image_cannot_be_classified_as_text_only_group() -> None:
    engine, _, _ = make_engine(
        QueueRouter([decision(TaskGroup.GENERAL)]),
        QueueRouter([decision(TaskGroup.GENERAL)]),
        group_providers(TaskGroup.MULTIMODAL, 1),
    )
    with pytest.raises(RoutingEngineError):
        asyncio.run(
            engine.handle(
                ChatRequest(
                    conversation_id="bad-image-group",
                    message="describe",
                    image={"media_type": "image/png", "data_base64": PNG_BASE64},
                )
            )
        )
