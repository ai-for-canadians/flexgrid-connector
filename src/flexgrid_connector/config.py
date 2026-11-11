"""Connector settings. Read from the environment when asked for, never at import time."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from .errors import FlexGridConfigError

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_CONNECT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class FlexGridConfig:
    """Where the endpoint is and how long to wait for it.

    base_url includes the version prefix and no trailing slash, the same convention
    OpenAI-compatible clients use: scheme://host:port/v1
    """

    base_url: str
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)
    # Longest the client waits for the server to send anything: the first byte of
    # a reply, or the next chunk of a stream. Not a cap on total generation time.
    timeout: float = DEFAULT_TIMEOUT_S
    # Kept short and separate so a dead or unroutable endpoint fails in seconds
    # instead of holding a caller for the full read timeout.
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_S

    def __post_init__(self) -> None:
        base_url = (self.base_url or "").strip().rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FlexGridConfigError(
                f"FLEXGRID_BASE_URL must look like http://host:port/v1, got {self.base_url!r}."
            )
        try:
            _ = parts.port  # raises on a port that is not a number
        except ValueError as exc:
            raise FlexGridConfigError(f"FLEXGRID_BASE_URL has an invalid port: {self.base_url!r}.") from exc
        for name in ("timeout", "connect_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise FlexGridConfigError(f"{name} must be a positive number of seconds, got {value!r}.")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", (self.model or "").strip() or None)
        object.__setattr__(self, "api_key", (self.api_key or "").strip() or None)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FlexGridConfig:
        """Build from FLEXGRID_* variables. Raises FlexGridConfigError if the URL is missing."""
        env = os.environ if env is None else env
        base_url = (env.get("FLEXGRID_BASE_URL") or "").strip()
        if not base_url:
            raise FlexGridConfigError(
                "FLEXGRID_BASE_URL is not set. Point it at the FlexGrid endpoint, for example http://host:port/v1."
            )
        raw_timeout = (env.get("FLEXGRID_TIMEOUT") or "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
        except ValueError as exc:
            raise FlexGridConfigError(
                f"FLEXGRID_TIMEOUT must be a number of seconds, got {raw_timeout!r}."
            ) from exc
        return cls(
            base_url=base_url,
            model=env.get("FLEXGRID_MODEL"),
            api_key=env.get("FLEXGRID_API_KEY"),
            timeout=timeout,
        )

    @property
    def safe_url(self) -> str:
        """base_url with any user:password@ removed, for logs and error messages."""
        parts = urlsplit(self.base_url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
