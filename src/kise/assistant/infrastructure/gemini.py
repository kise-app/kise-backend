"""The Gemini adapter for the ``LanguageModel`` port, over plain HTTPS.

One endpoint, ``models/{model}:generateContent``, covers all three inputs: text, an audio clip and a
receipt photo go in as parts of one user turn (media inline, base64), the instructions go in
``systemInstruction``, and for extraction the answer is constrained to a JSON schema with
``responseMimeType: application/json`` + ``responseJsonSchema``. No SDK: one ``httpx`` call keeps
the dependency small and the request visible.

The API key travels in the ``x-goog-api-key`` header and is never logged. Failures of every kind —
transport errors, non-2xx answers, a blocked prompt, text that is not JSON — become
``AssistantFailed`` with a short machine-readable reason, so a caller sees one error type.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

from kise.assistant.application.ports import LanguageModel, MediaPart, Part, TextPart
from kise.assistant.domain.errors import AssistantFailed

logger = logging.getLogger("kise.assistant")

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiLanguageModel(LanguageModel):
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 45.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiLanguageModel needs an API key")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    @staticmethod
    def _part(part: Part) -> dict[str, Any]:
        if isinstance(part, TextPart):
            return {"text": part.text}
        if isinstance(part, MediaPart):
            return {
                "inlineData": {
                    "mimeType": part.mime_type,
                    "data": base64.b64encode(part.data).decode("ascii"),
                }
            }
        raise TypeError(f"Unknown part {part!r}")  # pragma: no cover - closed union

    def _body(
        self, system: str, parts: list[Part], schema: dict[str, Any] | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [self._part(p) for p in parts]}],
        }
        if schema is not None:
            body["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            }
        return body

    def _call(self, body: dict[str, Any]) -> str:
        url = f"{self._base_url}/models/{self._model}:generateContent"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(
                    url,
                    json=body,
                    headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise AssistantFailed("timeout") from exc
        except httpx.HTTPError as exc:
            raise AssistantFailed("unreachable") from exc

        if response.status_code == 429:
            raise AssistantFailed("provider_rate_limited")
        if response.status_code >= 400:
            # The body can echo the request; log only the status and the provider's error code.
            status_text = ""
            try:
                status_text = str(response.json().get("error", {}).get("status", ""))
            except ValueError:
                pass
            logger.warning("Gemini returned %s %s", response.status_code, status_text)
            raise AssistantFailed(f"provider_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise AssistantFailed("provider_not_json") from exc

        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise AssistantFailed("blocked")
        candidates = data.get("candidates") or []
        if not candidates:
            raise AssistantFailed("no_answer")
        candidate = candidates[0] or {}
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            reason = str(candidate.get("finishReason") or "empty").lower()
            raise AssistantFailed(f"no_text_{reason}")
        return text

    def generate_json(
        self, *, system: str, parts: list[Part], schema: dict[str, Any]
    ) -> dict[str, Any]:
        text = self._call(self._body(system, parts, schema))
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AssistantFailed("answer_not_json") from exc
        if not isinstance(value, dict):
            raise AssistantFailed("answer_not_an_object")
        return value

    def generate_text(self, *, system: str, parts: list[Part]) -> str:
        return self._call(self._body(system, parts, None)).strip()
