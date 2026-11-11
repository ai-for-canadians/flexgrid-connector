"""The assumed wire contract, as pure functions. When the real ARC contract lands, these change with it."""
import pytest

from flexgrid_connector import FlexGridResponseError, contract


def test_chat_payload_is_minimal_by_default():
    assert contract.chat_payload("hi", "m") == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }


def test_stream_payload_asks_for_usage():
    payload = contract.chat_payload("hi", "m", stream=True)
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


def test_extra_fields_win_over_built_in_ones():
    payload = contract.chat_payload("hi", "m", max_tokens=10, extra={"max_tokens": 20, "session": "abc"})
    assert (payload["max_tokens"], payload["session"]) == (20, "abc")


def test_headers():
    assert contract.request_headers(None, stream=False) == {"Accept": "application/json"}
    assert contract.request_headers("k", stream=True) == {
        "Accept": "text/event-stream",
        "Authorization": "Bearer k",
    }


def test_null_content_becomes_empty_text():
    data = {"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}]}
    result = contract.parse_chat(data, headers={}, latency_s=0.1)
    assert (result.text, result.finish_reason, result.usage) == ("", "length", None)


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        "text",
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ["parts"]}}]},
    ],
)
def test_reply_in_the_wrong_shape_is_a_response_error(data):
    with pytest.raises(FlexGridResponseError, match="assumed OpenAI-compatible contract"):
        contract.parse_chat(data, headers={}, latency_s=0.1)


def test_usage_ignores_values_that_are_not_counts():
    data = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": "many", "total_tokens": True},
    }
    usage = contract.parse_chat(data, headers={}, latency_s=0.1).usage
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (3, None, None)


def test_stream_event_variants():
    text = contract.parse_stream_event('{"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}')
    assert (text.text, text.finish_reason) == ("hi", None)

    done = contract.parse_stream_event('{"choices": [{"delta": {}, "finish_reason": "stop"}]}')
    assert (done.text, done.finish_reason) == ("", "stop")

    usage = contract.parse_stream_event('{"choices": [], "usage": {"completion_tokens": 7}}')
    assert usage.usage.completion_tokens == 7


@pytest.mark.parametrize("payload", ["not json", "[1, 2]", '{"error": {"message": "card fell over"}}'])
def test_bad_stream_events_are_response_errors(payload):
    with pytest.raises(FlexGridResponseError):
        contract.parse_stream_event(payload)


def test_mid_stream_error_keeps_the_server_message():
    with pytest.raises(FlexGridResponseError, match="card fell over"):
        contract.parse_stream_event('{"error": {"message": "card fell over"}}')


def test_models():
    assert contract.parse_models({"data": [{"id": "a"}, {"id": "b", "limit": 8}]}) == [
        {"id": "a"},
        {"id": "b", "limit": 8},
    ]
    for bad in ({}, {"data": [{}]}, {"data": 3}, {"data": ["a"]}, []):
        with pytest.raises(FlexGridResponseError):
            contract.parse_models(bad)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"error": {"message": "nope"}}', "nope"),
        ('{"error": "nope"}', "nope"),
        ('{"detail": "nope"}', "nope"),
        ('{"message": "nope"}', "nope"),
        ("<html>\n  Bad   Gateway\n</html>", "<html> Bad Gateway </html>"),
        ("", "no error body"),
    ],
)
def test_error_message_finds_the_readable_part(body, expected):
    assert contract.error_message(body) == expected
