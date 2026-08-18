"""Entry point: global flags, dispatch, help, and the single error boundary."""

from __future__ import annotations

import os
import sys

from . import __version__, output
from . import config as config_module
from .argspec import Command, parse, render_command_help
from .commands import api as api_command
from .commands import area as area_command
from .commands import device as device_command
from .commands import doctor as doctor_command
from .commands import entity as entity_command
from .commands import home as home_command
from .commands import service as service_command
from .commands import setup as setup_command
from .commands import state as state_command
from .commands import template as template_command
from .commands import wscmd as ws_command
from .errors import EXIT_ERROR, EXIT_OK, AxiError, UsageError
from .output import MODE_HUMAN, MODE_JSON, MODE_TOON, HelpBlock
from .rest import RestClient
from .ws import WsClient

#: Dispatch order, which is also the order `--help` and the skill list them in.
COMMAND_ORDER = (
    "state",
    "service",
    "template",
    "entity",
    "area",
    "device",
    "ws",
    "api",
    "doctor",
    "setup",
)

_MODULES = {
    "state": state_command,
    "service": service_command,
    "template": template_command,
    "entity": entity_command,
    "area": area_command,
    "device": device_command,
    "ws": ws_command,
    "api": api_command,
    "doctor": doctor_command,
    "setup": setup_command,
    "home": home_command,
}

#: Commands an agent might reach for under a different noun.
_ALIASES = {
    "states": "state",
    "entities": "entity",
    "areas": "area",
    "devices": "device",
    "services": "service",
    "templates": "template",
    "rooms": "area",
    "room": "area",
    "registry": "entity",
    "websocket": "ws",
    "rest": "api",
    "health": "doctor",
    "status": "doctor",
}


def command_specs() -> dict:
    return {name: module.COMMAND for name, module in _MODULES.items()}


class Context:
    """Per-invocation state: configuration, transports and output mode."""

    def __init__(self, environ, *, mode: str = MODE_TOON, timeout: float | None = None) -> None:
        self.environ = environ
        self.mode = mode
        self.timeout = timeout
        self._config = None
        self._rest: RestClient | None = None

    def config(self):
        if self._config is None:
            self._config = config_module.load(self.environ, timeout=self.timeout)
            # Registered before any transport runs, so a token can never appear
            # in an error message or a debug line.
            output.register_secret(self._config.token)
        return self._config

    def rest(self) -> RestClient:
        if self._rest is None:
            self._rest = RestClient(self.config())
        return self._rest

    def ws(self) -> WsClient:
        return WsClient(self.config())


# ----------------------------------------------------------------- help text


