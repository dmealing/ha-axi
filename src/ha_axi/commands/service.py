"""`ha-axi service` -- discover and call Home Assistant services."""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import NotFound, UsageError
from ..output import HelpBlock
from ._common import friendly_name, parse_json_flag, parse_pairs, plural

COMMAND = Command(
    name="service",
    summary="List Home Assistant services and call them",
    usage="usage: ha-axi service <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            summary="List service domains, or the services in one domain",
            flags=(Flag("--domain", "<name>", note="show the services in one domain"),),
        ),
        Sub(
            name="call",
            args=("<domain.service>",),
            summary="Call a service",
            flags=(
                Flag("--target-entity", "<entity_id>", repeat=True),
                Flag("--target-area", "<area_id>", repeat=True),
                Flag("--target-device", "<device_id>", repeat=True),
                Flag(
                    "--data", "<key=value>", repeat=True, note="value parsed as JSON when it parses"
                ),
                Flag("--data-json", "<object>", note="merged over --data"),
                Flag("--response", boolean=True, note="request the service response payload"),
            ),
        ),
    ),
    notes=("--data-json takes a whole JSON object; --data takes repeated key=value pairs",),
    examples=(
        "ha-axi service list",
        "ha-axi service list --domain light",
        "ha-axi service call light.turn_on --target-entity light.example_lamp",
        "ha-axi service call light.turn_on --target-area example_room --data brightness=180",
        "ha-axi service call climate.set_temperature --target-entity climate.example_thermostat --data-json '{\"temperature\": 21}'",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    return _call(ctx, parsed)


def _list(ctx, parsed):
    domains = ctx.rest().services()
    wanted = parsed.get("domain")

    if not wanted:
        rows = [
            {"domain": entry.get("domain", ""), "services": len(entry.get("services") or {})}
            for entry in domains
        ]
        rows.sort(key=lambda row: row["domain"])
        if not rows:
            return {"services": "0 service domains registered in this installation"}
        return {
            "count": plural(len(rows), "domain"),
            "domains": rows,
            "help": HelpBlock(
                [
                    "Run `ha-axi service list --domain <domain>` to see one domain's services",
                    "Run `ha-axi service call <domain>.<service> --target-entity <entity_id>` to call one",
                ]
            ),
        }

    match = next((e for e in domains if e.get("domain") == wanted), None)
    if match is None:
        known = ", ".join(sorted(e.get("domain", "") for e in domains)[:12])
        raise NotFound(
            f"no service domain named {wanted!r}",
            help_lines=[
                "Run `ha-axi service list` to see every domain",
                f"domains include: {known}",
            ],
            code="NO_SUCH_DOMAIN",
        )

    services = match.get("services") or {}
    rows = [
        {
            "service": f"{wanted}.{name}",
            "name": (spec or {}).get("name") or name,
            "fields": len((spec or {}).get("fields") or {}),
        }
        for name, spec in sorted(services.items())
    ]
    if not rows:
        return {"services": f"0 services registered in domain {wanted}"}
    return {
        "count": f"{plural(len(rows), 'service')} in {wanted}",
        "services": rows,
        "help": HelpBlock(
            [
                f"Run `ha-axi service call {rows[0]['service']} --target-entity <entity_id>` to call one"
            ]
        ),
    }


def _call(ctx, parsed):
    target = parsed.positionals[0]
    domain, sep, service = target.partition(".")
    if not sep or not domain or not service:
        raise UsageError(
            f"expected <domain>.<service>, got {target!r}",
            help_lines=[
                "Run `ha-axi service call light.turn_on --target-entity <entity_id>`",
                "Run `ha-axi service list` to see the available domains",
            ],
            code="BAD_SERVICE",
        )

    data = parse_pairs(parsed.get("data", []), flag="--data")
    data.update(parse_json_flag(parsed.get("data_json"), flag="--data-json"))

    # The REST endpoint passes this body straight through as the service data
    # and never unwraps a `target` key, while entity services validate
    # entity_id / device_id / area_id flat at the top level under
    # PREVENT_EXTRA. A nested target is rejected twice over, so emit them flat.
    # (The WebSocket `call_service` command does take a nested target; this
    # shape is specific to REST.)
    for flag, key in (
        ("target_entity", "entity_id"),
        ("target_area", "area_id"),
        ("target_device", "device_id"),
    ):
        selected = parsed.get(flag)
        if selected:
            data[key] = selected

    result = ctx.rest().call_service(
        domain, service, data, return_response=parsed.get("response", False)
    )

    changed, response = _split_result(result)
    doc = {"service": f"{domain}.{service}"}
    if changed:
        doc["changed"] = [
            {
                "entity_id": state.get("entity_id", ""),
                "name": friendly_name(state),
                "state": state.get("state", ""),
            }
            for state in changed
        ]
        doc["count"] = f"{plural(len(changed), 'state')} changed"
    else:
        doc["changed"] = f"{domain}.{service} accepted with 0 states changed"
    if response is not None:
        doc["response"] = response
    return doc


def _split_result(result):
    """Separate the changed-state list from an optional service response payload."""
    if isinstance(result, list):
        return result, None
    if isinstance(result, dict):
        changed = result.get("changed_states")
        response = result.get("service_response")
        return (changed if isinstance(changed, list) else []), response
    return [], None
