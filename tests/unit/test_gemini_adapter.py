"""The Gemini adapter, against a scripted HTTP transport — no network, no key.

Proves the request is shaped the way the generateContent API documents (system instruction, one
user turn with text and base64 media, JSON mode with a schema, the key in a header) and that every
kind of failure becomes ``AssistantFailed``.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from kise.assistant.application.ports import MediaPart, TextPart
from kise.assistant.domain.errors import AssistantFailed, AssistantRateLimited
from kise.assistant.infrastructure.adapters import InMemoryRateLimiter
from kise.assistant.infrastructure.gemini import GeminiLanguageModel


def answer(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def model_with(handler) -> tuple[GeminiLanguageModel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    model = GeminiLanguageModel(
        "secret-key", model="gemini-test", transport=httpx.MockTransport(record)
    )
    return model, seen


def test_json_request_shape_and_parsing():
    model, seen = model_with(lambda r: httpx.Response(200, json=answer('{"expenses": []}')))
    schema = {"type": "object"}
    result = model.generate_json(
        system="be careful",
        parts=[TextPart("taxi 100"), MediaPart("audio/mp4", b"\x00\x01")],
        schema=schema,
    )
    assert result == {"expenses": []}

    request = seen[0]
    assert request.url.path.endswith("/models/gemini-test:generateContent")
    assert request.headers["x-goog-api-key"] == "secret-key"
    assert "secret-key" not in str(request.url)
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "be careful"}]}
    parts = body["contents"][0]["parts"]
    assert body["contents"][0]["role"] == "user"
    assert parts[0] == {"text": "taxi 100"}
    assert parts[1]["inlineData"]["mimeType"] == "audio/mp4"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"\x00\x01"
    assert body["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }


def test_text_mode_has_no_json_config():
    model, seen = model_with(lambda r: httpx.Response(200, json=answer("  1,250 ETB on food. ")))
    assert model.generate_text(system="s", parts=[TextPart("q")]) == "1,250 ETB on food."
    assert "generationConfig" not in json.loads(seen[0].content)


def test_text_split_across_parts_is_joined():
    payload = {"candidates": [{"content": {"parts": [{"text": '{"a":'}, {"text": " 1}"}]}}]}
    model, _ = model_with(lambda r: httpx.Response(200, json=payload))
    assert model.generate_json(system="s", parts=[TextPart("q")], schema={}) == {"a": 1}


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500, json={"error": {"status": "INTERNAL"}}), "provider_http_500"),
        (httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}), "provider_http_400"),
        (httpx.Response(429, json={}), "provider_rate_limited"),
        (httpx.Response(200, text="<html>"), "provider_not_json"),
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "blocked"),
        (httpx.Response(200, json={"candidates": []}), "no_answer"),
        (
            httpx.Response(
                200, json={"candidates": [{"content": {}, "finishReason": "MAX_TOKENS"}]}
            ),
            "no_text_max_tokens",
        ),
        (httpx.Response(200, json=answer("not json")), "answer_not_json"),
        (httpx.Response(200, json=answer("[1, 2]")), "answer_not_an_object"),
    ],
)
def test_failures_become_assistant_failed(response, reason):
    model, _ = model_with(lambda r: response)
    with pytest.raises(AssistantFailed) as caught:
        model.generate_json(system="s", parts=[TextPart("q")], schema={})
    assert caught.value.detail["reason"] == reason


def test_transport_errors_become_assistant_failed():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    model, _ = model_with(boom)
    with pytest.raises(AssistantFailed) as caught:
        model.generate_text(system="s", parts=[TextPart("q")])
    assert caught.value.detail["reason"] == "unreachable"


def test_a_key_is_required():
    with pytest.raises(ValueError):
        GeminiLanguageModel("", model="m")


# -- rate limiter -----------------------------------------------------------


def test_rate_limiter_rolls_over_the_window():
    clock = [0.0]
    limiter = InMemoryRateLimiter(2, window_seconds=60, now=lambda: clock[0])
    limiter.take("a")
    limiter.take("a")
    limiter.take("b")  # keys are independent
    with pytest.raises(AssistantRateLimited):
        limiter.take("a")
    clock[0] = 61
    limiter.take("a")


def test_a_zero_limit_means_unlimited():
    limiter = InMemoryRateLimiter(0)
    for _ in range(100):
        limiter.take("a")
