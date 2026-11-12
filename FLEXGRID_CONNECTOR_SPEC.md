# FlexGrid Connector Spec

Version 0.1.0.

**Status in one line:** the connector, the mock and the probe work end to end. The wire contract they speak is an assumption. Nothing here has been run against FlexGrid hardware yet.


## 1. What this is

A small Python client that lets an app send chat requests to FlexGrid's on-prem hardware instead of a cloud API.

Three pieces ship together:

- `flexgrid_connector`: the client. `chat()`, `chat_stream()`, `list_models()`, typed errors.
- `flexgrid-probe`: a readiness report for any endpoint URL.
- `flexgrid-mock`: a local stand-in for the endpoint, so tests run with no hardware and no network.


## 2. Background

FlexGrid runs open-weight models on Furiosa NPUs, on-prem. Apps reach the models through FlexGrid's orchestration layer, **ARC**: an app tells ARC which model it wants and ARC routes it there.

The open questions in section 9 cover what is not public yet: ARC's API contract, how a model is named in a request, how KV-cache ownership shows up to the caller, and how a new app is onboarded.


## 4. The assumed contract

**ASSUMPTION, not confirmed by FlexGrid: ARC exposes an OpenAI-compatible chat API.**

Why this is the default guess:

- It is the usual shape for servers that run open-weight models.
- Furiosa's public SDK docs describe an OpenAI-compatible server with `/v1/chat/completions` and `/v1/models`. FlexGrid runs Furiosa NPUs and builds models with Furiosa on the SDK.
  Source: https://developer.furiosa.ai/latest/en/furiosa_llm/furiosa-llm-serve.html (SDK 2026.4.1, read 2026-11-10).

Why it could still be wrong:

- That page describes Furiosa's own server, not ARC. It says the server hosts a single model and ignores the `model` field in a request.
- ARC is the part that picks a model across the network. So ARC may pass the Furiosa API through, wrap it, or replace it. We do not know which.

What the connector assumes, piece by piece:

| Piece | Assumed | Where it lives |
|---|---|---|
| Base URL | `scheme://host:port/v1`, version prefix included | `FLEXGRID_BASE_URL` |
| Chat | `POST {base}/chat/completions` | `contract.CHAT_PATH` |
| Model list | `GET {base}/models` returns `{"data": [{"id": ...}]}` | `contract.MODELS_PATH`, `contract.parse_models` |
| Picking a model | `"model": "<id>"` in the request body | `contract.chat_payload` |
| Auth | none, or `Authorization: Bearer <key>` when a key is set | `contract.request_headers` |
| Request body | `model`, `messages` (each with `role` and `content`), `stream`, optional `max_tokens` and `temperature` | `contract.chat_payload` |
| Reply | `choices[0].message.content`, `choices[0].finish_reason`, optional `usage` | `contract.parse_chat` |
| Streaming | server-sent events. `data: {json}` lines with `choices[0].delta.content`, ending in `data: [DONE]` | `contract.parse_stream_event` |
| Usage on a stream | sent on the last event when the request sets `stream_options.include_usage` | `contract.chat_payload` |
| Errors | HTTP status 400 or above, message in `error.message` | `contract.error_message` |

All of it sits in one file: `src/flexgrid_connector/contract.py`.

What changes once FlexGrid answers:

| If ARC turns out to be | Then |
|---|---|
| OpenAI-compatible as assumed | Set the env vars and run the probe. No code change. |
| OpenAI-compatible with differences (paths, auth header, model naming) | Edit `contract.py`. |
| A different HTTP API | Rewrite `contract.py`. `transport.py` stays. |
| gRPC or SDK only | Replace `contract.py` and `transport.py`. The public client and the error types stay, so apps do not change. This would add a dependency. |
| Exposing a KV-cache or session handle | Add it to `ChatResult` and to `chat()`. Until then it is already visible in `ChatResult.raw` and `ChatResult.headers`. |


## 5. Configuration

