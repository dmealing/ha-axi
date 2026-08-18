"""`ha-axi ws` -- an escape hatch to any WebSocket command.

Every registry operation the typed commands perform is declared in
:data:`ha_axi.ws.REGISTRY`; this command exposes that table directly, so a
capability Home Assistant adds is reachable before a typed wrapper exists.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..output import HelpBlock
from ..ws import REGISTRY
from ._common import parse_json_flag, parse_pairs, plural

COMMAND = Command(
    name="ws",
    summary="Send a command over the Home Assistant WebSocket API",
    usage="usage: ha-axi ws <command> [flags]",
    default_sub="ws",
    subs=(
        Sub(
            name="ws",
            args=("[command]",),
            summary="Send one WebSocket command by name or by raw type",
            flags=(
                Flag(
                    "--param",
                    "<key=value>",
                    repeat=True,
                    note="value parsed as JSON when it parses",
                ),
                Flag("--params-json", "<object>", note="merged over --param"),
                Flag("--list", boolean=True, note="show the declared commands and exit"),
                Flag("--raw", boolean=True, note="treat <command> as a literal API type"),
            ),
        ),
    ),
    notes=(
        "declared names are stable; --raw passes any type straight through to the API",
        "--params-json takes a whole JSON object; --param takes repeated key=value pairs",
    ),
    examples=(
        "ha-axi ws --list",
        "ha-axi ws entity.list",
        "ha-axi ws area.update --param area_id=example_room --param name='Example Study'",
        "ha-axi ws --raw config/floor_registry/list",
    ),
)


def run(ctx, sub: str, parsed):
    if parsed.get("list") or not parsed.positionals:
        return _list(parsed)

    name = parsed.positionals[0]
    params = parse_pairs(parsed.get("param", []), flag="--param")
    params.update(parse_json_flag(parsed.get("params_json"), flag="--params-json"))

    if parsed.get("raw"):
        type_ = name
    else:
        command = REGISTRY.get(name)
        if command is None:
            if "/" in name:
                raise UsageError(
                    f"{name!r} looks like a raw API type, which needs --raw",
                    help_lines=[f"Run `ha-axi ws --raw {name}`"],
                    code="UNKNOWN_COMMAND",
                )
            raise UsageError(
                f"unknown websocket command: {name}",
                help_lines=[
                    f"declared commands: {', '.join(sorted(REGISTRY))}",
                    "Run `ha-axi ws --list` to see each command's parameters",
                ],
                code="UNKNOWN_COMMAND",
            )
        missing = [key for key in command.required if key not in params]
        if missing:
            raise UsageError(
                f"{name} needs {', '.join(missing)}",
                help_lines=[
                    f"Run `ha-axi ws {name} "
                    + " ".join(f"--param {k}=<value>" for k in command.required)
                    + "`"
                ],
                code="MISSING_PARAM",
            )
        type_ = command.type

    with ctx.ws() as client:
        result = client.send_command(type_, params)

    doc = {"command": {"name": name, "type": type_}}
    if result is None:
        doc["result"] = f"{type_} succeeded with an empty result"
    else:
        doc["result"] = result
    return doc


def _list(parsed):
    rows = [
        {
            "command": command.name,
            "type": command.type,
            "params": ",".join(command.params) or "",
        }
        for command in sorted(REGISTRY.values(), key=lambda c: c.name)
    ]
    return {
        "count": f"{plural(len(rows), 'declared command')}",
        "commands": rows,
        "help": HelpBlock(
            [
                "Run `ha-axi ws <command> --param key=value` to send one",
                "Run `ha-axi ws --raw <api/type>` for a command that is not declared here",
            ]
        ),
    }
