"""The wire contract, in one file.

Everything in this module is an ASSUMPTION: that ARC exposes an OpenAI-compatible
chat API. FlexGrid has not confirmed it. When they do, this is the only module
that should have to change. See FLEXGRID_CONNECTOR_SPEC.md, "Open questions".
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import FlexGridResponseError
from .results import ChatChunk, ChatResult, Usage

# Appended to FLEXGRID_BASE_URL, which already carries the /v1 prefix.
CHAT_PATH = "/chat/completions"
MODELS_PATH = "/models"
# Last event of a stream.
STREAM_DONE = "[DONE]"

Message = Mapping[str, Any]

_MISMATCH = "This does not match the assumed OpenAI-compatible contract. See FLEXGRID_CONNECTOR_SPEC.md."


def request_headers(api_key: str | None, *, stream: bool) -> dict[str, str]:
    headers = {"Accept": "text/event-stream" if stream else "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def normalize_messages(messages: str | Sequence[Message], system: str | None = None) -> list[dict[str, Any]]:
    """A bare string becomes one user message. `system` is prepended as a system message."""
    if isinstance(messages, str):
        out: list[dict[str, Any]] = [{"role": "user", "content": messages}]
    else:
        out = []
        for message in messages:
            if not isinstance(message, Mapping) or "role" not in message or "content" not in message:
                raise ValueError("each message needs a 'role' and a 'content'")
            out.append(dict(message))
    if not out:
        raise ValueError("messages is empty")
    if system:
        out.insert(0, {"role": "system", "content": system})
    return out


def chat_payload(
    messages: str | Sequence[Message],
    model: str,
    *,
    system: str | None = None,
    stream: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
    include_usage: bool = True,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": normalize_messages(messages, system),
        "stream": stream,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
    if stream and include_usage:
        # Without this an OpenAI-compatible server reports no token counts on a stream.
        payload["stream_options"] = {"include_usage": True}
    if extra:
        payload.update(extra)
    return payload


def _usage(data: Any) -> Usage | None:
    if not isinstance(data, Mapping):
        return None

    def count(key: str) -> int | None:
        value = data.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return Usage(count("prompt_tokens"), count("completion_tokens"), count("total_tokens"))


def parse_chat(data: Any, *, headers: dict[str, str], latency_s: float) -> ChatResult:
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise FlexGridResponseError(f"The reply has no choices[0].message.content. {_MISMATCH}") from exc
    if content is None:
        # Allowed by the contract, for example when a reasoning model spends its whole budget thinking.
        content = ""
    if not isinstance(content, str):
        raise FlexGridResponseError(f"choices[0].message.content is not text. {_MISMATCH}")
    return ChatResult(
        text=content,
        model=data.get("model"),
        finish_reason=choice.get("finish_reason"),
        usage=_usage(data.get("usage")),
        latency_s=latency_s,
        raw=data,
        headers=headers,
    )


def parse_stream_event(payload: str) -> ChatChunk:
    """Decode one `data:` event of a stream. STREAM_DONE is handled by the caller."""
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise FlexGridResponseError(f"A stream event was not JSON. {_MISMATCH}") from exc
    if not isinstance(event, dict):
        raise FlexGridResponseError(f"A stream event was not a JSON object. {_MISMATCH}")
    choices = event.get("choices")
    if "error" in event and not choices:
        raise FlexGridResponseError(f"The server reported an error mid-stream: {error_message(payload)}")
    text, finish_reason = "", None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            text = delta["content"]
        finish_reason = choices[0].get("finish_reason")
    return ChatChunk(text=text, finish_reason=finish_reason, usage=_usage(event.get("usage")), raw=event)


def parse_models(data: Any) -> list[dict[str, Any]]:
    """The model list as the endpoint sent it, one dict per model, each with at least an "id"."""
    try:
        cards = [dict(item) for item in data["data"]]
        for card in cards:
            card["id"] = str(card["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FlexGridResponseError(f"The model list has no data[].id. {_MISMATCH}") from exc
    return cards


def error_message(body: str) -> str:
    """The most readable message in an error body, whatever its shape."""
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
        for key in ("message", "detail"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return value
    text = " ".join(body.split())
    return text[:300] if text else "no error body"
