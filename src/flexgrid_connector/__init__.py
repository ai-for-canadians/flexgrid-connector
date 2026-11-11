"""Client for FlexGrid on-prem inference.

The wire contract is an assumption (an OpenAI-compatible chat API) until FlexGrid
confirms what ARC exposes. See FLEXGRID_CONNECTOR_SPEC.md.
"""
from .client import AsyncFlexGridClient, FlexGridClient
from .config import FlexGridConfig
from .errors import (
    FlexGridAPIError,
    FlexGridAuthError,
    FlexGridConfigError,
    FlexGridConnectionError,
    FlexGridError,
    FlexGridResponseError,
    FlexGridTimeoutError,
    FlexGridUnreachableError,
)
from .results import ChatChunk, ChatResult, Usage

__version__ = "0.1.0"

__all__ = [
    "AsyncFlexGridClient",
    "ChatChunk",
    "ChatResult",
    "FlexGridAPIError",
    "FlexGridAuthError",
    "FlexGridClient",
    "FlexGridConfig",
    "FlexGridConfigError",
    "FlexGridConnectionError",
    "FlexGridError",
    "FlexGridResponseError",
    "FlexGridTimeoutError",
    "FlexGridUnreachableError",
    "Usage",
]