def render_root_help() -> str:
    specs = command_specs()
    names = ", ".join(COMMAND_ORDER)
    lines = [
        "usage: ha-axi [command] [subcommand] [args] [flags]",
        f"description: {home_command.DESCRIPTION}",
        f"commands[{len(COMMAND_ORDER) + 1}]:",
        f"  (none)=home, {names}",
        "flags[6]:",
        "  --human (readable output), --json (raw JSON output), --timeout <seconds> (default 30),",
        "  --debug (diagnostics on stderr), --help, -v/--version",
        "env[2]:",
        "  HA_URL (or HASS_SERVER) - Home Assistant base URL, e.g. https://homeassistant.example.com",
        "  HA_TOKEN (or HASS_TOKEN) - long-lived access token; there is deliberately no --token flag",
        f"summaries[{len(COMMAND_ORDER)}]:",
    ]
    width = max(len(name) for name in COMMAND_ORDER)
    lines.extend(f"  {name.ljust(width)}  {specs[name].summary}" for name in COMMAND_ORDER)
    lines.extend(
        [
            "examples:",
            "  ha-axi",
            "  ha-axi state list --domain light",
            "  ha-axi entity list --area 'Example Room'",
            "  ha-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room",
            "  ha-axi service call light.turn_on --target-entity light.example_lamp",
            "  ha-axi doctor",
        ]
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ dispatch


def _split_globals(argv: list) -> tuple:
    """Pull global flags off the front of the invocation, before the command."""
    globals_: dict = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("-") or token == "-":
            break
        name, sep, inline = token.partition("=")
        if name in ("--timeout",):
            index += 1
            if sep:
                globals_["timeout"] = inline
            elif index < len(argv):
                globals_["timeout"] = argv[index]
                index += 1
            continue
        if name in ("--human", "--json", "--debug", "--help", "-h", "--version", "-v", "-V"):
            globals_[name.lstrip("-")] = True
            index += 1
            continue
        break
    return globals_, argv[index:]


def _resolve_timeout(raw) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise UsageError(
            f"--timeout needs a number of seconds, got {raw!r}",
            help_lines=["Run `ha-axi --timeout 60 <command>`"],
            code="BAD_TIMEOUT",
        ) from None
    if value <= 0:
        raise UsageError(
            f"--timeout must be greater than 0, got {value:g}",
            help_lines=["Run `ha-axi --timeout 60 <command>`"],
            code="BAD_TIMEOUT",
        )
    return value


def _mode(globals_: dict) -> str:
    if globals_.get("json"):
        return MODE_JSON
    if globals_.get("human"):
        return MODE_HUMAN
    return MODE_TOON


def _unknown_command(name: str):
    suggestion = _ALIASES.get(name.lower())
    if suggestion:
        return UsageError(
            f"unknown command: {name}; use `{suggestion}` instead",
            help_lines=[f"Run `ha-axi {suggestion} --help` for its subcommands"],
            code="UNKNOWN_COMMAND",
        )
    return UsageError(
        f"unknown command: {name}",
        help_lines=[
            f"commands: {', '.join(COMMAND_ORDER)}",
            "Run `ha-axi --help` for the full reference",
        ],
        code="UNKNOWN_COMMAND",
    )


def _pick_sub(command: Command, argv: list) -> tuple:
    if argv and not argv[0].startswith("-"):
        sub = command.find(argv[0])
        if sub is not None:
            return sub, argv[1:]
    if command.default_sub:
        sub = command.find(command.default_sub)
        if sub is not None:
            return sub, argv
    if argv and not argv[0].startswith("-"):
        raise UsageError(
            f"unknown subcommand `{argv[0]}` for `{command.name}`",
            help_lines=[
                f"subcommands: {', '.join(s.name for s in command.subs)}",
                f"Run `ha-axi {command.name} --help` for the full reference",
            ],
            code="UNKNOWN_SUBCOMMAND",
        )
    raise UsageError(
        f"`{command.name}` needs a subcommand",
        help_lines=[
            f"subcommands: {', '.join(s.name for s in command.subs)}",
            f"Run `ha-axi {command.name} --help` for the full reference",
        ],
        code="MISSING_SUBCOMMAND",
    )


def _error_document(exc: AxiError) -> dict:
    doc: dict = {"error": exc.message}
    if exc.code:
        doc["code"] = exc.code
    if exc.help_lines:
        doc["help"] = HelpBlock(exc.help_lines)
    return doc


def main(argv: list | None = None, *, environ=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ

    globals_, rest = _split_globals(argv)
    mode = _mode(globals_)

    try:
        if globals_.get("version") or globals_.get("v") or globals_.get("V"):
            output.write({"ha-axi": __version__}, mode)
            return EXIT_OK

        if not rest:
            if globals_.get("help") or globals_.get("h"):
                output.write_text(render_root_help())
                return EXIT_OK
            command, sub_name, sub_argv = home_command.COMMAND, "home", []
        else:
            name = rest[0]
            module = _MODULES.get(name)
            if module is None or name == "home":
                raise _unknown_command(name)
            command = module.COMMAND
            if (
                globals_.get("help")
                or globals_.get("h")
                or "--help" in rest[1:]
                or "-h" in rest[1:]
            ):
                output.write_text(render_command_help(command))
                return EXIT_OK
            sub, sub_argv = _pick_sub(command, rest[1:])
            sub_name = sub.name

        if command is home_command.COMMAND:
            from .argspec import Parsed

            parsed = Parsed()
            module = home_command
        else:
            parsed = parse(sub, sub_argv, command=command)
            module = _MODULES[command.name]
            globals_.update(parsed.globals)
            mode = _mode(globals_)

        if globals_.get("debug"):
            environ = dict(environ)
            environ["HA_AXI_DEBUG"] = "1"

        ctx = Context(environ, mode=mode, timeout=_resolve_timeout(globals_.get("timeout")))
        doc = module.run(ctx, sub_name, parsed)
    except AxiError as exc:
        output.write(_error_document(exc), mode)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive interruption
        output.write({"error": "interrupted"}, mode)
        return EXIT_ERROR

    exit_code = EXIT_OK
    if isinstance(doc, dict) and "__exit_code__" in doc:
        doc = dict(doc)
        exit_code = doc.pop("__exit_code__")
    output.write(doc, mode)
    return exit_code


def entrypoint() -> None:  # pragma: no cover - thin console-script shim
    sys.exit(main())
