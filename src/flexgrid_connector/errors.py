"""Typed errors. Catch FlexGridError for everything, or a subclass to react to one failure mode.

FlexGridError
    FlexGridConfigError          missing or invalid settings
    FlexGridConnectionError      no usable HTTP response came back
        FlexGridUnreachableError     could not connect at all
        FlexGridTimeoutError         connected, then the server went silent
    FlexGridAPIError             the endpoint answered with an HTTP error status
        FlexGridAuthError            401 or 403
    FlexGridResponseError        HTTP 2xx, but not the shape the assumed contract expects
"""


class FlexGridError(Exception):
    """Base class for every error this package raises."""


class FlexGridConfigError(FlexGridError):
    """Settings are missing or invalid, for example FLEXGRID_BASE_URL is not set."""


class FlexGridConnectionError(FlexGridError):
    """No usable HTTP response came back from the endpoint."""


class FlexGridUnreachableError(FlexGridConnectionError):
    """Could not connect: refused, DNS failure, no route, TLS failure, or connect timeout."""


class FlexGridTimeoutError(FlexGridConnectionError):
    """Connected, then the server stayed silent for longer than the timeout."""


class FlexGridAPIError(FlexGridError):
    """The endpoint answered with an HTTP error status."""

    def __init__(self, status_code: int, message: str, body: str = ""):
        super().__init__(f"FlexGrid returned HTTP {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class FlexGridAuthError(FlexGridAPIError):
    """HTTP 401 or 403. Check FLEXGRID_API_KEY."""


class FlexGridResponseError(FlexGridError):
    """HTTP 2xx, but the body does not match the assumed contract."""
