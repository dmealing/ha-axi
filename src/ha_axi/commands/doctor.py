"""`ha-axi doctor` -- prove the environment and both transports work."""

from __future__ import annotations

from ..argspec import Command, Sub
from ..config import describe_environment, missing_env_vars, setup_help
from ..errors import AxiError, fault_class
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
        checks.append(_failed("environment", f"{' and '.join(missing)} not set", "NOT_CONFIGURED"))
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
        checks.append(_failed("rest", exc.message, exc.code))

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
        checks.append(_failed("websocket", exc.message, exc.code))

    return _document(checks, healthy=healthy, version=version)


def _failed(check: str, message: str, code: str | None) -> dict:
    """One failed check, carrying the same code and class the command would.

    `doctor` already told the two transports apart; what it could not tell
    apart was *why* either had failed, because a check row carried prose and
    nothing else. The row now says which fault it was in the same vocabulary
    every other command answers in -- and because the two transports run
    independently here, a row each is how "the token is wrong" and "the proxy
    does not forward upgrades" become two readable facts instead of one
    unhealthy instance.

    The environment check goes through here too: a machine with nothing
    configured is a fault with a code like any other, and the one `home`
    already answers the same condition with. The class is derived from the
    code rather than written beside it, so `errors.CODES` stays the only
    place the pair is declared.
    """
    row = {"check": check, "status": "fail"}
    if code:
        row["code"] = code
        row["class"] = fault_class(code)
    row["detail"] = message
    return row


def _document(checks, *, healthy: bool, version: str = ""):
    doc = {"healthy": healthy, "checks": checks}
    if version:
        doc["version"] = version
    if not healthy:
        doc["help"] = HelpBlock(setup_help())
        doc["__exit_code__"] = 1
    return doc
