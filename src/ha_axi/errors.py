"""Error types carrying the structured shape the CLI prints on stdout."""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


class AxiError(Exception):
    """An error the agent should be able to read, understand and act on.

    ``help_lines`` carry the specific command that fixes the problem, per the
    AXI standard: on errors, suggest the fix rather than pointing at --help.
    """

    exit_code = EXIT_ERROR

    def __init__(
        self,
        message: str,
        *,
        help_lines: list[str] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.help_lines = help_lines or []
        self.code = code


class UsageError(AxiError):
    """A malformed invocation: unknown flag, missing argument, bad value."""

    exit_code = EXIT_USAGE


class ConfigError(AxiError):
    """Required environment configuration is missing or unusable."""


class ConnectionFailed(AxiError):
    """The Home Assistant instance could not be reached."""


class AuthFailed(AxiError):
    """Home Assistant rejected the token."""


class NotFound(AxiError):
    """The requested entity, area or path does not exist."""


class ApiError(AxiError):
    """Home Assistant answered, but refused the request."""
