# flexgrid-connector

A small Python client for running inference on FlexGrid's on-prem hardware instead of a cloud API. It comes with a readiness probe and a local mock, so you can try everything with no hardware.

**Status: the wire contract is an assumption.** The connector assumes FlexGrid's ARC orchestration layer exposes an OpenAI-compatible chat API. That has not been confirmed yet, and nothing here has been run against FlexGrid hardware. `FLEXGRID_CONNECTOR_SPEC.md` says exactly what is assumed and lists the open questions.

Built by AI for Canadians. Apache 2.0.


## Known limits of v0.1

This is a v0.1. It is honest about where it is thin so a reviewer can plan around it rather than find out at the wrong moment.

- **The mock tests the mock.** 110 passing tests prove the connector is self-consistent against an assumed contract. They do not prove the contract is right. The first real exchange with ARC is the only thing that does.
- **One-model-per-endpoint may be the reality.** Furiosa's own OpenAI-compatible server serves one model per server and ignores the `model` field. If ARC is a thin pass-through, `FLEXGRID_MODEL` is a label the connector sends but ARC ignores, and multi-model routing is done by base URL. The connector works either way; the spec calls this out so nobody is surprised.
- **No retries, no backoff, no circuit breaker.** A 5xx or a dropped connection raises. Callers are expected to retry at their own layer in v0.1.
- **One timeout covers everything.** `FLEXGRID_TIMEOUT` is silence between bytes. There is no separate per-token stall timeout on a stream, and no cap on total generation time.
- **The probe tests one stream at a time.** The quoted aggregate throughput of a multi-NPU deployment is not measured. A parallel-streams option is on the v0.2 roadmap below.
- **No observability hooks.** No structured logging, no request IDs, no metrics callback. Prompt-logging guidance is deliberately not here yet.
- **Standard-library HTTP only.** Kept zero-dep for an air-gapped install. V0.2 reconsiders `httpx` once retries and streaming hardening land.

Read these together with section 8 of the spec.


## V2 roadmap

Tracked here so a reviewer can see what v0.1 is not, and when each gap is planned to close. Each numbered item is one PR.

**v0.2, hardening (next)**

1. Retry policy with jittered exponential backoff on 5xx and idempotent failures. Pluggable `RetryPolicy`, off by default so v0.1 behaviour stays available.
2. Circuit breaker on repeated unreachable or 5xx within a window. Raises the typed error early instead of hammering the box.
3. Per-token stall timeout on `chat_stream`, separate from `FLEXGRID_TIMEOUT`. Catches a stream that hangs mid-generation.
4. `--parallel N` on `flexgrid-probe`. Fires N concurrent streams against the endpoint and reports P50, P95, P99 first-token latency and aggregate tokens/sec. This is what validates a multi-NPU quote before anyone else hits the box.
5. `--record` on `flexgrid-probe`. Captures a real request and response from an endpoint and dumps it as a fixture the mock can replay. Escapes the mock-tests-the-mock tautology.
6. Pluggable `on_request` and `on_response` callbacks, each receiving a `request_id`. Minimum viable observability seam.
7. `CONTRACT_VERSION` constant surfaced in the probe output and in `ChatResult.contract_version`, so when FlexGrid says "that is wrong" the exchange is pinned to a known shape.
8. `/healthz` and `/readyz` probing, treated as distinct from "reachable". Handles the cold-start model-load case as its own retryable state.

**v0.3, hosting-aware**

9. Switch to `httpx` for sync, async and streaming, now that retries and stall timeouts justify the dependency.
10. Explicit auth strategy enum: `none`, `bearer`, `header`, `mtls`. Removes the hand-wave in the current API-key handling.
11. Private CA and self-signed TLS support, so a FlexGrid deployment with its own certificate authority is a config change not a code change.
12. Capacity signal. If ARC exposes queue depth or active-stream count on a status endpoint, surface it on the client so a caller can backpressure instead of queueing.

**v1.0, ready to stand behind**

13. Named contract variants: one base URL per model (the Furiosa-server case) and model-field routing (the ARC-dispatches case) as explicit configurations, not guesses.
14. Usage accounting hook. Per-call usage keyed by API key, so a shared deployment can see which caller used which card.
15. Prompt-logging opt-in with redaction guidance in the README, matching the sovereignty pitch.
16. Reconnection logic on SSE drops.

Open an issue to argue the order or add an item.


## Try it with no hardware

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                   # whole suite, no network, a few seconds

flexgrid-mock --port 8089 --tps 105      # terminal 1: local stand-in for the endpoint
```

```bash
# terminal 2
export FLEXGRID_BASE_URL=http://127.0.0.1:8089/v1
export FLEXGRID_MODEL=mock-small
flexgrid-probe
```

No install needed for a quick look. `python scripts/mock_flexgrid.py` and `python scripts/probe_flexgrid.py` run straight from a checkout, and `pytest` only needs pytest.


## Use it

```python
from flexgrid_connector import FlexGridClient, FlexGridError

client = FlexGridClient()                # reads FLEXGRID_* from the environment

try:
    reply = client.chat("Give me one line on on-prem inference.", system="Be brief.")
    print(reply.text)
except FlexGridError as exc:
    print(f"FlexGrid failed: {exc}")     # typed error. It never falls back to another provider.

for chunk in client.chat_stream("Same again, streamed."):
    print(chunk.text, end="", flush=True)
```

Async apps use `AsyncFlexGridClient`, same methods.

| Variable | Required | Meaning |
|---|---|---|
| `FLEXGRID_BASE_URL` | yes | Endpoint with the version prefix: `http://host:port/v1` |
| `FLEXGRID_MODEL` | for chat | Model ID to request |
| `FLEXGRID_API_KEY` | no | Sent as a bearer token when set |
| `FLEXGRID_TIMEOUT` | no | Seconds of silence before giving up. Default 60. |


## Pointing it at a real endpoint

1. Export `FLEXGRID_BASE_URL` and `FLEXGRID_MODEL`, plus `FLEXGRID_API_KEY` if you have one.
2. Run `flexgrid-probe` from a machine that can reach the units. It reports reachable, models listed, a completion round trip, latency and tokens/sec.
3. Green: point your app at the same settings.
4. Not green: the report says which check failed. If it is the contract, `src/flexgrid_connector/contract.py` is the file to change.


## Wrong about ARC?

Likely, in places. If you know how ARC actually works, open an issue or comment on `src/flexgrid_connector/contract.py` and `src/flexgrid_connector/mock_server.py`. One real request and response is the most useful thing you can send: it becomes a test case and the mock starts behaving like the real thing.


## What is in here

```
src/flexgrid_connector/
  client.py        FlexGridClient and AsyncFlexGridClient
  contract.py      the assumed wire contract, all of it      <- the file that changes once ARC is confirmed
  transport.py     HTTP on the standard library, every failure mapped to a typed error
  config.py        the four env vars
  errors.py        the error types
  results.py       ChatResult, ChatChunk, Usage
  probe.py         flexgrid-probe
  mock_server.py   flexgrid-mock
scripts/           run the probe and the mock without installing
tests/             pytest, runs against the mock
FLEXGRID_CONNECTOR_SPEC.md   assumed contract, config, failure behaviour, open questions
```

No runtime dependencies. Python 3.10 or newer.


## License

Copyright 2026 AI for Canadians. Licensed under the Apache License, Version 2.0. See `LICENSE`.

FlexGrid and ARC are FlexGrid's names. This is an independent client, not an official FlexGrid project.
