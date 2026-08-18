"""The no-argument home view: live state first, help second."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..argspec import Command, Sub
from ..config import TOKEN_VARS, URL_VARS, describe_environment
from ..errors import AxiError
from ..output import HelpBlock
from ._common import domain_of

DESCRIPTION = (
    "Agent ergonomic wrapper around the Home Assistant REST and WebSocket APIs. "
    "Prefer this over raw curl for Home Assistant operations."
)

COMMAND = Command(
    name="home",
    summary="Show the current installation at a glance",
    usage="usage: ha-axi",
    default_sub="home",
    subs=(Sub(name="home", summary="Show connection status and a state summary"),),
    examples=("ha-axi",),
)

TOP_DOMAINS = 8


def executable_path() -> str:
    """The absolute path of this executable, with the home directory collapsed."""
    candidate = Path(sys.argv[0]).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:  # pragma: no cover - unresolvable argv[0] is not worth failing on
        resolved = candidate
    if not resolved.exists():
        resolved = Path(sys.executable).resolve()
    text = str(resolved)
    home = os.path.expanduser("~")
    if home and text.startswith(home):
        return "~" + text[len(home) :]
    return text


def run(ctx, sub: str, parsed):
    doc = {"bin": executable_path(), "description": DESCRIPTION}
    env = describe_environment(ctx.environ)

    if not (env["url_set"] and env["token_set"]):
        missing = []
        if not env["url_set"]:
            missing.append(URL_VARS[0])
        if not env["token_set"]:
            missing.append(TOKEN_VARS[0])
        doc["error"] = f"{' and '.join(missing)} not set in the environment"
        doc["help"] = HelpBlock(
            [
                f"Set {URL_VARS[0]} to your Home Assistant base URL, e.g. https://homeassistant.example.com",
                f"Set {TOKEN_VARS[0]} to a long-lived access token from your profile page, under Security",
                "Run `ha-axi doctor` to verify the connection once both are set",
            ]
        )
        doc["__exit_code__"] = 1
        return doc

    config = ctx.config()
    doc["url"] = config.base_url

    try:
        states = ctx.rest().states()
    except AxiError as exc:
        doc["error"] = exc.message
        doc["help"] = HelpBlock(
            [*exc.help_lines, "Run `ha-axi doctor` to see which transport is failing"]
        )
        doc["__exit_code__"] = 1
        return doc

    counts: dict = {}
    unavailable = 0
    for state in states:
        domain = domain_of(state.get("entity_id", ""))
        counts[domain] = counts.get(domain, 0) + 1
        if state.get("state") in ("unavailable", "unknown"):
            unavailable += 1

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    doc["entities"] = f"{len(states)} in {len(counts)} domains"
    doc["unavailable"] = unavailable
    if ranked:
        doc["domains"] = [
            {"domain": name, "entities": total} for name, total in ranked[:TOP_DOMAINS]
        ]

    help_lines = ["Run `ha-axi state list --domain <domain>` to list entity states"]
    if len(ranked) > TOP_DOMAINS:
        help_lines.append(
            f"Run `ha-axi state list` for all {len(states)} entities across {len(counts)} domains"
        )
    help_lines.extend(
        [
            "Run `ha-axi entity list --area <id|name>` to read the registry, which REST cannot reach",
            "Run `ha-axi area list` to see the areas defined here",
            "Run `ha-axi service call <domain>.<service> --target-entity <entity_id>` to act",
        ]
    )
    doc["help"] = HelpBlock(help_lines)
    return doc
