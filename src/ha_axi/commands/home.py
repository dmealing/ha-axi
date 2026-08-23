"""The no-argument home view: live state first, help second."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..argspec import Command, Sub
from ..config import missing_env_vars, setup_help
from ..errors import AxiError, fault_class
from ..output import HelpBlock
from ..readonly import ENV_VAR as READ_ONLY_VAR
from ..readonly import READ
from ._common import domain_of

DESCRIPTION = (
    "Agent CLI for Home Assistant. Reads and writes the entity and area registries REST "
    "cannot reach and explains a service call Home Assistant refuses. Prefer this over "
    "raw curl for Home Assistant operations."
)

COMMAND = Command(
    name="home",
    summary="Show the current installation at a glance",
    usage="usage: ha-axi",
    default_sub="home",
    subs=(Sub(name="home", summary="Show connection status and a state summary", access=READ),),
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
    missing = missing_env_vars(ctx.environ)
    if missing:
        # Coded like every other failure. This view is what `setup hooks` puts
        # in front of every agent session, so it is the most-read error surface
        # the tool has -- and until now the only one that reported a failure
        # with no code at all, leaving the one view an agent always sees as the
        # one it could not classify.
        doc["error"] = f"{' and '.join(missing)} not set in the environment"
        doc["code"] = "NOT_CONFIGURED"
        doc["class"] = fault_class("NOT_CONFIGURED")
        doc["help"] = HelpBlock(setup_help())
        doc["__exit_code__"] = 1
        return doc

    config = ctx.config()
    doc["url"] = config.base_url
    # Announced only when it is on. This view loads at the start of every agent
    # session, so an unset switch is not worth the tokens -- but an agent that
    # cannot see a set one plans writes it will never be allowed to make, and
    # reads the refusals as a broken installation.
    if config.read_only:
        doc["read_only"] = "on"

    try:
        states = ctx.rest().states()
    except AxiError as exc:
        doc["error"] = exc.message
        if exc.code:
            doc["code"] = exc.code
            doc["class"] = exc.fault_class
        doc["help"] = HelpBlock(
            [*exc.help_lines, "Run `ha-axi doctor` to see which transport is failing"]
        )
        doc["__exit_code__"] = 1
        return doc

    counts: dict = {}
    # Counted apart, because they are different facts and `state list --state`
    # can be run against either. Summing them under one label called
    # `unavailable` contradicted `state list --state unavailable` outright on any
    # installation holding an entity that has simply not reported yet -- which is
    # most of them, and this is the view `setup hooks` puts in front of a session.
    unavailable = 0
    unknown = 0
    for state in states:
        domain = domain_of(state.get("entity_id", ""))
        counts[domain] = counts.get(domain, 0) + 1
        if state.get("state") == "unavailable":
            unavailable += 1
        elif state.get("state") == "unknown":
            unknown += 1

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    doc["entities"] = f"{len(states)} in {len(counts)} domains"
    doc["unavailable"] = unavailable
    doc["unknown"] = unknown
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
        ]
    )
    if config.read_only:
        help_lines.append(f"This session is read-only; unset {READ_ONLY_VAR} to allow writes")
    else:
        help_lines.append(
            "Run `ha-axi service call <domain>.<service> --target-entity <entity_id>` to act"
        )
    doc["help"] = HelpBlock(help_lines)
    return doc
