"""`ha-axi doctor` -- prove the environment and both transports work."""

from __future__ import annotations

from ..argspec import Command, Sub
from ..config import describe_environment, missing_env_vars, setup_help
from ..errors import AxiError
from ..output import HelpBlock
from ..readonly import READ
from ._common import plural

COMMAND = Command(
    name="doctor",
    summary="Check the environment, the REST API and the WebSocket API",
    usage="usage: ha-axi doctor",
    default_sub="doctor",
    subs=(Sub(name="doctor", summary="Run every connection check", access=READ),),
    notes=("exits non-zero when any check fails, so it works as a CI or hook gate",),
    examples=("ha-axi doctor",),
)


def run(ctx, sub: str, parsed):
    env = describe_environment(ctx.environ)
    healthy = True

    # First, because it is the one check that needs no configuration and no
    # connection -- and because a session that cannot write is the first thing
    # to know when a write has just been refused.
    checks = [
        {
            "check": "read_only",
            "status": "ok",
            "detail": (
                f"{env['read_only_var']} is set: every write is refused"
                if env["read_only"]
                else f"{env['read_only_var']} is not set: writes are allowed"
            ),
        }
    ]

    missing = missing_env_vars(ctx.environ)
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
        doc["help"] = HelpBlock(setup_help())
        doc["__exit_code__"] = 1
    return doc