Every environment variable the connector reads:

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `FLEXGRID_BASE_URL` | yes | none | Endpoint, including the version prefix. Shape: `http://host:port/v1`. |
| `FLEXGRID_MODEL` | for chat | none | Model ID to request. Can be overridden per call. The probe runs without it and uses the first model listed. |
| `FLEXGRID_API_KEY` | no | none | Sent as a bearer token when set. Left out when not. |
| `FLEXGRID_TIMEOUT` | no | `60` | Seconds of silence the client accepts before giving up. See section 7. |

There are no others. In particular:

- No URL or credential is hardcoded anywhere.
- `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` are ignored on purpose. Requests go straight to the configured host.
- The connect timeout is 5 seconds. It is a code setting (`FlexGridConfig.connect_timeout`), not an env var.

Apps that use the connector can add settings of their own, for example a switch between a cloud provider and FlexGrid. The connector does not read them.


## 6. Client surface

```python
from flexgrid_connector import FlexGridClient, FlexGridError

client = FlexGridClient()                 # reads FLEXGRID_* from the environment

reply = client.chat("Hello", system="Be brief.")
print(reply.text, reply.usage, reply.latency_s)

for chunk in client.chat_stream("Hello"):
    print(chunk.text, end="")

print(client.list_models())
```

- `chat(messages, *, system, model, max_tokens, temperature, extra)` returns a `ChatResult`.
- `chat_stream(...)` takes the same arguments and yields `ChatChunk` objects.
- `list_models()` returns model IDs. `describe_models()` returns the full entries as the endpoint sent them.
- `messages` is a string, or a list of `{"role": ..., "content": ...}` dicts.
- `extra` is merged into the request body as is. Use it for fields the connector does not know yet.
- `AsyncFlexGridClient` has the same methods for async apps. It runs the blocking client in a worker thread.
- `FlexGridConfig(base_url=..., model=...)` builds settings in code instead of from the environment.

The package has no runtime dependencies. Standard library only, so it installs on an air-gapped box.


## 7. Failure behaviour

**No fallback.** The connector talks to the configured endpoint or raises. It never calls another provider. There is no cloud SDK in the package.

| What happened | Error raised |
|---|---|
| `FLEXGRID_BASE_URL` missing or malformed, bad timeout, no model | `FlexGridConfigError` |
| Refused, DNS failure, no route, TLS failure, no answer to the connect within 5 s | `FlexGridUnreachableError` |
| Connected, then silence longer than `FLEXGRID_TIMEOUT` | `FlexGridTimeoutError` |
| Connection dropped part way | `FlexGridConnectionError` |
| HTTP 401 or 403 | `FlexGridAuthError` |
| Any other HTTP status of 300 or above | `FlexGridAPIError` (carries `status_code` and `body`) |
| HTTP 200 but not the assumed shape | `FlexGridResponseError` |

`FlexGridUnreachableError` and `FlexGridTimeoutError` are both `FlexGridConnectionError`. Everything is a `FlexGridError`.

Details worth knowing:

- `FLEXGRID_TIMEOUT` is the longest the client waits for the server to send anything: the first byte of a reply, or the next chunk of a stream. It is not a cap on total generation time.
- Redirects are reported, not followed. A redirect could send the API key to a different host.
- `FlexGridResponseError` is the error to expect if ARC is not OpenAI-compatible. Its message points back to this spec.
- The API key never appears in an error message or in `repr()` of the config.


## 8. Known gaps in v0.1

Each item here is tracked in the V2 roadmap in the README, so a reviewer can see both what is thin and when it closes.

