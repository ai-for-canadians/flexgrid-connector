"""AsyncFlexGridClient. Plain asyncio.run, so the suite needs no async test plugin."""
import asyncio
import time

import pytest

from flexgrid_connector import (
    AsyncFlexGridClient,
    FlexGridAPIError,
    FlexGridConfig,
    FlexGridConfigError,
    FlexGridUnreachableError,
)


@pytest.fixture
def aclient(config):
    return AsyncFlexGridClient(config)


def test_async_chat_and_models(aclient):
    async def scenario():
        cards = await aclient.describe_models()
        return await aclient.list_models(), cards, await aclient.chat("hello there", system="be brief")

    models, cards, result = asyncio.run(scenario())
    assert models == ["mock-small", "mock-large"] == [card["id"] for card in cards]
    assert "You said: hello there" in result.text
    assert result.usage.completion_tokens > 0


def test_async_stream_matches_chat(aclient):
    async def scenario():
        chunks = [chunk async for chunk in aclient.chat_stream("hello there")]
        return chunks, await aclient.chat("hello there")

    chunks, whole = asyncio.run(scenario())
    assert "".join(chunk.text for chunk in chunks) == whole.text
    assert chunks[-1].usage.completion_tokens == whole.usage.completion_tokens


def test_async_calls_run_side_by_side(aclient, mock):
    mock.delay_s = 0.3

    async def scenario():
        return await asyncio.gather(*(aclient.chat(f"message {i}") for i in range(3)))

    started = time.perf_counter()
    results = asyncio.run(scenario())
    assert [f"You said: message {i}" in r.text for i, r in enumerate(results)] == [True] * 3
    assert time.perf_counter() - started < 0.8  # three in a row would take 0.9s


def test_async_errors_are_the_same_typed_errors(dead_url, aclient, mock):
    async def unreachable():
        await AsyncFlexGridClient(FlexGridConfig(base_url=dead_url, model="any")).chat("hi")

    with pytest.raises(FlexGridUnreachableError):
        asyncio.run(unreachable())

    mock.fail_status = 500

    async def failing_stream():
        return [chunk async for chunk in aclient.chat_stream("hi")]

    with pytest.raises(FlexGridAPIError):
        asyncio.run(failing_stream())


def test_async_bad_arguments_fail_at_the_call(mock):
    client = AsyncFlexGridClient(FlexGridConfig(base_url=mock.base_url))

    async def scenario():
        client.chat_stream("hi")

    with pytest.raises(FlexGridConfigError):
        asyncio.run(scenario())


def test_leaving_an_async_stream_early_does_not_hang(aclient):
    async def scenario():
        async for chunk in aclient.chat_stream("hello there"):
            if chunk.text:
                break
        return await aclient.chat("again")

    assert "You said: again" in asyncio.run(scenario()).text


def test_cancelling_stops_the_wait_straight_away(aclient, mock):
    mock.delay_s = 1.0

    async def scenario():
        await asyncio.wait_for(aclient.chat("hi"), timeout=0.2)

    started = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(scenario())
    # asyncio.run waits for the worker thread on shutdown, so allow for the mock's one-second delay.
    assert time.perf_counter() - started < 2.5
