import pytest

from flexgrid_connector import (
    FlexGridAPIError,
    FlexGridClient,
    FlexGridConfig,
    FlexGridConfigError,
    FlexGridResponseError,
)


def test_stream_adds_up_to_the_same_reply_as_chat(client):
    chunks = list(client.chat_stream("hello there"))

    assert "".join(chunk.text for chunk in chunks) == client.chat("hello there").text
    assert sum(1 for chunk in chunks if chunk.text) > 10  # really piece by piece


def test_stream_request_asks_for_a_stream(client, mock):
    list(client.chat_stream("hi"))
    body = mock.requests[-1].body
    assert body["stream"] is True
    assert mock.requests[-1].headers["accept"] == "text/event-stream"


def test_stream_ends_with_finish_reason_and_usage(client):
    chunks = list(client.chat_stream("hello there"))

    assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == ["stop"]
    assert chunks[-1].usage.completion_tokens == sum(1 for chunk in chunks if chunk.text)


def test_usage_can_be_turned_off(client, mock):
    chunks = list(client.chat_stream("hi", include_usage=False))

    assert "stream_options" not in mock.requests[-1].body
    assert all(chunk.usage is None for chunk in chunks)


def test_stream_honours_max_tokens(client):
    chunks = list(client.chat_stream("hi", max_tokens=4))
    assert len("".join(chunk.text for chunk in chunks).split()) == 4
    assert chunks[-2].finish_reason == "length"


def test_stopping_early_leaves_the_client_usable(client):
    stream = client.chat_stream("hello there")
    assert next(stream).text
    stream.close()
    assert "You said: again" in client.chat("again").text


def test_error_status_on_a_stream_is_typed(client, mock):
    mock.fail_status = 503
    with pytest.raises(FlexGridAPIError) as caught:
        list(client.chat_stream("hi"))
    assert caught.value.status_code == 503


def test_a_reply_that_is_not_a_stream_fails_instead_of_looking_empty(client, mock):
    mock.malformed = "shape"
    with pytest.raises(FlexGridResponseError, match="no data events"):
        list(client.chat_stream("hi"))


def test_bad_arguments_fail_at_the_call_not_at_first_iteration(mock):
    client = FlexGridClient(FlexGridConfig(base_url=mock.base_url))
    with pytest.raises(FlexGridConfigError):
        client.chat_stream("hi")
