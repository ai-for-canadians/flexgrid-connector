import socket

import pytest

from flexgrid_connector import FlexGridClient, FlexGridConfig
from flexgrid_connector.mock_server import MockFlexGridServer


@pytest.fixture(autouse=True)
def no_ambient_settings(monkeypatch):
    # A developer's shell may export FLEXGRID_*. The suite must not depend on it.
    for name in ("FLEXGRID_BASE_URL", "FLEXGRID_MODEL", "FLEXGRID_API_KEY", "FLEXGRID_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock():
    with MockFlexGridServer() as server:
        yield server


@pytest.fixture
def config(mock):
    return FlexGridConfig(base_url=mock.base_url, model="mock-small", timeout=5)


@pytest.fixture
def client(config):
    return FlexGridClient(config)


@pytest.fixture
def dead_url():
    """A loopback URL with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"
