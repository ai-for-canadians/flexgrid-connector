"""Failure behaviour: every way the endpoint can let us down raises a typed error, quickly."""
import http.client
import socket
import ssl
import time

import pytest

from flexgrid_connector import (
    FlexGridAPIError,
    FlexGridAuthError,
    FlexGridClient,
    FlexGridConfig,
    FlexGridConnectionError,
    FlexGridError,
    FlexGridResponseError,
    FlexGridTimeoutError,
    FlexGridUnreachableError,
)
from flexgrid_connector.mock_server import MockFlexGridServer
from flexgrid_connector.transport import _connect_failure


def test_unreachable_endpoint_raises_fast_and_says_so(dead_url):
    client = FlexGridClient(FlexGridConfig(base_url=dead_url, model="any", timeout=30))

    started = time.perf_counter()
    with pytest.raises(FlexGridUnreachableError) as caught:
        client.chat("hi")

    assert time.perf_counter() - started < 3
    message = str(caught.value)
    assert dead_url in message
    assert "does not fall back" in message
    assert isinstance(caught.value, FlexGridConnectionError)
    assert isinstance(caught.value, FlexGridError)


def test_every_entry_point_reports_unreachable_the_same_way(dead_url):
    client = FlexGridClient(FlexGridConfig(base_url=dead_url, model="any"))
    with pytest.raises(FlexGridUnreachableError):
        client.list_models()
    with pytest.raises(FlexGridUnreachableError):
        list(client.chat_stream("hi"))


def test_connect_attempt_uses_the_short_timeout_not_the_read_timeout(monkeypatch):
    # A black-holed address never answers. Patched here so the suite sends no packets off the machine.
    attempts = []

    def never_connects(conn):
        attempts.append(conn.timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", never_connects)
    client = FlexGridClient(FlexGridConfig(base_url="http://box.example:8000/v1", model="any", timeout=30))

    with pytest.raises(FlexGridUnreachableError, match="no answer to the connection attempt within 5s"):
        client.chat("hi")
    assert attempts == [5.0]


def test_silent_server_times_out_instead_of_hanging(mock):
    mock.delay_s = 2.0
    client = FlexGridClient(FlexGridConfig(base_url=mock.base_url, model="mock-small", timeout=0.3))

    started = time.perf_counter()
    with pytest.raises(FlexGridTimeoutError, match="FLEXGRID_TIMEOUT") as caught:
        client.chat("hi")

    assert time.perf_counter() - started < 1.5
    assert isinstance(caught.value, FlexGridConnectionError)
    assert not isinstance(caught.value, FlexGridUnreachableError)


def test_stream_that_stalls_midway_times_out():
    with MockFlexGridServer(tokens_per_sec=0.5) as mock:  # two seconds between tokens
        client = FlexGridClient(FlexGridConfig(base_url=mock.base_url, model="mock-small", timeout=0.3))
        started = time.perf_counter()
        with pytest.raises(FlexGridTimeoutError):
            list(client.chat_stream("hi"))
        assert time.perf_counter() - started < 1.5


def test_dropped_connection_is_a_connection_error(client, mock):
    mock.drop_connection = True
    with pytest.raises(FlexGridConnectionError) as caught:
        client.chat("hi")
    assert type(caught.value) is FlexGridConnectionError
    assert "dropped" in str(caught.value)


def test_server_error_carries_status_and_body(client, mock):
    mock.fail_status = 500
    with pytest.raises(FlexGridAPIError) as caught:
        client.chat("hi")
    assert caught.value.status_code == 500
    assert "injected failure" in str(caught.value)
    assert "injected failure" in caught.value.body


def test_wrong_or_missing_key_is_an_auth_error():
    with MockFlexGridServer(api_key="right") as mock:
        for key in (None, "wrong"):
            client = FlexGridClient(FlexGridConfig(base_url=mock.base_url, model="mock-small", api_key=key))
            with pytest.raises(FlexGridAuthError) as caught:
                client.chat("hi")
            assert caught.value.status_code == 401
            assert isinstance(caught.value, FlexGridAPIError)


def test_unknown_model_names_the_model(client):
    with pytest.raises(FlexGridAPIError) as caught:
        client.chat("hi", model="not-deployed")
    assert caught.value.status_code == 404
    assert "not-deployed" in str(caught.value)


@pytest.mark.parametrize("kind", ["shape", "not-json"])
def test_ok_status_with_the_wrong_body_points_at_the_spec(client, mock, kind):
    mock.malformed = kind
    with pytest.raises(FlexGridResponseError, match="FLEXGRID_CONNECTOR_SPEC.md"):
        client.chat("hi")


def test_redirects_are_reported_not_followed(client, mock):
    mock.fail_status = 302
    with pytest.raises(FlexGridAPIError, match="redirects are not followed") as caught:
        client.chat("hi")
    assert caught.value.status_code == 302


def test_error_messages_never_include_the_api_key(dead_url):
    client = FlexGridClient(FlexGridConfig(base_url=dead_url, model="any", api_key="k-secret"))
    with pytest.raises(FlexGridUnreachableError) as caught:
        client.chat("hi")
    assert "k-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (socket.gaierror(-2, "Name or service not known"), "did not resolve"),
        (TimeoutError("timed out"), "within 5s"),
        (ConnectionRefusedError(111, "Connection refused"), "connection refused"),
        (ssl.SSLError(1, "handshake failure"), "TLS handshake failed"),
        (OSError(101, "Network is unreachable"), "Network is unreachable"),
    ],
)
def test_connect_failures_are_explained_in_plain_words(exc, expected):
    assert expected in _connect_failure(exc, 5.0)
