# flexgrid-connector

A small Python client for running inference on FlexGrid's on-prem hardware instead of a cloud API. It comes with a readiness probe and a local mock, so you can try everything with no hardware.

**Status: the wire contract is an assumption.** The connector assumes FlexGrid's ARC orchestration layer exposes an OpenAI-compatible chat API. That has not been confirmed yet, and nothing here has been run against FlexGrid hardware. `FLEXGRID_CONNECTOR_SPEC.md` says exactly what is assumed and lists the open questions.

Built by AI for Canadians. Apache 2.0.


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
