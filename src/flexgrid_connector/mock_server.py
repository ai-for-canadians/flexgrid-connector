"""A stand-in for a FlexGrid endpoint, so everything runs with no hardware and no network.

It mimics the ASSUMED contract in contract.py, not the real ARC. A green run
against this mock proves the connector is internally consistent. It says nothing
about real FlexGrid hardware.

    python -m flexgrid_connector.mock_server --port 8089 --tps 105
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Named so nobody mistakes them for real ARC model IDs, which we do not know yet.
DEFAULT_MODELS = ("mock-small", "mock-large")

CANNED = (
    "This is canned text from the mock FlexGrid server, not a real model. It exists so the "
    "connector, the probe and the tests can run with no hardware and no network."
)


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


def reply_for(model: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic reply: echoes the last user message so tests can assert on it."""
    last_user = next((str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), "")
    return f"[{model}] You said: {last_user} | {CANNED}"


class MockFlexGridServer:
    """In-process mock. Use as a context manager, or call start() and stop().

    The attributes below can be changed while it runs. Tests use them to inject failures.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        models: tuple[str, ...] = DEFAULT_MODELS,
        api_key: str | None = None,
        tokens_per_sec: float | None = None,
        verbose: bool = False,
    ):
        self.models = list(models)
        # When set, requests must carry "Authorization: Bearer <api_key>".
        self.api_key = api_key
        # Paces replies to simulate generation speed. None means no delay.
        self.tokens_per_sec = tokens_per_sec
        # Answer every request with this HTTP status.
        self.fail_status: int | None = None
        # Stay silent this long before answering, to trip client timeouts.
        self.delay_s = 0.0
        # Answer chat with HTTP 200 and a body that breaks the contract:
        # "shape" is JSON with the wrong fields, "not-json" is plain text.
        self.malformed: str | None = None
        # Hang up without answering.
        self.drop_connection = False
        # Extra fields to report on every model in the list.
        self.model_extras: dict[str, Any] = {}
        self.verbose = verbose
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer((host, port), _handler_for(self))
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return str(self._httpd.server_address[0])

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def start(self) -> MockFlexGridServer:
        # The default 0.5s poll makes every stop() wait half a second, which adds up across a test suite.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, name="mock-flexgrid", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def record(self, request: RecordedRequest) -> None:
        with self._lock:
            self.requests.append(request)

    def __enter__(self) -> MockFlexGridServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _handler_for(mock: MockFlexGridServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "MockFlexGrid"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            if mock.verbose:
                super().log_message(format, *args)

        def do_GET(self) -> None:
            self._handle(None)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                self._record(None)
                self._error(400, "request body is not JSON", "invalid_request_error")
                return
            self._handle(body)

        # --- routing -------------------------------------------------------

        def _handle(self, body: Any) -> None:
            self._record(body)
            try:
                if mock.delay_s:
                    time.sleep(mock.delay_s)
                if mock.drop_connection:
                    self.close_connection = True
                    return
                if mock.api_key and self.headers.get("Authorization") != f"Bearer {mock.api_key}":
                    self._error(401, "missing or wrong API key", "authentication_error")
                elif mock.fail_status:
                    self._error(mock.fail_status, "injected failure from the mock", "server_error")
                elif self.command == "GET" and self.path == "/v1/models":
                    cards = [{**_model_card(m), **mock.model_extras} for m in mock.models]
                    self._json(200, {"object": "list", "data": cards})
                elif self.command == "POST" and self.path == "/v1/chat/completions":
                    self._chat(body)
                else:
                    self._error(404, f"no route for {self.command} {self.path}", "not_found")
            except (BrokenPipeError, ConnectionResetError):
                # The client hung up, usually a test tripping its own timeout.
                self.close_connection = True

        def _chat(self, body: Any) -> None:
            messages = body.get("messages") if isinstance(body, dict) else None
            if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
                self._error(400, "'messages' must be a non-empty list of objects", "invalid_request_error")
                return
            model = body.get("model")
            if model not in mock.models:
                self._error(404, f"The model `{model}` does not exist", "invalid_request_error", "model_not_found")
                return
            if mock.malformed == "not-json":
                self._send(200, "text/plain", b"this is not JSON")
                return
            if mock.malformed:
                self._json(200, {"unexpected": "shape"})
                return

            words = reply_for(model, body["messages"]).split()
            finish_reason = "stop"
            max_tokens = body.get("max_tokens")
            if isinstance(max_tokens, int) and 0 < max_tokens < len(words):
                words, finish_reason = words[:max_tokens], "length"
            prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in body["messages"])
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": len(words),
                "total_tokens": prompt_tokens + len(words),
            }
            head = {"id": f"mock-{len(mock.requests)}", "created": int(time.time()), "model": model}
            pause = 1.0 / mock.tokens_per_sec if mock.tokens_per_sec else 0.0

            if not body.get("stream"):
                time.sleep(pause * len(words))
                message = {"role": "assistant", "content": " ".join(words)}
                self._json(
                    200,
                    {
                        **head,
                        "object": "chat.completion",
                        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                        "usage": usage,
                    },
                )
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-Mock-FlexGrid", "1")
            self.end_headers()

            def event(choices: list[dict[str, Any]], **more: Any) -> None:
                payload = {**head, "object": "chat.completion.chunk", "choices": choices, **more}
                self._chunk(f"data: {json.dumps(payload)}\n\n".encode())

            event([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])
            for i, word in enumerate(words):
                time.sleep(pause)
                piece = word if i == 0 else f" {word}"
                event([{"index": 0, "delta": {"content": piece}, "finish_reason": None}])
            event([{"index": 0, "delta": {}, "finish_reason": finish_reason}])
            if (body.get("stream_options") or {}).get("include_usage"):
                event([], usage=usage)
            self._chunk(b"data: [DONE]\n\n")
            self._chunk(b"")

        # --- plumbing ------------------------------------------------------

        def _record(self, body: Any) -> None:
            headers = {name.lower(): value for name, value in self.headers.items()}
            mock.record(RecordedRequest(self.command, self.path, headers, body))

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(status, "application/json", json.dumps(payload).encode())

        def _send(self, status: int, content_type: str, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Mock-FlexGrid", "1")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str, kind: str, code: str | None = None) -> None:
            self._json(status, {"error": {"message": message, "type": kind, "code": code}})

        def _chunk(self, data: bytes) -> None:
            # HTTP chunked framing. An empty chunk ends the body.
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

    return Handler


def _model_card(model_id: str) -> dict[str, Any]:
    return {"id": model_id, "object": "model", "owned_by": "mock-flexgrid"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flexgrid-mock",
        description="Local stand-in for a FlexGrid endpoint. Mimics the assumed contract, not the real ARC.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: loopback only)")
    parser.add_argument("--port", type=int, default=8089, help="port to bind, 0 picks a free one (default: 8089)")
    parser.add_argument("--tps", type=float, default=None, help="simulate this many tokens per second")
    parser.add_argument("--api-key", default=None, help="require this bearer token")
    parser.add_argument("--verbose", action="store_true", help="log each request")
    args = parser.parse_args(argv)

    mock = MockFlexGridServer(
        host=args.host, port=args.port, api_key=args.api_key, tokens_per_sec=args.tps, verbose=args.verbose
    )
    print(f"Mock FlexGrid listening on {mock.base_url}", flush=True)
    print("This is the assumed contract, not the real ARC.", flush=True)
    print(f"Try:  FLEXGRID_BASE_URL={mock.base_url} FLEXGRID_MODEL={mock.models[0]} flexgrid-probe", flush=True)
    try:
        mock.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
