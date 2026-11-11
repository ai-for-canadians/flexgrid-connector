import pytest

from flexgrid_connector import FlexGridClient, FlexGridConfig, FlexGridConfigError
from flexgrid_connector.mock_server import MockFlexGridServer


def test_list_models(client):
    assert client.list_models() == ["mock-small", "mock-large"]


def test_describe_models_keeps_whatever_the_endpoint_reports(client, mock):
    mock.model_extras = {"some_limit": 4096}
    cards = client.describe_models()
    assert [card["id"] for card in cards] == ["mock-small", "mock-large"]
    assert all(card["some_limit"] == 4096 for card in cards)


def test_chat_round_trip(client, mock):
    result = client.chat("hello there")

    assert "You said: hello there" in result.text
    assert result.model == "mock-small"
    assert result.finish_reason == "stop"
    assert result.usage.completion_tokens == len(result.text.split())
    assert result.usage.total_tokens == result.usage.prompt_tokens + result.usage.completion_tokens
    assert result.latency_s > 0

    sent = mock.requests[-1]
    assert (sent.method, sent.path) == ("POST", "/v1/chat/completions")
    assert sent.body["model"] == "mock-small"
    assert sent.body["messages"] == [{"role": "user", "content": "hello there"}]
    assert sent.body["stream"] is False


def test_raw_body_and_headers_are_kept_for_anything_the_contract_does_not_cover(client):
    result = client.chat("hi")
    assert result.raw["object"] == "chat.completion"
    assert result.headers["x-mock-flexgrid"] == "1"


def test_message_list_and_system_prompt_go_through_in_order(client, mock):
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]
    result = client.chat(history, system="be brief")

    assert "You said: second" in result.text
    assert mock.requests[-1].body["messages"] == [{"role": "system", "content": "be brief"}, *history]


def test_model_can_be_overridden_per_call(client, mock):
    assert client.chat("hi", model="mock-large").model == "mock-large"
    assert mock.requests[-1].body["model"] == "mock-large"


def test_generation_options_are_forwarded(client, mock):
    result = client.chat("hi", max_tokens=5, temperature=0.2, extra={"top_p": 0.9})

    body = mock.requests[-1].body
    assert (body["max_tokens"], body["temperature"], body["top_p"]) == (5, 0.2, 0.9)
    assert result.finish_reason == "length"
    assert len(result.text.split()) == 5


def test_unset_options_are_left_out_of_the_request(client, mock):
    client.chat("hi")
    assert not {"max_tokens", "temperature", "stream_options"} & set(mock.requests[-1].body)


def test_api_key_is_sent_as_a_bearer_token():
    with MockFlexGridServer(api_key="k-123") as mock:
        client = FlexGridClient(FlexGridConfig(base_url=mock.base_url, model="mock-small", api_key="k-123"))
        assert client.chat("hi").text
        assert mock.requests[-1].headers["authorization"] == "Bearer k-123"


def test_no_api_key_means_no_authorization_header(client, mock):
    client.chat("hi")
    assert "authorization" not in mock.requests[-1].headers


def test_client_reads_its_settings_from_the_environment(mock, monkeypatch):
    monkeypatch.setenv("FLEXGRID_BASE_URL", mock.base_url)
    monkeypatch.setenv("FLEXGRID_MODEL", "mock-large")
    assert FlexGridClient().chat("hi").model == "mock-large"


def test_client_without_a_url_refuses_to_start():
    with pytest.raises(FlexGridConfigError, match="FLEXGRID_BASE_URL is not set"):
        FlexGridClient()


def test_no_model_anywhere_is_a_config_error(mock):
    client = FlexGridClient(FlexGridConfig(base_url=mock.base_url))
    with pytest.raises(FlexGridConfigError, match="FLEXGRID_MODEL"):
        client.chat("hi")
    assert mock.requests == []


@pytest.mark.parametrize("bad", [[], [{"role": "user"}], ["just a string"]])
def test_malformed_messages_are_rejected_before_anything_is_sent(client, mock, bad):
    with pytest.raises(ValueError):
        client.chat(bad)
    assert mock.requests == []


def test_ambient_proxy_settings_are_ignored(client, monkeypatch, dead_url):
    # Inference traffic goes straight to the configured host, never through a proxy from the environment.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, dead_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert "You said: hi" in client.chat("hi").text
