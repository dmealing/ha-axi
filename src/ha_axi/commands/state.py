"""`ha-axi state` -- entity states over the REST API.

State is the runtime view: what an entity is doing right now. The registry view
-- names, areas, platforms -- lives under `ha-axi entity`, which speaks the
WebSocket API instead.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..output import HelpBlock, truncate
from ._common import (
    PREVIEW_CHARS,
    count_line,
    domain_of,
    friendly_name,
    matches_search,
    parse_limit,
    project,
    select_fields,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = ["entity_id", "name", "state", "domain", "last_changed", "last_updated"]
DEFAULT_LIST_FIELDS = ["entity_id", "name", "state"]

COMMAND = Command(
    name="state",
    summary="Read entity states from the Home Assistant REST API",
    usage="usage: ha-axi state <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            summary="List entity states",
            flags=(
                Flag("--domain", "<name>", repeat=True, note="repeat to widen"),
                Flag("--state", "<value>", note="exact match"),
                Flag("--search", "<text>", note="matches entity_id and name"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
        Sub(
            name="get",
            args=("<entity_id>",),
            summary="Show one entity state with its attributes",
            flags=(Flag("--full", boolean=True, note="do not truncate long attributes"),),
        ),
    ),
    notes=("state is the runtime view; run `ha-axi entity list` for registry names and areas",),
    examples=(
        "ha-axi state list --domain light",
        "ha-axi state list --search lamp --limit 20",
        "ha-axi state list --domain sensor --state unavailable",
        "ha-axi state get light.example_lamp",
        "ha-axi state get media_player.example_speaker --full",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    return _get(ctx, parsed)


def _row(state: dict) -> dict:
    return {
        "entity_id": state.get("entity_id", ""),
        "name": friendly_name(state),
        "state": state.get("state", ""),
        "domain": domain_of(state.get("entity_id", "")),
        "last_changed": state.get("last_changed", ""),
        "last_updated": state.get("last_updated", ""),
    }


def _list(ctx, parsed):
    states = ctx.rest().states()
    rows = [_row(state) for state in states]
    total = len(rows)

    domains = [d.strip().lower() for d in parsed.get("domain", []) if d.strip()]
    if domains:
        rows = [row for row in rows if row["domain"].lower() in domains]
    wanted_state = parsed.get("state")
    if wanted_state:
        rows = [row for row in rows if row["state"] == wanted_state]
    search = parsed.get("search")
    if search:
        rows = [row for row in rows if matches_search(search, row["entity_id"], row["name"])]

    matched = len(rows)
    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    fields = select_fields(parsed.get("fields"), LIST_FIELDS, DEFAULT_LIST_FIELDS)
    shown = rows[:limit]

    filtered = bool(domains or wanted_state or search)
    if not shown:
        scope = _scope(domains, wanted_state, search)
        return {
            "states": f"0 entity states found{scope}",
            "total": f"{total} entities in this installation",
            "help": HelpBlock(
                [
                    "Run `ha-axi state list` with no filters to see every entity",
                    "Run `ha-axi entity list` to read the registry, which includes disabled entities",
                ]
            ),
        }

    count = count_line(len(shown), matched, total, filtered=filtered)

    help_lines = ["Run `ha-axi state get <entity_id>` for one entity's full attributes"]
    if len(shown) < matched:
        help_lines.append(f"Run `ha-axi state list --limit {matched}` to see all {matched}")
    if not domains:
        help_lines.append("Run `ha-axi state list --domain light` to narrow by domain")

    return {
        "count": count,
        "states": project(shown, fields),
        "help": HelpBlock(help_lines),
    }


def _scope(domains, wanted_state, search) -> str:
    parts = []
    if domains:
        parts.append(f"in domain {'|'.join(domains)}")
    if wanted_state:
        parts.append(f"with state {wanted_state}")
    if search:
        parts.append(f"matching {search!r}")
    return f" {' '.join(parts)}" if parts else " in this installation"


def _get(ctx, parsed):
    entity_id = parsed.positionals[0]
    state = ctx.rest().state(entity_id)
    attributes = dict(state.get("attributes") or {})
    full = parsed.get("full", False)

    hint = ""
    if not full:
        for key, value in list(attributes.items()):
            if isinstance(value, str) and len(value) > PREVIEW_CHARS:
                attributes[key], hint = truncate(
                    value,
                    PREVIEW_CHARS,
                    f"Run `ha-axi state get {entity_id} --full` to see complete attributes",
                )

    doc = {
        "state": {
            "entity_id": state.get("entity_id", entity_id),
            "name": friendly_name(state),
            "state": state.get("state", ""),
            "domain": domain_of(state.get("entity_id", entity_id)),
            "last_changed": state.get("last_changed", ""),
            "last_updated": state.get("last_updated", ""),
        },
        "attributes": attributes if attributes else {},
    }
    if not attributes:
        doc["attributes"] = "0 attributes on this entity"
    if hint:
        doc["help"] = HelpBlock([hint])
    return doc