- Not run against real FlexGrid hardware. The mock mimics the assumed contract, so a green test suite proves the connector is consistent with itself and nothing more. The first real exchange with ARC is the only thing that validates the contract.
- The probe measures one stream at a time. A multi-NPU deployment's quoted aggregate throughput is not tested. V0.2 adds `--parallel N`.
- No retries, no backoff, no circuit breaker. A 5xx or a dropped connection raises. Callers retry at their own layer. V0.2 adds a pluggable retry policy and a breaker.
- One timeout covers both the connect and the inter-byte wait. There is no separate per-token stall timeout on a stream, and no cap on total generation time. V0.2 adds a stall timeout.
- No observability hooks. No structured logging, no request IDs, no metrics callback. V0.2 adds `on_request` and `on_response` callbacks and a `request_id`.
- No contract version surfaced. When FlexGrid says "that is wrong" there is no anchor for which shape was in play. V0.2 adds `CONTRACT_VERSION`.
- KV cache and sessions are not modelled. Each call resends the whole conversation.
- No tool calling.
- Reasoning output is not handled. `text` is `message.content` only. If a reasoning model returns its thinking in a separate field, it is in `ChatResult.raw`. If it comes back inside the answer, it is in `text` untouched.
- No connection reuse. One short-lived connection per call. V0.3's move to `httpx` fixes this.
- TLS uses the system trust store. There is no option yet for a private certificate authority. V0.3 adds private CA support.
- No separate health or readiness probing. "Reachable" is one check. Cold-start model-load on Furiosa can take seconds and is not distinguished from an outage. V0.2 adds `/healthz` and `/readyz`.
- No prompt-logging guidance. Deliberately absent until there is a redaction story to match. V1.0 adds it.
- A DNS lookup is bounded by the operating system's resolver, not by the connect timeout. Use an IP or a name that resolves locally.


## 9. Open questions

Once these are answered, the real ARC contract is fully specified and `contract.py` can stop guessing.

**The API**

1. What does an app talk to: an HTTP API, gRPC, or an SDK? If HTTP, is it OpenAI-compatible (`/v1/chat/completions`)?
2. What is the base URL shape? Host, port, path prefix. HTTP or HTTPS, and if HTTPS, whose certificate?
3. Auth: API key, token, mTLS, or network-only? How is it sent?
4. What does one working request and response look like, and which request parameters are supported?

**Models**

5. Which models are live on a deployment, and what exact ID does a request send for each?
6. How is a model picked: a field in each request, a separate endpoint per model, or a setting when the app is deployed to ARC?
7. What are the context window and max output for each model?
8. For reasoning models: does the thinking come back in its own field or inside the answer? Can it be turned off per request?
9. Is tool calling supported on any of the models?

**Streaming and usage**

10. Is streaming supported, and in what format?
11. Do responses include token counts, on both streamed and non-streamed calls?

**KV cache**

12. How does KV-cache ownership show up to the caller: a session ID, a header, a separate call?
13. What ends a session: the end of a request, an explicit release, a timeout? Can a later request pick the KV back up, for a multi-turn chat?

**Network and onboarding**

14. Where can a deployment be reached from: the local network only, a VPN, the internet? Is there an IP allowlist?
15. What does onboarding a new app to ARC look like, step by step?
16. What limits should a client design around: concurrent requests, rate limits, request size, timeouts?
17. Errors: which status codes and body format? Most important: model not found, at capacity, and prompt too long.
18. Is there a health or status endpoint to poll, and are there metrics a client can read?

**Data**

19. What does ARC log about requests (prompts, outputs, metadata), where is it kept, and for how long?
20. Is there a way to get a test key on a single card before full onboarding?

**Concurrency and routing**

21. Does ARC serve multiple models per endpoint, or one model per endpoint? If the latter, is the `model` field in the request ignored? This is the Furiosa-server question stated plainly: the answer decides whether `FLEXGRID_MODEL` picks the model or is only a label.
22. What is the concurrency model per card: one stream at a time, N streams FIFO, N streams time-sliced? What happens when the Nth plus first request arrives, queued or rejected?
23. For the quoted aggregate throughput of a multi-NPU deployment, how does ARC spread load across cards, and is there a request header or hint a caller can use to pin a conversation to a card for KV reuse?

**Operations**

24. What is the expected cold-start time to load a model onto a card, and is there a status code or state a client can poll that distinguishes "model loading" from "down"? A reasoning model with a long warmup needs its own retryable state.
25. Does a stream that drops mid-generation have any recovery handle the client can use to resume, or is the only option to retry the whole prompt?
