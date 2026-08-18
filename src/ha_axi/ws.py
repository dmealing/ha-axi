"""Home Assistant WebSocket API client.

The entity, area, device, floor and label registries have no REST equivalent --
they are reachable only over the WebSocket API. That gap is the main reason this
tool exists.

Adding a command is a one-line entry in :data:`REGISTRY`; the transport, the
auth handshake and the error translation below are shared by every command.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import Any

from .config import Config
from .errors import ApiError, AuthFailed, ConnectionFailed, NotFound
from .output import debug

#: Cap on a single frame. Registry payloads on large installations comfortably
#: exceed the library default of 1 MiB.
MAX_FRAME_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class WsCommand:
    """One WebSocket command, declared once and reused by every caller."""

    name: str
    type: str
    summary: str
    required: tuple = ()
    optional: tuple = ()

    @property
    def params(self) -> tuple:
        return self.required + self.optional


def _cmd(name, type_, summary, required=(), optional=()) -> WsCommand:
    return WsCommand(name=name, type=type_, summary=summary, required=required, optional=optional)


#: The command table. Every registry operation the CLI exposes routes through
#: here, and so does `ha-axi ws <name>`, which is what makes the surface
#: extensible without touching the transport.
REGISTRY: dict = {
    c.name: c
    for c in (
        _cmd("entity.list", "config/entity_registry/list", "Read the entity registry"),
        _cmd(
            "entity.get",
            "config/entity_registry/get",
            "Read one entity registry entry",
            required=("entity_id",),
        ),
        _cmd(
            "entity.update",
            "config/entity_registry/update",
            "Update an entity registry entry",
            required=("entity_id",),
            optional=(
                "name",
                "area_id",
                "icon",
                "new_entity_id",
                "disabled_by",
                "hidden_by",
                "labels",
                "aliases",
            ),
        ),
        _cmd("area.list", "config/area_registry/list", "Read the area registry"),
        _cmd(
            "area.create",
            "config/area_registry/create",
            "Create an area",
            required=("name",),
            optional=("icon", "floor_id", "aliases", "labels", "picture"),
        ),
        _cmd(
            "area.update",
            "config/area_registry/update",
            "Update an area",
            required=("area_id",),
            optional=("name", "icon", "floor_id", "aliases", "labels", "picture"),
        ),
        _cmd(
            "area.delete",
            "config/area_registry/delete",
            "Delete an area",
            required=("area_id",),
        ),
        _cmd("device.list", "config/device_registry/list", "Read the device registry"),
        _cmd(
            "device.update",
            "config/device_registry/update",
            "Update a device registry entry",
            required=("device_id",),
            optional=("name_by_user", "area_id", "disabled_by", "labels"),
        ),
        _cmd("floor.list", "config/floor_registry/list", "Read the floor registry"),
        _cmd("label.list", "config/label_registry/list", "Read the label registry"),
        _cmd("config.get", "get_config", "Read the instance configuration"),
        _cmd("service.list", "get_services", "Read every registered service"),
        _cmd("state.list", "get_states", "Read every entity state"),
    )
}


class WsClient:
    """A synchronous WebSocket session: connect, authenticate, send commands.

    Used as a context manager so one connection serves several commands, which
    matters because the auth handshake costs a round trip.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._socket: Any = None
        self._next_id = 1
        self.ha_version = ""

    # ------------------------------------------------------------- lifecycle

    def __enter__(self) -> WsClient:
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def connect(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:  # pragma: no cover - dependency is declared in pyproject
            raise ConnectionFailed(
                "the websockets package is required for registry commands",
                help_lines=["Install it with `pip install 'ha-axi'` or `pip install websockets`"],
                code="MISSING_DEPENDENCY",
            ) from None

        url = self.config.ws_url
        debug(f"websocket connect {url}")
        try:
            self._socket = connect(
                url,
                open_timeout=self.config.timeout,
                close_timeout=self.config.timeout,
                max_size=MAX_FRAME_BYTES,
            )
        except (TimeoutError, OSError) as exc:
            raise ConnectionFailed(
                f"could not open a WebSocket to Home Assistant: {exc}",
                help_lines=[
                    "Check HA_URL points at a reachable Home Assistant instance",
                    "Run `ha-axi doctor` to test both the REST and WebSocket connections",
                ],
                code="WS_UNREACHABLE",
            ) from None
        except Exception as exc:
            raise ConnectionFailed(
                f"WebSocket handshake with Home Assistant failed: {exc}",
                help_lines=["Confirm HA_URL points at the Home Assistant root, not at /api"],
                code="WS_HANDSHAKE",
            ) from None
        self._authenticate()

    def close(self) -> None:
        if self._socket is not None:
            with contextlib.suppress(Exception):
                self._socket.close()
            self._socket = None

    # ------------------------------------------------------------- handshake

    def _authenticate(self) -> None:
        message = self._receive()
        if message.get("type") != "auth_required":
            raise ConnectionFailed(
                f"unexpected greeting from the WebSocket API: {message.get('type')!r}",
                code="WS_PROTOCOL",
            )
        self.ha_version = message.get("ha_version", "")
        self._send({"type": "auth", "access_token": self.config.token})

        while True:
            message = self._receive()
            kind = message.get("type")
            if kind == "auth_ok":
                self.ha_version = message.get("ha_version", self.ha_version)
                return
            if kind == "auth_invalid":
                raise AuthFailed(
                    "Home Assistant rejected the access token over the WebSocket API",
                    help_lines=[
                        "Check HA_TOKEN holds a current long-lived access token",
                        "Create a new one on your Home Assistant profile page, under Security",
                    ],
                    code="UNAUTHORIZED",
                )
            if kind != "auth_required":
                raise ConnectionFailed(
                    f"unexpected message during authentication: {kind!r}", code="WS_PROTOCOL"
                )

    # ------------------------------------------------------------- transport

    def _closed(self, exc: Exception) -> ConnectionFailed:
        return ConnectionFailed(
            f"WebSocket connection to Home Assistant closed: {exc}", code="WS_CLOSED"
        )

    def _send(self, payload: dict) -> None:
        try:
            self._socket.send(json.dumps(payload))
        except Exception as exc:
            raise self._closed(exc) from None

    def _receive(self) -> dict:
        try:
            raw = self._socket.recv(timeout=self.config.timeout)
        except Exception as exc:
            raise self._closed(exc) from None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            raise ConnectionFailed(
                "the WebSocket API sent a message that is not JSON", code="WS_PROTOCOL"
            ) from None
        if not isinstance(message, dict):
            raise ConnectionFailed(
                "the WebSocket API sent a message that is not an object", code="WS_PROTOCOL"
            )
        return message

    # -------------------------------------------------------------- commands

    def send_command(self, type_: str, params: dict | None = None) -> Any:
        """Send one command and return its ``result``, translating failures."""
        if self._socket is None:
            self.connect()
        message_id = self._next_id
        self._next_id += 1
        payload = {"id": message_id, "type": type_}
        # Null is meaningful here: sending `name: null` is how the registry
        # clears a user override. Callers decide what to include; nothing is
        # filtered out on the way past.
        payload.update(params or {})
        debug(f"websocket command {type_}")
        self._send(payload)

        while True:
            message = self._receive()
            if message.get("id") != message_id or message.get("type") != "result":
                # Event and pong frames may interleave; they are not our answer.
                continue
            if message.get("success"):
                return message.get("result")
            raise self._command_error(type_, message.get("error") or {})

    def run(self, name: str, params: dict | None = None) -> Any:
        """Send a command declared in :data:`REGISTRY` by its friendly name."""
        command = REGISTRY.get(name)
        if command is None:
            raise NotFound(
                f"unknown websocket command: {name}",
                help_lines=["Run `ha-axi ws --list` to see the declared commands"],
                code="NO_SUCH_COMMAND",
            )
        return self.send_command(command.type, params)

    def _command_error(self, type_: str, error: dict):
        code = str(error.get("code") or "unknown_error")
        message = str(error.get("message") or "the WebSocket API refused the command")
        if code in ("unauthorized", "invalid_auth"):
            return AuthFailed(
                f"the token is not permitted to run {type_}",
                help_lines=["Check HA_TOKEN belongs to an account with administrator rights"],
                code="UNAUTHORIZED",
            )
        if code == "not_found":
            return NotFound(message, code="NOT_FOUND")
        if code == "invalid_format":
            return ApiError(
                f"{type_} rejected the arguments: {message}",
                help_lines=["Run `ha-axi ws --list` to see each command's parameters"],
                code="INVALID_FORMAT",
            )
        return ApiError(f"{type_} failed: {message}", code=code.upper())
