import json
import subprocess
import sys
from pathlib import Path

from flexgrid_connector import FlexGridConfig
from flexgrid_connector.mock_server import MockFlexGridServer
from flexgrid_connector.probe import FAIL, PASS, SKIP, WARN, main, run_probe

REPO = Path(__file__).resolve().parents[1]


def statuses(report):
    return {check.name: check.status for check in report.checks}


def test_probe_against_the_mock_is_ready(config):
    report = run_probe(config, runs=2)

    assert report.ready
    assert statuses(report) == {"reachable": PASS, "models listed": PASS, "completion": PASS, "streaming": PASS}
    assert len(report.stream_runs) == 2
    assert all(run.first_token_s is not None and not run.approximate for run in report.stream_runs)
    assert report.extra_headers == {"x-mock-flexgrid": "1"}


def test_probe_surfaces_what_the_endpoint_says_about_the_model(mock, capsys):
    mock.model_extras = {"some_limit": 4096, "nested": {"ignored": True}}
    report = run_probe(FlexGridConfig(base_url=mock.base_url, model="mock-small"), runs=1)
    assert report.model_card == {"some_limit": 4096}

    main(["--base-url", mock.base_url, "--model", "mock-small", "--runs", "1"])
    assert "some_limit: 4096" in capsys.readouterr().out


def test_probe_measures_tokens_per_second():
    with MockFlexGridServer(tokens_per_sec=400) as mock:
        report = run_probe(FlexGridConfig(base_url=mock.base_url, model="mock-small"), runs=1)
    rate = report.stream_runs[0].tokens_per_s
    assert 100 < rate < 440  # paced at 400, sleeps only ever make it slower


def test_cli_prints_a_ready_report_and_exits_zero(mock, capsys):
    code = main(["--base-url", mock.base_url, "--model", "mock-small", "--runs", "1"])
    out = capsys.readouterr().out

    assert code == 0
    assert "READY" in out and "NOT READY" not in out
    assert "tok/s" in out
    assert "assumed OpenAI-compatible, not confirmed by FlexGrid" in out


def test_cli_reads_the_environment(mock, capsys, monkeypatch):
    monkeypatch.setenv("FLEXGRID_BASE_URL", mock.base_url)
    monkeypatch.setenv("FLEXGRID_MODEL", "mock-large")
    assert main(["--runs", "1"]) == 0
    assert "mock-large" in capsys.readouterr().out


def test_unset_url_fails_loudly_with_exit_code_two(capsys):
    code = main([])
    captured = capsys.readouterr()

    assert code == 2
    assert "FLEXGRID_BASE_URL is not set" in captured.out
    assert "NOT READY" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_unreachable_url_fails_loudly_with_exit_code_one(dead_url, capsys):
    code = main(["--base-url", dead_url, "--model", "any"])
    captured = capsys.readouterr()

    assert code == 1
    assert "[FAIL] reachable" in captured.out
    assert "unreachable" in captured.out
    assert captured.out.count("[SKIP]") == 3
    assert "NOT READY" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_with_no_model_configured_the_first_listed_one_is_used(mock):
    report = run_probe(FlexGridConfig(base_url=mock.base_url), runs=1)
    assert report.ready
    assert report.model == "mock-small"
    assert "used the first one listed" in report.checks[2].detail


def test_model_that_is_not_deployed_is_not_ready(mock):
    report = run_probe(FlexGridConfig(base_url=mock.base_url, model="not-deployed"), runs=1)
    assert not report.ready
    assert statuses(report) == {"reachable": PASS, "models listed": WARN, "completion": FAIL, "streaming": SKIP}


def test_rejected_key_still_counts_as_reachable():
    with MockFlexGridServer(api_key="right") as mock:
        report = run_probe(FlexGridConfig(base_url=mock.base_url, model="mock-small"), runs=1)
    assert not report.ready
    assert statuses(report)["reachable"] == PASS
    assert statuses(report)["models listed"] == FAIL
    assert "401" in report.checks[1].detail


def test_endpoint_that_breaks_the_contract_is_not_ready(mock):
    mock.malformed = "shape"
    report = run_probe(FlexGridConfig(base_url=mock.base_url, model="mock-small"), runs=1)
    assert not report.ready
    assert statuses(report)["completion"] == FAIL
    assert "FLEXGRID_CONNECTOR_SPEC.md" in report.checks[2].detail


def test_streaming_check_can_be_skipped(mock, capsys):
    assert main(["--base-url", mock.base_url, "--model", "mock-small", "--no-stream"]) == 0
    assert "[SKIP] streaming" in capsys.readouterr().out


def test_json_output(mock, capsys):
    assert main(["--base-url", mock.base_url, "--model", "mock-small", "--runs", "1", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is True
    assert [check["status"] for check in report["checks"]] == [PASS] * 4
    assert report["stream_runs"][0]["tokens"] > 0


def test_probe_script_runs_from_a_checkout_without_installing(mock):
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "probe_flexgrid.py"), "--base-url", mock.base_url, "--runs", "1"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "READY" in done.stdout
