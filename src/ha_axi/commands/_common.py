"""Helpers shared by the command modules."""

from __future__ import annotations

import json
from typing import Any

from ..errors import NotFound, UsageError

#: Preview length for long free-text values before `--full` is needed.
PREVIEW_CHARS = 1200


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def friendly_name(state: dict) -> str:
    attributes = state.get("attributes") or {}
    return attributes.get("friendly_name") or state.get("entity_id", "")


def registry_name(entry: dict) -> str:
    """The name an entity registry entry presents, user override winning."""
    return entry.get("name") or entry.get("original_name") or ""


def plural(count: int, singular: str, many: str = "") -> str:
    """Render a count with a correctly pluralized noun."""
    word = singular if count == 1 else (many or f"{singular}s")
    return f"{count} {word}"


def device_area_map(devices: list) -> dict:
    """Map each device id to the area it sits in."""
    return {device.get("id"): device.get("area_id") for device in devices}


def area_name_map(areas: list) -> dict:
    """Map each area id to its display name."""
    return {area.get("area_id"): area.get("name") or "" for area in areas}


def effective_area_id(entry: dict, device_areas: dict) -> str:
    """The area an entity actually belongs to.

    An entity with no area of its own inherits its device's. Every count and
    filter has to apply that fallback or it will disagree with what Home
    Assistant itself shows.
    """
    return entry.get("area_id") or device_areas.get(entry.get("device_id")) or ""


def parse_limit(raw, *, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(str(raw))
    except ValueError:
        raise UsageError(
            f"--limit needs a whole number, got {raw!r}",
            help_lines=["Run the command again with `--limit 50`"],
            code="BAD_LIMIT",
        ) from None
    if value < 1:
        raise UsageError(
            f"--limit must be at least 1, got {value}",
            help_lines=["Run the command again with `--limit 50`"],
            code="BAD_LIMIT",
        )
    return value


def select_fields(raw: str | None, available: list, default: list) -> list:
    """Resolve ``--fields`` against the fields a view can actually produce."""
    if not raw:
        return list(default)
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    if not wanted:
        return list(default)
    unknown = [name for name in wanted if name not in available]
    if unknown:
        raise UsageError(
            f"unknown field{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}",
            help_lines=[f"available fields: {', '.join(available)}"],
            code="UNKNOWN_FIELD",
        )
    return wanted


def project(rows: list, fields: list) -> list:
    """Reduce rows to the requested fields, preserving field order."""
    return [{name: row.get(name) for name in fields} for row in rows]


def parse_value(raw: str) -> Any:
    """Interpret a ``key=value`` value as JSON when it parses, else as a string."""
    text = raw.strip()
    if text == "":
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


def parse_pairs(pairs: list, *, flag: str) -> dict:
    """Turn repeated ``--flag key=value`` tokens into a dict."""
    out: dict = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise UsageError(
                f"{flag} needs key=value, got {pair!r}",
                help_lines=[f"Run the command again with `{flag} brightness=180`"],
                code="BAD_PAIR",
            )
        out[key.strip()] = parse_value(value)
    return out


def parse_json_flag(raw: str | None, *, flag: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"{flag} is not valid JSON: {exc.msg}",
            help_lines=[f"""Run the command again with `{flag} '{{"brightness": 180}}'`"""],
            code="BAD_JSON",
        ) from None
    if not isinstance(parsed, dict):
        raise UsageError(
            f"{flag} must be a JSON object",
            help_lines=[f"""Run the command again with `{flag} '{{"brightness": 180}}'`"""],
            code="BAD_JSON",
        )
    return parsed


def resolve_area(areas: list, needle: str) -> dict:
    """Find an area by ``area_id`` or by name, case-insensitively."""
    for area in areas:
        if area.get("area_id") == needle:
            return area
    lowered = needle.strip().lower()
    matches = [a for a in areas if (a.get("name") or "").strip().lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(a.get("area_id", "") for a in matches)
        raise UsageError(
            f"{needle!r} matches more than one area: {ids}",
            help_lines=["Pass the area_id instead of the name"],
            code="AMBIGUOUS_AREA",
        )
    raise NotFound(
        f"no area with id or name {needle!r}",
        help_lines=[
            "Run `ha-axi area list` to see the areas that exist",
            f'Run `ha-axi area create --name "{needle}"` to add it',
        ],
        code="NO_SUCH_AREA",
    )


def matches_search(needle: str, *values) -> bool:
    lowered = needle.lower()
    return any(lowered in str(value or "").lower() for value in values)
