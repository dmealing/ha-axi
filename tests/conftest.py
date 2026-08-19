"""Fake Home Assistant servers, so every test runs against a real socket.

No live installation and no live token is ever needed: the REST double is an
``http.server`` and the WebSocket double is a real ``websockets`` server that
performs the same auth handshake Home Assistant does. Both bind to loopback on
an ephemeral port.

Every fixture in this suite is synthetic. Entity ids, names and areas are
invented (``light.example_lamp``, ``Example Room``) and the token is an obvious
placeholder, so nothing here describes any particular installation.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from ha_axi import output

#: An obviously-synthetic token. It is not a JWT and grants nothing.
FAKE_TOKEN = "example-test-token-not-a-real-credential"


def synthetic_jwt() -> str:
    """A structurally valid, entirely fake JWT, assembled at run time.

    Encoding the header and payload here rather than writing the `eyJ...`
    literal keeps the shape out of the test sources, so the leak scanner's
    condensed pass -- which exists to catch exactly such a literal split across
    lines -- does not fire on the tests that exercise it.
    """
    import base64
    import json

    def segment(payload):
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment({'sub': 'example'})}.c2lnbmF0dXJlaGVyZQ"


# --------------------------------------------------------------- fixture data

STATES = [
    {
        "entity_id": "light.example_lamp",
        "state": "on",
        "attributes": {"friendly_name": "Example Lamp", "brightness": 180},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
    },
    {
        "entity_id": "light.example_ceiling",
        "state": "off",
        "attributes": {"friendly_name": "Example Ceiling"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
    },
    {
        "entity_id": "sensor.example_temperature",
        "state": "21.5",
        "attributes": {"friendly_name": "Example Temperature", "unit_of_measurement": "C"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
    },
    {
        "entity_id": "switch.example_outlet",
        "state": "unavailable",
        "attributes": {"friendly_name": "Example Outlet"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
    },
]

SERVICES = [
    {
        "domain": "light",
        "services": {
            "turn_on": {"name": "Turn on", "fields": {"brightness": {}}},
            "turn_off": {"name": "Turn off", "fields": {}},
        },
    },
    {"domain": "switch", "services": {"toggle": {"name": "Toggle", "fields": {}}}},
]

ENTITY_REGISTRY = [
    {
        "entity_id": "light.example_lamp",
        "name": "Example Lamp",
        "original_name": "Lamp",
        "area_id": "example_room",
        "device_id": "device_one",
        "platform": "demo",
        "unique_id": "unique-one",
        "icon": None,
        "disabled_by": None,
        "hidden_by": None,
        "entity_category": None,
    },
    {
        "entity_id": "light.example_ceiling",
        "name": None,
        "original_name": "Example Ceiling",
        "area_id": None,
        "device_id": "device_two",
        "platform": "demo",
        "unique_id": "unique-two",
        "icon": None,
        "disabled_by": None,
        "hidden_by": None,
        "entity_category": None,
    },
    {
        "entity_id": "sensor.example_temperature",
        "name": None,
        "original_name": "Example Temperature",
        "area_id": None,
        "device_id": None,
        "platform": "demo",
        "unique_id": "unique-three",
        "icon": None,
        "disabled_by": None,
        "hidden_by": None,
        "entity_category": "diagnostic",
    },
]

AREA_REGISTRY = [
    {
        "area_id": "example_room",
        "name": "Example Room",
        "icon": None,
        "floor_id": None,
        "aliases": [],
    },
    {
        "area_id": "example_hall",
        "name": "Example Hall",
        "icon": "mdi:door",
        "floor_id": "ground",
        "aliases": [],
    },
]

DEVICE_REGISTRY = [
    {
        "id": "device_one",
        "name": "Example Device One",
        "name_by_user": None,
        "area_id": "example_room",
        "manufacturer": "Example Co",
        "model": "Model X",
    },
    {
        "id": "device_two",
        "name": "Example Device Two",
        "name_by_user": "Renamed Device",
        "area_id": "example_hall",
        "manufacturer": "Example Co",
        "model": "Model Y",
    },
]

#: Every key each modelled WebSocket command accepts, beyond `id` and `type`.
#:
#: Declared here rather than imported from ``ha_axi.ws.REGISTRY`` on purpose: a
#: double that takes its schema from the client can only ever prove the client
#: agrees with itself. Home Assistant validates each command against a
#: voluptuous schema that defaults to ``PREVENT_EXTRA``, so an undeclared key
#: comes back as ``invalid_format`` rather than being quietly ignored -- which
#: is what makes a wrong wire shape fail a test instead of passing one.
#:
#: A parameter added to the client and not to this table will be rejected here.
#: That is the point: the table is a second opinion, and updating it is how a
#: new parameter gets confirmed against what the API actually accepts.
WS_COMMAND_KEYS = {
    "config/entity_registry/list": (),
    "config/entity_registry/get": ("entity_id",),
    "config/entity_registry/update": (
        "entity_id",
        "name",
        "icon",
        "area_id",
        "new_entity_id",
        "disabled_by",
        "hidden_by",
        "labels",
        "aliases",
    ),
    "config/area_registry/list": (),
    "config/area_registry/create": ("name", "icon", "floor_id", "aliases", "labels", "picture"),
    "config/area_registry/update": (
        "area_id",
        "name",
        "icon",
        "floor_id",
        "aliases",
        "labels",
        "picture",
    ),
    "config/area_registry/delete": ("area_id",),
    "config/device_registry/list": (),
    "config/device_registry/update": (
        "device_id",
        "name_by_user",
        "area_id",
        "disabled_by",
        "labels",
    ),
    "config/floor_registry/list": (),
    "config/label_registry/list": (),
    "get_config": (),
    "get_services": (),
    "get_states": (),
}

#: The fields `config/entity_registry/update` writes onto the stored entry.
ENTITY_UPDATE_FIELDS = ("name", "icon", "area_id", "disabled_by", "hidden_by", "labels", "aliases")


# ------------------------------------------------------------------ REST double


class FakeRestServer:
    """An HTTP server that answers the Home Assistant REST endpoints under test."""

    def __init__(self):
        self.requests = []
        self.state = {
            "states": [json.loads(json.dumps(s)) for s in STATES],
            "services": SERVICES,
            "template": "rendered",
            "service_result": None,
        }
        self.status_override = None
        #: Seconds to stall before answering, for exercising client timeouts.
        self.delay = 0.0
        #: Answer with this raw body and Content-Type: application/json.
        self.malformed_json = None
        #: When set to a URL, the NEXT request answers 302 pointing at it and
        #: the setting clears, so a followed redirect can reach a real handler.
        self.redirect_to = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _authorized(self):
                header = self.headers.get("Authorization", "")
                return header == f"Bearer {FAKE_TOKEN}"

            def _send(self, code, payload, content_type="application/json"):
                if isinstance(payload, bytes):
                    body = payload
                elif content_type == "application/json":
                    body = json.dumps(payload).encode()
                else:
                    body = str(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                outer.requests.append(
                    {
                        "method": method,
                        "path": path,
                        "body": body,
                        "query": urlparse(self.path).query,
                    }
                )

                if outer.delay:
                    time.sleep(outer.delay)
                if outer.malformed_json is not None:
                    return self._send(200, outer.malformed_json, content_type="application/json")
                if outer.redirect_to is not None:
                    location, outer.redirect_to = outer.redirect_to, None
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.status_override is not None:
                    code, payload = outer.status_override
                    return self._send(code, payload)
                if not self._authorized():
                    return self._send(401, {"message": "Unauthorized"})

                if path == "/api/":
                    return self._send(200, {"message": "API running."})
                if path == "/api/config":
                    return self._send(200, {"version": "2026.1.0", "location_name": "Example Home"})
                if path == "/api/states":
                    return self._send(200, outer.state["states"])
                if path.startswith("/api/states/"):
                    entity_id = path[len("/api/states/") :]
                    for state in outer.state["states"]:
                        if state["entity_id"] == entity_id:
                            return self._send(200, state)
                    return self._send(404, {"message": "Entity not found."})
                if path == "/api/services" and method == "GET":
                    return self._send(200, outer.state["services"])
                if path.startswith("/api/services/") and method == "POST":
                    # Home Assistant validates service data under
                    # vol.Schema(..., extra=vol.PREVENT_EXTRA); a nested
                    # `target` key is an extra key and is rejected. Mirroring
                    # that here is what stops a wrong wire shape passing.
                    if isinstance(body, dict) and "target" in body:
                        return self._send(
                            400, {"message": "extra keys not allowed @ data['target']"}
                        )
                    result = outer.state["service_result"]
                    if result is None:
                        result = [outer.state["states"][0]]
                    return self._send(200, result)
                if path == "/api/template" and method == "POST":
                    return self._send(200, outer.state["template"], content_type="text/plain")
                return self._send(404, {"message": "Not found."})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A short poll interval keeps shutdown() from costing half a second per test.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"


# ------------------------------------------------------------- WebSocket double


class FakeWsServer:
    """A WebSocket server performing the Home Assistant auth handshake and registry commands."""

    def __init__(self, *, reject_auth=False):
        self.reject_auth = reject_auth
        self.received = []
        self.entities = [json.loads(json.dumps(e)) for e in ENTITY_REGISTRY]
        self.areas = [json.loads(json.dumps(a)) for a in AREA_REGISTRY]
        self.devices = [json.loads(json.dumps(d)) for d in DEVICE_REGISTRY]
        self.fail_next = None
        self.close_after = None
        #: Frames to emit before the next command result, e.g. an event or a
        #: pong, which a real instance interleaves freely.
        self.interleave = []
        #: Replace the auth_required greeting with something else.
        self.greeting = None
        #: Send an extra message between `auth` and `auth_ok`.
        self.mid_auth = None
        #: Emit a non-JSON frame instead of the next result.
        self.send_garbage = False
        #: Close the connection instead of answering the next command.
        self.close_on_command = False
        self._server = None
        self._thread = None

    # -- protocol ----------------------------------------------------------

    def _handler(self, websocket):
        if self.greeting is not None:
            websocket.send(json.dumps(self.greeting))
            return
        websocket.send(json.dumps({"type": "auth_required", "ha_version": "2026.1.0"}))
        message = json.loads(websocket.recv())
        if message.get("type") != "auth" or message.get("access_token") != FAKE_TOKEN:
            websocket.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
            return
        if self.reject_auth:
            websocket.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
            return
        if self.mid_auth is not None:
            websocket.send(json.dumps(self.mid_auth))
            return
        websocket.send(json.dumps({"type": "auth_ok", "ha_version": "2026.1.0"}))

        while True:
            try:
                raw = websocket.recv()
            except Exception:
                return
            command = json.loads(raw)
            self.received.append(command)
            if self.close_on_command:
                websocket.close()
                return
            if self.send_garbage:
                self.send_garbage = False
                websocket.send("this is not json")
                continue
            # A real instance interleaves events and pongs with results; the
            # client must skip them rather than mis-correlate.
            for frame in self.interleave:
                websocket.send(json.dumps(frame))
            self.interleave = []
            websocket.send(json.dumps(self._respond(command)))
            if self.close_after is not None and len(self.received) >= self.close_after:
                # Close inside the handler so the close frame follows the last
                # reply immediately, the way a restart drops a session mid-flight.
                websocket.close()
                return

    def _respond(self, command):
        message_id, type_ = command.get("id"), command.get("type")
        if self.fail_next is not None:
            error = self.fail_next
            self.fail_next = None
            return {"id": message_id, "type": "result", "success": False, "error": error}

        def fail(code, message):
            return {
                "id": message_id,
                "type": "result",
                "success": False,
                "error": {"code": code, "message": message},
            }

        def ok(result):
            # Serialized on the way out, as a real instance necessarily is: a
            # client can never end up holding a reference into server state,
            # so a test cannot pass because both sides share one object.
            return {
                "id": message_id,
                "type": "result",
                "success": True,
                "result": json.loads(json.dumps(result)),
            }

        allowed = WS_COMMAND_KEYS.get(type_)
        if allowed is not None:
            extra = sorted(set(command) - {"id", "type"} - set(allowed))
            if extra:
                return fail("invalid_format", f"extra keys not allowed @ data[{extra[0]!r}]")

        if type_ == "config/entity_registry/list":
            return ok(self.entities)
        if type_ == "config/area_registry/list":
            return ok(self.areas)
        if type_ == "config/device_registry/list":
            return ok(self.devices)
        if type_ == "config/floor_registry/list":
            return ok([])
        if type_ == "config/entity_registry/get":
            for entry in self.entities:
                if entry["entity_id"] == command.get("entity_id"):
                    return ok(entry)
            return fail("not_found", "Entity not found")
        if type_ == "config/entity_registry/update":
            for entry in self.entities:
                if entry["entity_id"] != command.get("entity_id"):
                    continue
                for key in ENTITY_UPDATE_FIELDS:
                    if key in command:
                        entry[key] = command[key]
                if "new_entity_id" in command:
                    entry["entity_id"] = command["new_entity_id"]
                # Home Assistant answers with the registry entry that now
                # exists, not with the request that produced it: every stored
                # field, including the ones the request never mentioned, and an
                # `area_id` that stays null when the entity's area comes from
                # its device. A double that echoed the request instead would
                # let a client report an entity's area from its own payload and
                # never be contradicted.
                return ok({"entity_entry": entry})
            return fail("not_found", "Entity not found")
        if type_ == "config/area_registry/create":
            area = {
                "area_id": command["name"].lower().replace(" ", "_"),
                "name": command["name"],
                "icon": command.get("icon"),
                "floor_id": command.get("floor_id"),
                "aliases": [],
            }
            self.areas.append(area)
            return ok(area)
        if type_ == "config/area_registry/update":
            for area in self.areas:
                if area["area_id"] != command.get("area_id"):
                    continue
                for key in ("name", "icon", "floor_id"):
                    if key in command:
                        area[key] = command[key]
                return ok(area)
            return fail("not_found", "Area not found")
        return fail("unknown_command", f"Unknown command {type_}")

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        from websockets.sync.server import serve

        self._server = serve(self._handler, "127.0.0.1", 0)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()

    @property
    def port(self):
        return self._server.socket.getsockname()[1]


class FakeInstallation:
    """Both transports on one base URL, which is what the CLI expects.

    Home Assistant serves REST and WebSocket from a single origin, and the CLI
    derives the WebSocket URL from ``HA_URL`` accordingly. The two doubles are
    separate servers on separate ports, so a command that needs both --
    ``state list --area``, ``doctor`` -- had no single ``HA_URL`` to run
    against, and could only be exercised by hand-wiring a Context.

    This restores the real topology with a front door: it reads the request
    line of each incoming connection and splices the connection to the
    WebSocket double for ``/api/websocket`` and to the REST double for
    everything else. Routing on the request line alone means neither double
    changes and no request body is ever parsed here.
    """

    WS_PATH = "/api/websocket"

    def __init__(self, rest, ws):
        self.rest = rest
        self.ws = ws
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._accept_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._closing.set()
        with suppress(OSError):
            # Closing a socket another thread is blocked in accept() on does
            # not reliably wake it, so knock once and let the loop notice.
            socket.create_connection(self._listener.getsockname(), timeout=1).close()
        self._thread.join(timeout=5)
        with suppress(OSError):
            self._listener.close()

    @property
    def url(self):
        host, port = self._listener.getsockname()[:2]
        return f"http://{host}:{port}"

    @property
    def environ(self):
        return {"HA_URL": self.url, "HA_TOKEN": FAKE_TOKEN}

    # -- routing -----------------------------------------------------------

    def _accept_forever(self):
        while not self._closing.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                # The listener was closed by stop(); nothing further arrives.
                return
            if self._closing.is_set():
                client.close()
                return
            threading.Thread(target=self._route, args=(client,), daemon=True).start()

    def _route(self, client):
        upstream = None
        try:
            head = self._read_request_line(client)
            if not head:
                return
            parts = head.split(" ")
            path = parts[1].split("?")[0] if len(parts) > 1 else ""
            port = self.ws.port if path == self.WS_PATH else self.rest.port
            upstream = socket.create_connection(("127.0.0.1", port))
            upstream.sendall(head.encode("latin-1"))
            # One direction per thread, and this one blocks until the upstream
            # is done, so both sockets close exactly once the exchange ends.
            back = threading.Thread(target=self._splice, args=(upstream, client), daemon=True)
            back.start()
            self._splice(client, upstream)
            back.join(timeout=5)
        except OSError:
            pass
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    with suppress(OSError):
                        sock.close()

    @staticmethod
    def _read_request_line(client) -> str:
        """Read only the request line, which is all the routing decision needs."""
        line = b""
        while not line.endswith(b"\n"):
            byte = client.recv(1)
            if not byte or len(line) > 8192:
                return ""
            line += byte
        return line.decode("latin-1")

    @staticmethod
    def _splice(src, dst) -> None:
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            with suppress(OSError):
                dst.shutdown(socket.SHUT_WR)


@pytest.fixture(autouse=True)
def _clean_secrets():
    output.reset_secrets()
    yield
    output.reset_secrets()


@pytest.fixture
def rest_server():
    server = FakeRestServer().start()
    yield server
    server.stop()


@pytest.fixture
def ws_server():
    server = FakeWsServer().start()
    yield server
    server.stop()


@pytest.fixture
def rest_env(rest_server):
    return {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN}


@pytest.fixture
def ws_env(ws_server):
    return {"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": FAKE_TOKEN}


@pytest.fixture
def installation(rest_server, ws_server):
    server = FakeInstallation(rest_server, ws_server).start()
    yield server
    server.stop()


@pytest.fixture
def installation_env(installation):
    """One HA_URL serving REST and WebSocket, as a real instance does.

    Use this for anything that crosses transports; `rest_env` and `ws_env`
    stay the cheaper choice when only one is in play.
    """
    return installation.environ


@pytest.fixture
def run_cli(capsys):
    """Invoke the CLI exactly as a shell would and return (exit code, stdout)."""
    from ha_axi.cli import main

    def invoke(argv, environ=None):
        capsys.readouterr()
        code = main(list(argv), environ=dict(environ or {}))
        return code, capsys.readouterr().out

    return invoke
