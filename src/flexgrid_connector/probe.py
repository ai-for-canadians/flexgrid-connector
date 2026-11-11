"""Readiness probe and small benchmark for a FlexGrid endpoint.

    flexgrid-probe --base-url http://host:port/v1 --model <id>

Checks, in order: the endpoint answers, it lists models, a small completion
round-trips, and a streamed completion gives time to first token and tokens/sec.

Exit codes: 0 ready, 1 not ready, 2 nothing to probe (no usable URL).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field

from .client import FlexGridClient
from .config import FlexGridConfig
from .errors import FlexGridAPIError, FlexGridConfigError, FlexGridConnectionError, FlexGridError

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

DEFAULT_PROMPT = "Count from 1 to 40. Numbers only, separated by spaces."
DEFAULT_MAX_TOKENS = 64
DEFAULT_RUNS = 3

# Headers every HTTP server sends. Anything else is printed, because that is where
# an ARC session or KV-cache handle would show up if it travels in a header.
_ROUTINE_HEADERS = frozenset(
    {
        "access-control-allow-credentials",
        "access-control-allow-origin",
        "cache-control",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "date",
        "keep-alive",
        "server",
        "transfer-encoding",
        "vary",
    }
)


_ROUTINE_MODEL_FIELDS = frozenset({"id", "object", "owned_by", "created"})


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class StreamRun:
    first_token_s: float | None
    total_s: float
    tokens: int
    tokens_per_s: float | None
    # True when the server sent no usage, so stream chunks were counted instead of tokens.
    approximate: bool


@dataclass
class ProbeReport:
    base_url: str | None
    model: str | None
    checks: list[Check] = field(default_factory=list)
    stream_runs: list[StreamRun] = field(default_factory=list)
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Whatever the endpoint reports about the tested model beyond its ID, for example context limits.
    model_card: dict[str, object] = field(default_factory=dict)

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def ready(self) -> bool:
        statuses = [check.status for check in self.checks]
        return PASS in statuses and FAIL not in statuses

    def to_dict(self) -> dict[str, object]:
        return {"ready": self.ready, **asdict(self)}


def run_probe(
    config: FlexGridConfig,
    *,
    prompt: str = DEFAULT_PROMPT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    runs: int = DEFAULT_RUNS,
    stream: bool = True,
) -> ProbeReport:
    report = ProbeReport(base_url=config.safe_url, model=config.model)
    client = FlexGridClient(config)

    models: list[str] = []
    cards: list[dict[str, object]] = []
    started = time.perf_counter()
    try:
        cards = client.describe_models()
        models = [str(card["id"]) for card in cards]
    except FlexGridConnectionError as exc:
        report.add("reachable", FAIL, str(exc))
        for name in ("models listed", "completion", "streaming"):
            report.add(name, SKIP, "the endpoint was not reachable")
        return report
    except FlexGridAPIError as exc:
        report.add("reachable", PASS, f"answered HTTP {exc.status_code} in {_ms(started)} ms")
        report.add("models listed", FAIL, str(exc))
    except FlexGridError as exc:
        report.add("reachable", PASS, f"answered in {_ms(started)} ms")
        report.add("models listed", FAIL, str(exc))
    else:
        report.add("reachable", PASS, f"HTTP 200 from /models in {_ms(started)} ms")
        listed = f"{len(models)}: {', '.join(models[:8])}{' ...' if len(models) > 8 else ''}"
        if not models:
            report.add("models listed", WARN, "the endpoint lists no models")
        elif config.model and config.model not in models:
            report.add("models listed", WARN, f"{listed}. The configured model {config.model!r} is not in the list")
        else:
            report.add("models listed", PASS, listed)

    model = config.model or (models[0] if models else None)
    report.model = model
    if not model:
        report.add("completion", FAIL, "no model to test. Set FLEXGRID_MODEL or pass --model")
        report.add("streaming", SKIP, "no model to test")
        return report
    picked = "" if config.model else " (no model configured, used the first one listed)"
    card = next((card for card in cards if card["id"] == model), {})
    report.model_card = {
        key: value
        for key, value in card.items()
        if key not in _ROUTINE_MODEL_FIELDS and isinstance(value, (str, int, float, bool))
    }

    try:
        result = client.chat(prompt, model=model, max_tokens=max_tokens)
    except FlexGridError as exc:
        report.add("completion", FAIL, str(exc))
        report.add("streaming", SKIP, "the completion check failed")
        return report
    report.extra_headers = {k: v for k, v in result.headers.items() if k not in _ROUTINE_HEADERS}
    tokens = result.usage.completion_tokens if result.usage else None
    if tokens:
        speed = f"{tokens} tokens in {result.latency_s:.2f} s, {tokens / result.latency_s:.1f} tok/s end to end"
    else:
        speed = f"reply in {result.latency_s:.2f} s, the server reported no token usage"
    if result.text.strip():
        report.add("completion", PASS, speed + picked)
    else:
        report.add("completion", WARN, f"the reply was empty. {speed}{picked}")

    if not stream:
        report.add("streaming", SKIP, "skipped with --no-stream")
        return report
    try:
        for _ in range(max(1, runs)):
            report.stream_runs.append(_time_stream(client, prompt, model, max_tokens))
    except FlexGridError as exc:
        report.add("streaming", FAIL, str(exc))
        return report
    report.add(*_summarise_stream(report.stream_runs))
    return report


def _time_stream(client: FlexGridClient, prompt: str, model: str, max_tokens: int) -> StreamRun:
    started = time.perf_counter()
    first = last = None
    pieces = 0
    reported = None
    for chunk in client.chat_stream(prompt, model=model, max_tokens=max_tokens):
        if chunk.text:
            last = time.perf_counter()
            first = first or last
            pieces += 1
        if chunk.usage and chunk.usage.completion_tokens is not None:
            reported = chunk.usage.completion_tokens
    total = time.perf_counter() - started
    tokens = reported if reported is not None else pieces
    rate = None
    if first is not None and last is not None and tokens > 1 and last > first:
        # The first token marks the start of the window, so it is not counted in it.
        rate = (tokens - 1) / (last - first)
    return StreamRun(
        first_token_s=None if first is None else first - started,
        total_s=total,
        tokens=tokens,
        tokens_per_s=rate,
        approximate=reported is None,
    )


def _summarise_stream(runs: list[StreamRun]) -> tuple[str, str, str]:
    firsts = [run.first_token_s for run in runs if run.first_token_s is not None]
    rates = [run.tokens_per_s for run in runs if run.tokens_per_s is not None]
    if not firsts:
        return "streaming", WARN, "the stream completed but carried no text"
    detail = f"first token {statistics.median(firsts) * 1000:.0f} ms"
    if rates:
        detail += f", {statistics.median(rates):.1f} tok/s while generating"
    detail += f" (median of {len(runs)} run{'s' if len(runs) != 1 else ''})"
    if any(run.approximate for run in runs):
        detail += ". Approximate: the server sent no usage, so stream chunks were counted"
    return "streaming", PASS, detail


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


# --- output ---------------------------------------------------------------

_COLORS = {PASS: "32", WARN: "33", FAIL: "31", SKIP: "2"}


def format_report(report: ProbeReport, *, color: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    lines = ["FlexGrid readiness probe", ""]
    if report.base_url:
        lines += [
            f"  endpoint   {report.base_url}",
            f"  model      {report.model or 'not set'}",
            "  contract   assumed OpenAI-compatible, not confirmed by FlexGrid",
            "",
        ]
    width = max(len(check.name) for check in report.checks)
    for check in report.checks:
        tag = paint(f"[{check.status}]", _COLORS[check.status])
        lines.append(f"  {tag} {check.name.ljust(width)}   {check.detail}")

    if report.stream_runs:
        lines += ["", "  streaming runs", "    run   first token     tok/s     total"]
        for i, run in enumerate(report.stream_runs, 1):
            first = "-" if run.first_token_s is None else f"{run.first_token_s * 1000:.0f} ms"
            rate = "-" if run.tokens_per_s is None else f"{run.tokens_per_s:.1f}"
            lines.append(f"    {i:>3}   {first:>11}   {rate:>7}   {run.total_s:>5.2f} s")

    if report.model_card:
        lines += ["", "  what the endpoint reports about this model"]
        lines += [f"    {name}: {value}" for name, value in sorted(report.model_card.items())]

    if report.extra_headers:
        lines += ["", "  response headers worth a look"]
        lines += [f"    {name}: {value}" for name, value in sorted(report.extra_headers.items())]

    lines.append("")
    if report.ready:
        lines.append("  " + paint("READY", "1;32") + "  The endpoint answered every check under the assumed contract.")
    else:
        failed = next((check.name for check in report.checks if check.status == FAIL), None)
        if failed == "config":
            why = "Nothing was probed."
        else:
            why = f"First failure: {failed}." if failed else "No check passed."
        lines.append("  " + paint("NOT READY", "1;31") + f"  {why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flexgrid-probe",
        description="Readiness report for a FlexGrid endpoint: reachable, models, completion, latency, tokens/sec.",
        epilog="The API key is read from FLEXGRID_API_KEY only, so it never lands in shell history.",
    )
    parser.add_argument("--base-url", help="endpoint, for example http://host:port/v1 (default: FLEXGRID_BASE_URL)")
    parser.add_argument("--model", help="model to test (default: FLEXGRID_MODEL, else the first one listed)")
    parser.add_argument("--timeout", type=float, help="seconds of silence to allow (default: FLEXGRID_TIMEOUT or 60)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt for the completion checks")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="cap on generated tokens per run")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="streamed runs to time (default: 3)")
    parser.add_argument("--no-stream", action="store_true", help="skip the streaming check")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.base_url:
        env["FLEXGRID_BASE_URL"] = args.base_url
    if args.model:
        env["FLEXGRID_MODEL"] = args.model
    if args.timeout is not None:
        env["FLEXGRID_TIMEOUT"] = str(args.timeout)

    try:
        config = FlexGridConfig.from_env(env)
    except FlexGridConfigError as exc:
        report = ProbeReport(base_url=None, model=None)
        report.add("config", FAIL, str(exc))
        exit_code = 2
    else:
        report = run_probe(
            config, prompt=args.prompt, max_tokens=args.max_tokens, runs=args.runs, stream=not args.no_stream
        )
        exit_code = 0 if report.ready else 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
        print(format_report(report, color=color))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
