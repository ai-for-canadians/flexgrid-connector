"""What the client hands back."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str | None
    finish_reason: str | None
    usage: Usage | None
    latency_s: float
    # The decoded body and the response headers, untouched. If ARC returns anything
    # beyond the assumed contract (a KV-cache or session handle, say), it is visible
    # here before the connector has first-class support for it.
    raw: dict[str, Any] = field(repr=False)
    headers: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class ChatChunk:
    """One piece of a streamed reply. The last chunks may carry finish_reason or usage and no text."""

    text: str
    finish_reason: str | None = None
    usage: Usage | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
