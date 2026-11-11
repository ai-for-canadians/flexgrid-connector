"""Promises the package makes about itself: it installs anywhere, and the mock ships with it."""
import os
import re
import subprocess
import sys
from pathlib import Path

import flexgrid_connector
from flexgrid_connector import FlexGridClient, FlexGridConfig

REPO = Path(__file__).resolve().parents[1]


def test_no_runtime_dependencies():
    pyproject = (REPO / "pyproject.toml").read_text()
    assert re.search(r"^dependencies = \[\]$", pyproject, flags=re.MULTILINE)


def test_only_the_standard_library_is_imported():
    code = (
        "import sys; before = set(sys.modules); "
        "import flexgrid_connector, flexgrid_connector.probe, flexgrid_connector.mock_server; "
        "extra = sorted(m.split('.')[0] for m in set(sys.modules) - before); "
        "print(','.join(sorted({m for m in extra if m not in sys.stdlib_module_names and m != 'flexgrid_connector'})))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""


def test_version_matches_pyproject():
    pyproject = (REPO / "pyproject.toml").read_text()
    assert f'version = "{flexgrid_connector.__version__}"' in pyproject


def test_mock_server_script_serves_the_assumed_contract():
    server = subprocess.Popen(
        [sys.executable, str(REPO / "scripts" / "mock_flexgrid.py"), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        banner = server.stdout.readline()
        base_url = banner.split()[-1]
        assert base_url.startswith("http://127.0.0.1:"), banner
        client = FlexGridClient(FlexGridConfig(base_url=base_url, model="mock-small", timeout=10))
        assert "You said: ping" in client.chat("ping").text
    finally:
        server.terminate()
        server.wait(timeout=10)
