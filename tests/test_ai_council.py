from __future__ import annotations

import json
from threading import Lock

import httpx

from app.services.ai_council import (
    ANTHROPIC_MESSAGES_URL,
    GEMINI_API_BASE_URL,
    INDEPENDENT_STAGE_ORDER,
    OPENAI_RESPONSES_URL,
    AnthropicMessagesProvider,
    GeminiGenerateContentProvider,
    IndependentCouncilResult,
    IndependentMultiAgentCouncil,
    LLMProvider,
    LLMRequest,
    OpenAIResponsesProvider,
    ProviderRegistry,
    WorkflowBrief,
    WorkflowStage,
    build_independent_council_config,
)


class RecordingProvider(LLMProvider):
    def __init__(self, key: str) -> None:
        self.key = key
        self.calls: list[LLMRequest] = []
        self._lock = Lock()

    def generate(self, request: LLMRequest, model: str) -> str:
        with self._lock:
            self.calls.append(request)
        return f"{self.key}/{model}:{request.metadata['stage']}"


def test_three_ai_work_independently_then_all_debate_and_synthesize() -> None:
    openai = RecordingProvider("openai")
    anthropic = RecordingProvider("anthropic")
    gemini = RecordingProvider("gemini")
    provider_models = {
        "openai": "openai-test-model",
        "anthropic": "anthropic-test-model",
        "gemini": "gemini-test-model",
    }
    config = build_independent_council_config(
        participant_providers=("openai", "anthropic", "gemini"),
        integrator_provider="openai",
        provider_models=provider_models,
        debate_rounds=2,
        max_output_tokens=800,
    )
    events: list[str] = []

    result = IndependentMultiAgentCouncil(
        ProviderRegistry([openai, anthropic, gemini]), config
    ).run(
        WorkflowBrief(
            objective="複数AIで商品企画を評価する",
            constraints="公開前に人が承認する",
        ),
        on_event=lambda event: events.append(event.kind),
    )

    assert [item.participant.provider for item in result.independent_proposals] == [
        "openai",
        "anthropic",
        "gemini",
    ]
    assert all(
        [item.research.content, item.planning.content, item.proposal.content]
        == [
            f"{item.participant.provider}/{item.participant.model}:research",
            f"{item.participant.provider}/{item.participant.model}:planning",
            f"{item.participant.provider}/{item.participant.model}:proposal",
        ]
        for item in result.independent_proposals
    )
    assert len(result.debate) == 2
    assert all(len(round_item.contributions) == 3 for round_item in result.debate)
    assert all(
        {item.provider for item in round_item.contributions} == {"openai", "anthropic", "gemini"}
        for round_item in result.debate
    )
    assert result.final_answer == "openai/openai-test-model:synthesis"
    assert events[0] == "independent_stage_started"
    assert events[-1] == "completed"
    assert len(openai.calls) + len(anthropic.calls) + len(gemini.calls) == 16

    providers = (openai, anthropic, gemini)
    for provider in providers:
        own_model = provider_models[provider.key]
        calls_by_stage = {
            stage: next(
                request for request in provider.calls if request.metadata["stage"] == stage.value
            )
            for stage in INDEPENDENT_STAGE_ORDER
        }
        assert calls_by_stage[WorkflowStage.RESEARCH].enable_web_search is True
        planning_prompt = calls_by_stage[WorkflowStage.PLANNING].user_prompt
        proposal_prompt = calls_by_stage[WorkflowStage.PROPOSAL].user_prompt
        assert f"{provider.key}/{own_model}:research" in planning_prompt
        assert f"{provider.key}/{own_model}:research" in proposal_prompt
        assert f"{provider.key}/{own_model}:planning" in proposal_prompt
        for other in providers:
            if other.key == provider.key:
                continue
            assert f"{other.key}/{provider_models[other.key]}:" not in planning_prompt
            assert f"{other.key}/{provider_models[other.key]}:" not in proposal_prompt

    first_round_request = next(
        request
        for request in anthropic.calls
        if request.metadata["stage"] == "debate" and request.metadata["round"] == "1"
    )
    for provider in providers:
        assert (
            f"{provider.key}/{provider_models[provider.key]}:proposal"
            in first_round_request.user_prompt
        )
    second_round_request = next(
        request
        for request in anthropic.calls
        if request.metadata["stage"] == "debate" and request.metadata["round"] == "2"
    )
    assert "ラウンド 1" in second_round_request.user_prompt
    synthesis_request = next(
        request for request in openai.calls if request.metadata["stage"] == "synthesis"
    )
    for provider in providers:
        proposal_marker = f"{provider.key}/{provider_models[provider.key]}:proposal"
        debate_marker = f"{provider.key}/{provider_models[provider.key]}:debate"
        assert proposal_marker in synthesis_request.user_prompt
        assert debate_marker in synthesis_request.user_prompt
    serialized = result.to_dict()
    assert serialized["brief"]["objective"] == "複数AIで商品企画を評価する"
    assert serialized["workflow_version"] == "independent_v2"
    assert len(serialized["independent_proposals"]) == 3
    restored = IndependentCouncilResult.from_dict(serialized)
    assert restored.to_dict() == serialized


