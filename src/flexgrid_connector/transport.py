"""HTTP over the standard library, with every failure mapped to a typed error.

Two deliberate choices:

* No third-party HTTP client. The connector has to install on an air-gapped box.
* Connections go straight to the configured host. http.client ignores HTTP_PROXY
  and HTTPS_PROXY, so on-prem inference traffic cannot be routed through an
  ambient proxy by accident. Redirects are not followed for the same reason.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
from collections.abc import Generator
from typing import Any
from urllib.parse import urlsplit

from . import contract
from .config import FlexGridConfig
from .errors import (
    FlexGridAPIError,
    FlexGridAuthError,
    FlexGridConnectionError,
    FlexGridResponseError,
    FlexGridTimeoutError,
    FlexGridUnreachableError,
)

_NO_FALLBACK = "The connector does not fall back to any other provider."


class Transport:
    """One short-lived connection per call. Simple, and a LAN hop makes reuse a small win."""

    def __init__(self, config: FlexGridConfig):
        self._config = config
        parts = urlsplit(config.base_url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or ""
        self._port = parts.port or (443 if self._https else 80)
        self._prefix = parts.path

    def request_json(self, method: str, path: str, body: Any = None) -> tuple[Any, dict[str, str]]:
        """Send one request, return (decoded JSON body, response headers with lower-case names)."""
        conn = self._connect()
        try:
            response = self._send(conn, method, path, body, stream=False)
            text = self._read(response)
        finally:
            conn.close()
        if response.status >= 300:
            raise self._api_error(response, text)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise FlexGridResponseError(
                f"FlexGrid returned HTTP {response.status} with a body that is not JSON. "
                "This does not match the assumed OpenAI-compatible contract. See FLEXGRID_CONNECTOR_SPEC.md."
            ) from exc
        return data, _headers(response)

    def stream_events(self, path: str, body: Any) -> Generator[str, None, None]:
        """POST, then yield the payload of each server-sent `data:` line as it arrives."""
        conn = self._connect()
        try:
            response = self._send(conn, "POST", path, body, stream=True)
            if response.status >= 300:
                raise self._api_error(response, self._read(response))
            while True:
                try:
                    raw = response.readline()
                except TimeoutError as exc:
                    raise FlexGridTimeoutError(self._timeout_message("mid-stream")) from exc
                except (http.client.HTTPException, OSError) as exc:
                    raise FlexGridConnectionError(self._dropped_message(exc)) from exc
                if not raw:
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("data:"):
                    yield line[5:].strip()
        finally:
            conn.close()

    # --- internals ---------------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        config = self._config
        connect_timeout = min(config.connect_timeout, config.timeout)
        conn: http.client.HTTPConnection
        if self._https:
            conn = http.client.HTTPSConnection(
                self._host, self._port, timeout=connect_timeout, context=ssl.create_default_context()
            )
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=connect_timeout)
        try:
            conn.connect()
        except OSError as exc:
            conn.close()
            raise FlexGridUnreachableError(
                f"FlexGrid endpoint is unreachable at {config.safe_url}: "
                f"{_connect_failure(exc, connect_timeout)}. {_NO_FALLBACK}"
            ) from exc
        conn.sock.settimeout(config.timeout)
        return conn

    def _send(
        self, conn: http.client.HTTPConnection, method: str, path: str, body: Any, *, stream: bool
    ) -> http.client.HTTPResponse:
        headers = contract.request_headers(self._config.api_key, stream=stream)
        headers["Connection"] = "close"
        headers["User-Agent"] = "flexgrid-connector"
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, self._prefix + path, body=payload, headers=headers)
            return conn.getresponse()
        except TimeoutError as exc:
            raise FlexGridTimeoutError(self._timeout_message("waiting for a reply")) from exc
        except (http.client.HTTPException, OSError) as exc:
            raise FlexGridConnectionError(self._dropped_message(exc)) from exc

    def _read(self, response: http.client.HTTPResponse) -> str:
        try:
            return response.read().decode("utf-8", errors="replace")
        except TimeoutError as exc:
            raise FlexGridTimeoutError(self._timeout_message("mid-reply")) from exc
        except (http.client.HTTPException, OSError) as exc:
            raise FlexGridConnectionError(self._dropped_message(exc)) from exc

    def _api_error(self, response: http.client.HTTPResponse, text: str) -> FlexGridAPIError:
        status = response.status
        if 300 <= status < 400:
            location = response.getheader("Location") or "an unknown location"
            advice = "Set FLEXGRID_BASE_URL to the final URL; redirects are not followed."
            return FlexGridAPIError(status, f"redirect to {location}. {advice}", text)
        cls = FlexGridAuthError if status in (401, 403) else FlexGridAPIError
        return cls(status, contract.error_message(text), text)

    def _timeout_message(self, when: str) -> str:
        return (
            f"FlexGrid at {self._config.safe_url} sent nothing for {self._config.timeout:g}s ({when}). "
            f"Raise FLEXGRID_TIMEOUT if the model needs longer. {_NO_FALLBACK}"
        )

    def _dropped_message(self, exc: BaseException) -> str:
        detail = str(exc) or type(exc).__name__
        return f"The connection to FlexGrid at {self._config.safe_url} dropped: {detail}. {_NO_FALLBACK}"


def _headers(response: http.client.HTTPResponse) -> dict[str, str]:
    return {name.lower(): value for name, value in response.getheaders()}


def _connect_failure(exc: OSError, connect_timeout: float) -> str:
    if isinstance(exc, socket.gaierror):
        return "the host name did not resolve"
    if isinstance(exc, TimeoutError):
        return f"no answer to the connection attempt within {connect_timeout:g}s"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused, nothing is listening on that port"
    if isinstance(exc, ssl.SSLError):
        return f"the TLS handshake failed ({getattr(exc, 'reason', None) or exc})"
    return exc.strerror or str(exc) or type(exc).__name__
