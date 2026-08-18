"""Connection settings, read from the environment and never from a file.

Home Assistant credentials are long-lived JWTs. This tool deliberately has no
``--token`` flag and reads no credential file inside the repository: a token
passed on a command line leaks into shell history and the process table, and a
token in a file leaks into commits. The environment is the only channel.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError

#: Primary variable names, with the ``hass-cli`` names accepted as fallbacks so
#: an existing Home Assistant shell environment works unchanged.
URL_VARS = ("HA_URL", "HASS_SERVER")
TOKEN_VARS = ("HA_TOKEN", "HASS_TOKEN")

DEFAULT_TIMEOUT = 30.0

_SETUP_HELP = [
    "Set HA_URL to your Home Assistant base URL, e.g. export HA_URL=https://homeassistant.example.com",
    "Set HA_TOKEN to a long-lived access token from your Home Assistant profile page, under Security",
    "Run `ha-axi doctor` to verify the connection once both are set",
]


@dataclass(frozen=True)
class Config:
    """A resolved, ready-to-use connection configuration."""

    base_url: str
    token: str
    timeout: float = DEFAULT_TIMEOUT

    @property
    def rest_root(self) -> str:
        return f"{self.base_url}/api"

    @property
    def ws_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, f"{parts.path}/api/websocket", "", ""))

    @property
    def auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


def _first_env(names: tuple, environ) -> tuple:
    for name in names:
        value = environ.get(name)
        if value and value.strip():
            return name, value.strip()
    return None, None


def normalize_base_url(raw: str) -> str:
    """Accept a bare host, add a scheme if missing, and drop any trailing path noise."""
    value = raw.strip().rstrip("/")
    if "://" not in value:
        value = f"http://{value}"
    parts = urlsplit(value)
    if not parts.netloc:
        raise ConfigError(
            f"{URL_VARS[0]} is not a usable URL: {value!r}",
            help_lines=[_SETUP_HELP[0]],
            code="BAD_URL",
        )
    if parts.scheme not in ("http", "https"):
        raise ConfigError(
            f"{URL_VARS[0]} must use http or https, got {parts.scheme!r}",
            help_lines=[_SETUP_HELP[0]],
            code="BAD_URL",
        )
    path = parts.path.rstrip("/")
    # A base URL that already points at the API root is a common paste mistake.
    if path.endswith("/api"):
        path = path[: -len("/api")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def load(environ=None, *, timeout: float | None = None) -> Config:
    """Resolve a :class:`Config` or raise :class:`ConfigError` naming what is absent."""
    environ = os.environ if environ is None else environ
    _, raw_url = _first_env(URL_VARS, environ)
    _, token = _first_env(TOKEN_VARS, environ)

    missing = []
    if not raw_url:
        missing.append(URL_VARS[0])
    if not token:
        missing.append(TOKEN_VARS[0])
    if missing:
        names = " and ".join(missing)
        plural = "are" if len(missing) > 1 else "is"
        raise ConfigError(
            f"{names} {plural} not set in the environment",
            help_lines=_SETUP_HELP,
            code="NOT_CONFIGURED",
        )

    return Config(
        base_url=normalize_base_url(raw_url),
        token=token,
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
    )


def describe_environment(environ=None) -> dict:
    """Report which variables are set without ever revealing the token."""
    environ = os.environ if environ is None else environ
    url_var, raw_url = _first_env(URL_VARS, environ)
    token_var, token = _first_env(TOKEN_VARS, environ)
    return {
        "url_var": url_var or "",
        "url_set": bool(raw_url),
        "token_var": token_var or "",
        "token_set": bool(token),
    }
