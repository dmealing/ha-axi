"""`ha-axi device` -- the device registry, which is WebSocket-only."""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..output import HelpBlock
from ._common import (
    area_name_map,
    count_line,
    filter_by_area,
    matches_search,
    parse_limit,
    project,
    select_fields,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = [
    "device_id",
    "name",
    "area",
    "area_id",
    "manufacturer",
    "model",
    "entities",
]
DEFAULT_LIST_FIELDS = ["device_id", "name", "area"]

COMMAND = Command(
    name="device",
    summary="Read the device registry over the WebSocket API",
    usage="usage: ha-axi device list [flags]",
    default_sub="list",
    subs=(
        Sub(
            name="list",
            summary="List devices with their areas and entity counts",
            flags=(
                Flag("--area", "<id|name>", note="'none' selects unassigned devices"),
                Flag("--search", "<text>", note="matches name, manufacturer and model"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
    ),
    notes=("an entity with no area of its own inherits the area of its device",),
    examples=(
        "ha-axi device list",
        "ha-axi device list --area 'Example Room'",
        "ha-axi device list --search example --fields device_id,name,model",
    ),
)


def run(ctx, sub: str, parsed):
    with ctx.ws() as client:
        devices = client.run("device.list") or []
        areas = client.run("area.list") or []
        entities = client.run("entity.list") or []

    area_names = area_name_map(areas)
    entity_counts: dict = {}
    for entry in entities:
        device_id = entry.get("device_id")
        if device_id:
            entity_counts[device_id] = entity_counts.get(device_id, 0) + 1

    rows = [
        {
            "device_id": device.get("id", ""),
            "name": device.get("name_by_user") or device.get("name") or "",
            "area": area_names.get(device.get("area_id") or "", ""),
            "area_id": device.get("area_id") or "",
            "manufacturer": device.get("manufacturer") or "",
            "model": device.get("model") or "",
            "entities": entity_counts.get(device.get("id"), 0),
        }
        for device in devices
    ]
    total = len(rows)

    scope: list = []
    rows = filter_by_area(rows, areas, parsed.get("area"), scope)

    search = parsed.get("search")
    if search:
        rows = [
            row
            for row in rows
            if matches_search(search, row["name"], row["manufacturer"], row["model"])
        ]
        scope.append(f"matching {search!r}")

    matched = len(rows)
    if not rows:
        where = " ".join(scope) or "in this installation"
        return {
            "devices": f"0 devices found {where}",
            "total": f"{total} devices in the device registry",
            "help": HelpBlock(["Run `ha-axi device list` with no filters to see every device"]),
        }

    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    fields = select_fields(parsed.get("fields"), LIST_FIELDS, DEFAULT_LIST_FIELDS)
    shown = rows[:limit]
    count = count_line(len(shown), matched, total, filtered=bool(scope))
    help_lines = ["Run `ha-axi entity list --area <id|name>` to see the entities in an area"]
    if len(shown) < matched:
        help_lines.append(f"Run `ha-axi device list --limit {matched}` to see all {matched}")

    return {"count": count, "devices": project(shown, fields), "help": HelpBlock(help_lines)}
