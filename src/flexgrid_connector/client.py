"""The client: chat(), chat_stream() and list_models(), blocking and async."""
from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections.abc import AsyncIterator, Generator, Mapping, Sequence
from typing import Any

from . import contract
from .config import FlexGridConfig
from .contract import Message
from .errors import FlexGridConfigError, FlexGridResponseError
from .results import ChatChunk, ChatResult
from .transport import Transport


class FlexGridClient:
    """Blocking client for a FlexGrid endpoint.

    With no arguments it reads FLEXGRID_BASE_URL, FLEXGRID_MODEL, FLEXGRID_API_KEY
    and FLEXGRID_TIMEOUT from the environment. Every failure raises a FlexGridError
    subclass. Nothing here ever calls a different provider.
    """

    def __init__(self, config: FlexGridConfig | None = None):
        self.config = config if config is not None else FlexGridConfig.from_env()
        self._transport = Transport(self.config)

    def list_models(self) -> list[str]:
        """Model IDs the endpoint advertises."""
        return [card["id"] for card in self.describe_models()]

    def describe_models(self) -> list[dict[str, Any]]:
        """The model list as the endpoint sent it. Keeps whatever else it reports, such as context limits."""
        data, _ = self._transport.request_json("GET", contract.MODELS_PATH)
        return contract.parse_models(data)

    def chat(
        self,
        messages: str | Sequence[Message],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        """Send a conversation, return the whole reply.

        messages is a string (one user message) or a list of {"role", "content"} dicts.
        model defaults to FLEXGRID_MODEL. extra is merged into the request body as is,
        for fields the connector does not know about yet.
        """
        payload = contract.chat_payload(
            messages,
            self._model(model),
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            extra=extra,
        )
        started = time.perf_counter()
        data, headers = self._transport.request_json("POST", contract.CHAT_PATH, payload)
        return contract.parse_chat(data, headers=headers, latency_s=time.perf_counter() - started)

    def chat_stream(
        self,
        messages: str | Sequence[Message],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        include_usage: bool = True,
        extra: Mapping[str, Any] | None = None,
    ) -> Generator[ChatChunk, None, None]:
        """Same as chat(), but yields the reply piece by piece as the server produces it.

        Join chunk.text for the full reply. The last chunks carry finish_reason and,
        when the server reports it, usage.
        """
        payload = contract.chat_payload(
            messages,
            self._model(model),
            system=system,
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
            include_usage=include_usage,
            extra=extra,
        )
        return self._stream(payload)

    def _stream(self, payload: dict[str, Any]) -> Generator[ChatChunk, None, None]:
        events = self._transport.stream_events(contract.CHAT_PATH, payload)
        seen = False
        try:
            for event in events:
                seen = True
                if event == contract.STREAM_DONE:
                    break
                chunk = contract.parse_stream_event(event)
                if chunk.text or chunk.finish_reason or chunk.usage:
                    yield chunk
        finally:
            events.close()
        if not seen:
            # A server that ignores "stream": true would otherwise look like an empty reply.
            raise FlexGridResponseError(
                "The stream carried no data events. The endpoint may not support streaming under the "
                "assumed OpenAI-compatible contract. See FLEXGRID_CONNECTOR_SPEC.md."
            )

    def _model(self, model: str | None) -> str:
        chosen = model or self.config.model
        if not chosen:
            raise FlexGridConfigError("No model given. Set FLEXGRID_MODEL or pass model=...")
        return chosen


_END = object()


class AsyncFlexGridClient:
    """Async flavour of FlexGridClient, same methods and arguments.

    Each call runs the blocking client in a worker thread. Cancelling the awaiting
    task stops the wait straight away; the request itself ends when the server
    answers or the timeout expires.
    """

    def __init__(self, config: FlexGridConfig | None = None):
        self._sync = FlexGridClient(config)
        self.config = self._sync.config

    async def list_models(self) -> list[str]:
        return await asyncio.to_thread(self._sync.list_models)

    async def describe_models(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync.describe_models)

    async def chat(self, messages: str | Sequence[Message], **kwargs: Any) -> ChatResult:
        return await asyncio.to_thread(functools.partial(self._sync.chat, messages, **kwargs))

    def chat_stream(self, messages: str | Sequence[Message], **kwargs: Any) -> AsyncIterator[ChatChunk]:
        # Built here, not inside the generator, so a bad argument raises at the call.
        chunks = self._sync.chat_stream(messages, **kwargs)
        return self._bridge(chunks)

    async def _bridge(self, chunks: Generator[ChatChunk, None, None]) -> AsyncIterator[ChatChunk]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        stop = threading.Event()

        def deliver(item: Any) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                # The loop is gone, so nobody is listening any more.
                stop.set()

        def pump() -> None:
            try:
                for chunk in chunks:
                    if stop.is_set():
                        return
                    deliver(chunk)
                deliver(_END)
            except BaseException as exc:  # handed to the consumer, which re-raises it
                deliver(exc)
            finally:
                chunks.close()

        pumping = loop.run_in_executor(None, pump)
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stop.set()
            pumping.cancel()
