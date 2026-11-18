"""Exceptions. Each carries the process exit code the CLI should use."""
from __future__ import annotations


class PortError(Exception):
    exit_code = 3


class UsageError(PortError):
    """Bad arguments, missing configuration or unreadable bundle (exit 2)."""

    exit_code = 2


class AuthError(PortError):
    """The server rejected the API key (exit 2)."""

    exit_code = 2

    def __init__(self, status: int, message: str = "", url: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.url = url


class ApiError(PortError):
    """The server answered with an error or never became healthy (exit 3)."""

    exit_code = 3

    def __init__(self, status: int, message: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class ConnectError(ApiError):
    """No HTTP answer at all: server down, wrong host or port, network drop (exit 3)."""

    def __init__(self, url: str, detail: str = ""):
        super().__init__(0, f"cannot reach {url}: {detail}")
        self.url = url
        self.detail = detail


class IndexingError(PortError):
    """Indexing failed or timed out on the target (exit 3)."""

    exit_code = 3
