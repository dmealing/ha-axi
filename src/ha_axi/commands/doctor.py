"""`ha-axi doctor` -- prove the environment and both transports work."""

from __future__ import annotations

from ..argspec import Command, Sub
from ..config import TOKEN_VARS, URL_VARS, describe_environment
from ..errors import AxiError
from ..output import HelpBlock
from ._common import plural

COMMAND = Command(
    name="doctor",
    summary="Check the environment, the REST API and the WebSocket API",
    usage="usage: ha-axi doctor",
    default_sub="doctor",
    subs=(Sub(name="doctor", summary="Run every connection check"),),
    notes=("exits non-zero when any check fails, so it works as a CI or hook gate",),
    examples=("ha-axi doctor",),
)


def run(ctx, sub: str, parsed):
    env = describe_environment(ctx.environ)
    checks = []
    healthy = True

    missing = []
    if not env["url_set"]:
        missing.append(URL_VARS[0])
    if not env["token_set"]:
        missing.append(TOKEN_VARS[0])
    if missing:
        checks.append(
            {
                "check": "environment",
                "status": "fail",
                "detail": f"{' and '.join(missing)} not set",
            }
        )
        return _document(checks, healthy=False)

    checks.append(
        {
            "check": "environment",
            "status": "ok",
            "detail": f"{env['url_var']} and {env['token_var']} are set",
        }
    )

    version = ""
    try:
        health = ctx.rest().health()
        detail = health.get("message") if isinstance(health, dict) else str(health)
        info = ctx.rest().config_info()
        if isinstance(info, dict):
            version = info.get("version", "")
        checks.append(
            {
                "check": "rest",
                "status": "ok",
                "detail": f"{detail or 'reachable'}{f' (version {version})' if version else ''}",
            }
        )
    except AxiError as exc:
        healthy = False
        checks.append({"check": "rest", "status": "fail", "detail": exc.message})

    try:
        with ctx.ws() as client:
            entities = client.run("entity.list") or []
            areas = client.run("area.list") or []
        checks.append(
            {
                "check": "websocket",
                "status": "ok",
                "detail": (
                    "authenticated, "
                    f"{plural(len(entities), 'registry entry', 'registry entries')} "
                    f"in {plural(len(areas), 'area')}"
                ),
            }
        )
    except AxiError as exc:
        healthy = False
        checks.append({"check": "websocket", "status": "fail", "detail": exc.message})

    return _document(checks, healthy=healthy, version=version)


def _document(checks, *, healthy: bool, version: str = ""):
    doc = {"healthy": healthy, "checks": checks}
    if version:
        doc["version"] = version
    if not healthy:
        doc["help"] = HelpBlock(
            [
                f"Set {URL_VARS[0]} to your Home Assistant base URL, e.g. https://homeassistant.example.com",
                f"Set {TOKEN_VARS[0]} to a long-lived access token from your profile page, under Security",
                "Run `ha-axi doctor` again once both are set",
            ]
        )
        doc["__exit_code__"] = 1
    return doc