def test_openai_provider_uses_responses_api_and_optional_web_search() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "web_search_call",
                        "action": {
                            "sources": [
                                {"title": "OpenAI source", "url": "https://openai.example/source"}
                            ]
                        },
                    },
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "調査結果です。"}],
                    },
                ]
            },
        )

    provider = OpenAIResponsesProvider(
        "openai-test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        maximum_quality=True,
    )
    output = provider.generate(
        LLMRequest(
            system_prompt="system",
            user_prompt="user",
            max_output_tokens=900,
            enable_web_search=True,
        ),
        "openai-test-model",
    )

    assert output.startswith("調査結果です。")
    assert "https://openai.example/source" in output
    assert captured["url"] == OPENAI_RESPONSES_URL
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer openai-test-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "openai-test-model"
    assert payload["tools"] == [{"type": "web_search"}]
    assert payload["include"] == ["web_search_call.action.sources"]
    assert payload["reasoning"] == {"effort": "max", "mode": "pro"}


def test_provider_retries_transient_http_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"output_text": "再試行成功"})

    provider = OpenAIResponsesProvider(
        "openai-test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    output = provider.generate(LLMRequest(system_prompt="system", user_prompt="user"), "model")

    assert output == "再試行成功"
    assert attempts == 2


def test_anthropic_provider_uses_messages_api() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "web_search_tool_result",
                        "content": [
                            {
                                "type": "web_search_result",
                                "title": "Anthropic source",
                                "url": "https://anthropic.example/source",
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "text": "提案結果です。",
                        "citations": [
                            {
                                "type": "web_search_result_location",
                                "title": "Anthropic source",
                                "url": "https://anthropic.example/source",
                            }
                        ],
                    },
                ],
                "usage": {"server_tool_use": {"web_search_requests": 1}},
                "stop_reason": "end_turn",
            },
        )

    provider = AnthropicMessagesProvider(
        "anthropic-test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        maximum_quality=True,
    )
    output = provider.generate(
        LLMRequest(
            system_prompt="system",
            user_prompt="user",
            max_output_tokens=700,
            enable_web_search=True,
        ),
        "anthropic-test-model",
    )

    assert output.startswith("提案結果です。")
    assert "https://anthropic.example/source" in output
    assert captured["url"] == ANTHROPIC_MESSAGES_URL
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["system"] == "system"
    assert payload["messages"] == [{"role": "user", "content": "user"}]
    assert payload["output_config"] == {"effort": "max"}
    assert payload["tools"][0]["type"] == "web_search_20260318"
    assert payload["tools"][0]["allowed_callers"] == ["direct"]


def test_anthropic_web_search_resumes_pause_turn_without_losing_tool_state() -> None:
    payloads: list[dict[str, object]] = []
    paused_content = [
        {
            "type": "server_tool_use",
            "id": "srvtoolu_test",
            "name": "web_search",
            "input": {"query": "市場"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "content": paused_content,
                    "usage": {"server_tool_use": {"web_search_requests": 1}},
                    "stop_reason": "pause_turn",
                },
            )
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "検索を完了しました。"}],
                "usage": {"server_tool_use": {"web_search_requests": 0}},
                "stop_reason": "end_turn",
            },
        )

    provider = AnthropicMessagesProvider(
        "anthropic-test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        maximum_quality=True,
    )
    output = provider.generate(
        LLMRequest(
            system_prompt="system",
            user_prompt="市場を検索して",
            enable_web_search=True,
        ),
        "anthropic-test-model",
    )

    assert output == "検索を完了しました。"
    assert len(payloads) == 2
    assert payloads[1]["messages"] == [
        {"role": "user", "content": "市場を検索して"},
        {"role": "assistant", "content": paused_content},
    ]
    assert payloads[1]["tools"] == payloads[0]["tools"]


def test_gemini_provider_uses_generate_content_api() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "設計結果です。"}]},
                        "groundingMetadata": {
                            "webSearchQueries": ["市場調査"],
                            "groundingChunks": [
                                {
                                    "web": {
                                        "title": "Gemini source",
                                        "uri": "https://gemini.example/source",
                                    }
                                }
                            ],
                        },
                    }
                ]
            },
        )

    provider = GeminiGenerateContentProvider(
        "gemini-test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        maximum_quality=True,
    )
    output = provider.generate(
        LLMRequest(
            system_prompt="system",
            user_prompt="user",
            max_output_tokens=600,
            enable_web_search=True,
        ),
        "gemini-test-model",
    )

    assert output.startswith("設計結果です。")
    assert "https://gemini.example/source" in output
    assert captured["url"] == (f"{GEMINI_API_BASE_URL}/gemini-test-model:generateContent")
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-goog-api-key"] == "gemini-test-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["systemInstruction"] == {"parts": [{"text": "system"}]}
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}
    assert payload["tools"] == [{"googleSearch": {}}]
