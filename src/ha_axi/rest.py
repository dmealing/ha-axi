"""Home Assistant REST API client, built on the standard library.

Covers the read-and-act half of the API: entity states, service calls and
template rendering. The registries are not reachable here -- see :mod:`ha_axi.ws`.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config
from .errors import ApiError, AuthFailed, ConnectionFailed, NotFound
from .output import debug

_JSON = "application/json"


class RestClient:
    """A thin, synchronous wrapper over the Home Assistant REST endpoints."""

    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------- transport

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict | None = None,
    ) -> Any:
        """Perform one authenticated request and decode the response.

        Returns parsed JSON when the response is JSON, otherwise the response
        text -- ``/api/template`` answers in ``text/plain``.
        """
        url = self._url(path, query)
        data = None
        headers = dict(self.config.auth_header)
        headers["Accept"] = f"{_JSON}, text/plain"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = _JSON

        debug(f"{method} {url}")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, method, path) from None
        except urllib.error.URLError as exc:
            raise self._url_error(exc) from None
        except (TimeoutError, socket.timeout):
            raise ConnectionFailed(
                f"timed out after {self.config.timeout:g}s waiting for Home Assistant",
                help_lines=["Raise the limit with `ha-axi --timeout 60 <command>`"],
                code="TIMEOUT",
            ) from None

        if _JSON in content_type:
            try:
                return json.loads(payload) if payload else None
            except json.JSONDecodeError:
                return payload
        return payload

    def _url(self, path: str, query: dict | None) -> str:
        path = path if path.startswith("/") else f"/{path}"
        if path != "/api" and not path.startswith("/api/"):
            path = f"/api{path}"
        url = f"{self.config.base_url}{path}"
        if query:
            pairs = [(k, v) for k, v in query.items() if v is not None]
            if pairs:
                url = f"{url}?{urllib.parse.urlencode(pairs)}"
        return url

    def _http_error(self, exc, method: str, path: str):
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            detail = parsed.get("message") or parsed.get("error") or raw
        except Exception:
            detail = ""
        detail = (detail or "").strip()

        if exc.code in (401, 403):
            return AuthFailed(
                "Home Assistant rejected the access token",
                help_lines=[
                    "Check HA_TOKEN holds a current long-lived access token",
                    "Create a new one on your Home Assistant profile page, under Security",
                ],
                code="UNAUTHORIZED",
            )
        if exc.code == 404:
            return NotFound(
                f"no such API path: {path}",
                help_lines=["Run `ha-axi --help` to see the available commands"],
                code="NOT_FOUND",
            )
        if exc.code == 405:
            return ApiError(
                f"{method} is not allowed on {path}",
                help_lines=["Run `ha-axi api --help` for the supported methods"],
                code="METHOD_NOT_ALLOWED",
            )
        return ApiError(
            f"Home Assistant returned HTTP {exc.code}" + (f": {detail}" if detail else ""),
            code=f"HTTP_{exc.code}",
        )

    def _url_error(self, exc):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            return ConnectionFailed(
                f"TLS handshake with Home Assistant failed: {reason}",
                help_lines=["Confirm HA_URL uses the scheme your instance actually serves"],
                code="TLS_ERROR",
            )
        return ConnectionFailed(
            f"could not reach Home Assistant: {reason}",
            help_lines=[
                "Check HA_URL points at a reachable Home Assistant instance",
                "Run `ha-axi doctor` to test the connection",
            ],
            code="UNREACHABLE",
        )

    # ------------------------------------------------------------- endpoints

    def health(self) -> Any:
        return self.request("GET", "/")

    def config_info(self) -> Any:
        return self.request("GET", "/config")

    def states(self) -> list:
        result = self.request("GET", "/states")
        return result if isinstance(result, list) else []

    def state(self, entity_id: str) -> dict:
        try:
            return self.request("GET", f"/states/{urllib.parse.quote(entity_id)}")
        except NotFound:
            raise NotFound(
                f"no entity with id {entity_id}",
                help_lines=[
                    f"Run `ha-axi state list --search {entity_id.split('.')[-1]}` to find it",
                    "Run `ha-axi state list --domain <domain>` to browse one domain",
                ],
                code="NO_SUCH_ENTITY",
            ) from None

    def services(self) -> list:
        result = self.request("GET", "/services")
        return result if isinstance(result, list) else []

    def call_service(
        self, domain: str, service: str, data: dict, *, return_response: bool = False
    ) -> Any:
        query = {"return_response": ""} if return_response else None
        return self.request("POST", f"/services/{domain}/{service}", body=data, query=query)

    def render_template(self, template: str) -> str:
        result = self.request("POST", "/template", body={"template": template})
        return result if isinstance(result, str) else json.dumps(result)
