"""`ha-axi entity` -- the entity registry, which is WebSocket-only.

The registry is where an entity's stable identity lives: its user-set name, the
area it belongs to, the integration that supplied it. None of that is reachable
over REST, which is why this command exists.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import NotFound, UsageError
from ..output import HelpBlock
from ._common import (
    area_name_map,
    device_area_map,
    domain_of,
    effective_area_id,
    matches_search,
    parse_limit,
    project,
    registry_name,
    resolve_area,
    select_fields,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = [
    "entity_id",
    "name",
    "area",
    "area_id",
    "platform",
    "domain",
    "device_id",
    "original_name",
    "disabled",
    "hidden",
    "entity_category",
]
DEFAULT_LIST_FIELDS = ["entity_id", "name", "area"]

COMMAND = Command(
    name="entity",
    summary="Read and update the entity registry over the WebSocket API",
    usage="usage: ha-axi entity <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            summary="List entity registry entries",
            flags=(
                Flag("--area", "<id|name>", note="'none' selects unassigned entities"),
                Flag("--domain", "<name>", repeat=True),
                Flag("--platform", "<name>"),
                Flag("--search", "<text>", note="matches entity_id and name"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
        Sub(name="get", args=("<entity_id>",), summary="Show one registry entry"),
        Sub(
            name="update",
            args=("<entity_id>",),
            summary="Set an entity's name, area or icon",
            flags=(
                Flag("--name", "<text>"),
                Flag("--area", "<id|name>"),
                Flag("--icon", "<mdi:name>"),
                Flag("--new-id", "<entity_id>", note="rename the entity_id itself"),
                Flag("--clear-name", boolean=True, note="fall back to the integration's name"),
                Flag("--clear-area", boolean=True),
                Flag("--clear-icon", boolean=True),
            ),
        ),
    ),
    notes=(
        "an entity's area is inherited from its device until it is set here explicitly",
        "entity_ids are not stable identity: filter by --area or --search, not by guessing ids",
    ),
    examples=(
        "ha-axi entity list --area 'Example Room'",
        "ha-axi entity list --domain light --fields entity_id,name,area,platform",
        "ha-axi entity list --area none --limit 500",
        "ha-axi entity get light.example_lamp",
        "ha-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    if sub == "get":
        return _get(ctx, parsed)
    return _update(ctx, parsed)


def _snapshot(client):
    entities = client.run("entity.list") or []
    areas = client.run("area.list") or []
    devices = client.run("device.list") or []
    return entities, areas, devices


def _row(entry: dict, area_names: dict, device_areas: dict) -> dict:
    entity_id = entry.get("entity_id", "")
    area_id = effective_area_id(entry, device_areas)
    return {
        "entity_id": entity_id,
        "name": registry_name(entry),
        "area": area_names.get(area_id, "") if area_id else "",
        "area_id": area_id,
        "platform": entry.get("platform", ""),
        "domain": domain_of(entity_id),
        "device_id": entry.get("device_id") or "",
        "original_name": entry.get("original_name") or "",
        "disabled": bool(entry.get("disabled_by")),
        "hidden": bool(entry.get("hidden_by")),
        "entity_category": entry.get("entity_category") or "",
    }


def _list(ctx, parsed):
    with ctx.ws() as client:
        entities, areas, devices = _snapshot(client)

    area_names = area_name_map(areas)
    device_areas = device_area_map(devices)
    rows = [_row(entry, area_names, device_areas) for entry in entities]
    total = len(rows)

    area_filter = parsed.get("area")
    scope = []
    if area_filter:
        if area_filter.strip().lower() in ("none", "null", ""):
            rows = [row for row in rows if not row["area_id"]]
            scope.append("with no area")
        else:
            area = resolve_area(areas, area_filter)
            rows = [row for row in rows if row["area_id"] == area.get("area_id")]
            scope.append(f"in area {area.get('name')}")

    domains = [d.strip().lower() for d in parsed.get("domain", []) if d.strip()]
    if domains:
        rows = [row for row in rows if row["domain"].lower() in domains]
        scope.append(f"in domain {'|'.join(domains)}")

    platform = parsed.get("platform")
    if platform:
        rows = [row for row in rows if row["platform"].lower() == platform.strip().lower()]
        scope.append(f"from platform {platform}")

    search = parsed.get("search")
    if search:
        rows = [
            row
            for row in rows
            if matches_search(search, row["entity_id"], row["name"], row["original_name"])
        ]
        scope.append(f"matching {search!r}")

    matched = len(rows)
    if not rows:
        where = " ".join(scope) or "in this installation"
        return {
            "entities": f"0 registry entries found {where}",
            "total": f"{total} entries in the entity registry",
            "help": HelpBlock(
                [
                    "Run `ha-axi entity list` with no filters to see every entry",
                    "Run `ha-axi area list` to see the areas that exist",
                ]
            ),
        }

    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    fields = select_fields(parsed.get("fields"), LIST_FIELDS, DEFAULT_LIST_FIELDS)
    shown = rows[:limit]

    count = (
        f"{len(shown)} of {matched} matched ({total} total)"
        if scope
        else f"{len(shown)} of {total} total"
    )
    help_lines = ["Run `ha-axi entity get <entity_id>` for one entry in full"]
    if len(shown) < matched:
        help_lines.append(f"Run `ha-axi entity list --limit {matched}` to see all {matched}")
    help_lines.append(
        'Run `ha-axi entity update <entity_id> --name "<name>" --area <id|name>` to change one'
    )

    return {"count": count, "entities": project(shown, fields), "help": HelpBlock(help_lines)}


def _find(entities: list, entity_id: str) -> dict:
    for entry in entities:
        if entry.get("entity_id") == entity_id:
            return entry
    raise NotFound(
        f"no registry entry for {entity_id}",
        help_lines=[
            f"Run `ha-axi entity list --search {entity_id.split('.')[-1]}` to find it",
            "Run `ha-axi state get <entity_id>` if the entity exists but is not registered",
        ],
        code="NO_SUCH_ENTITY",
    )


def _get(ctx, parsed):
    entity_id = parsed.positionals[0]
    with ctx.ws() as client:
        entities, areas, devices = _snapshot(client)
    entry = _find(entities, entity_id)
    row = _row(entry, area_name_map(areas), device_area_map(devices))
    row["unique_id"] = entry.get("unique_id") or ""
    row["icon"] = entry.get("icon") or ""
    row["area_source"] = "entity" if entry.get("area_id") else ("device" if row["area_id"] else "")
    return {"entity": row}


def _update(ctx, parsed):
    entity_id = parsed.positionals[0]
    changes: dict = {}
    if parsed.get("clear_name"):
        changes["name"] = None
    if parsed.get("name") is not None:
        changes["name"] = parsed.get("name")
    if parsed.get("clear_icon"):
        changes["icon"] = None
    if parsed.get("icon") is not None:
        changes["icon"] = parsed.get("icon")
    if parsed.get("new_id") is not None:
        changes["new_entity_id"] = parsed.get("new_id")

    clear_area = parsed.get("clear_area")
    area_arg = parsed.get("area")
    if clear_area and area_arg:
        raise UsageError(
            "--area and --clear-area are mutually exclusive",
            help_lines=[f"Run `ha-axi entity update {entity_id} --clear-area`"],
            code="CONFLICTING_FLAGS",
        )

    if not changes and not clear_area and area_arg is None:
        raise UsageError(
            "nothing to update",
            help_lines=[
                f'Run `ha-axi entity update {entity_id} --name "<name>"` to set the name',
                f"Run `ha-axi entity update {entity_id} --area <id|name>` to move it",
            ],
            code="NO_CHANGES",
        )

    with ctx.ws() as client:
        entities = client.run("entity.list") or []
        areas = client.run("area.list") or []
        current = _find(entities, entity_id)

        if clear_area:
            changes["area_id"] = None
        elif area_arg is not None:
            changes["area_id"] = resolve_area(areas, area_arg).get("area_id")

        # Idempotent: a request that asks for the state already stored is a no-op.
        pending = {k: v for k, v in changes.items() if _differs(current, k, v)}
        if not pending:
            return {
                "entity": entity_id,
                "updated": "already matches the requested values, no change made",
                "name": registry_name(current),
                "area": area_name_map(areas).get(current.get("area_id") or "", ""),
            }

        result = client.run("entity.update", {"entity_id": entity_id, **pending})

    entry = (result or {}).get("entity_entry") if isinstance(result, dict) else None
    entry = entry or current
    area_names = area_name_map(areas)
    return {
        "entity": entry.get("entity_id", entity_id),
        "updated": sorted(pending),
        "name": registry_name(entry),
        "area": area_names.get(entry.get("area_id") or "", ""),
        "area_id": entry.get("area_id") or "",
    }


def _differs(current: dict, key: str, value) -> bool:
    if key == "new_entity_id":
        return current.get("entity_id") != value
    return (current.get(key) or None) != (value or None)
