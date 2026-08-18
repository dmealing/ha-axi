"""`ha-axi setup` -- install the session integrations and the agent skill."""

from __future__ import annotations

from pathlib import Path

from .. import hooks, skill
from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..output import HelpBlock

COMMAND = Command(
    name="setup",
    summary="Install or repair the agent integrations for ha-axi",
    usage="usage: ha-axi setup <subcommand> [flags]",
    subs=(
        Sub(
            name="hooks",
            summary="Install SessionStart hooks for Claude Code, Codex and OpenCode",
            flags=(Flag("--home", "<path>", note="install under a different home directory"),),
        ),
        Sub(
            name="skill",
            summary="Write or verify the installable Agent Skill",
            flags=(
                Flag("--path", "<dir>", default=".", note="repository root"),
                Flag(
                    "--check", boolean=True, note="exit non-zero when the committed copy is stale"
                ),
            ),
        ),
    ),
    notes=(
        "hooks give ambient context every session; the skill loads on demand instead -- install either",
        "hook installation is idempotent and repairs the path after a reinstall or a move",
    ),
    examples=(
        "ha-axi setup hooks",
        "ha-axi setup skill",
        "ha-axi setup skill --check",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "hooks":
        return _hooks(parsed)
    return _skill(ctx, parsed)


def _hooks(parsed):
    home = parsed.get("home")
    report = hooks.install(Path(home) if home else None)
    doc = {
        "hooks": {"command": report["command"]},
        "targets": report["targets"],
    }
    if report["errors"]:
        doc["errors"] = report["errors"]
        doc["__exit_code__"] = 1
    else:
        doc["help"] = HelpBlock(
            ["Restart your agent session to receive ha-axi ambient context at session start"]
        )
    return doc


def _skill(ctx, parsed):
    from ..cli import COMMAND_ORDER, command_specs

    root = Path(parsed.get("path") or ".")
    path = skill.target_path(root)
    content = skill.render([command_specs()[name] for name in COMMAND_ORDER])

    if parsed.get("check"):
        if not path.exists():
            return {
                "skill": str(path),
                "status": "missing",
                "help": HelpBlock([f"Run `ha-axi setup skill --path {root}` to write it"]),
                "__exit_code__": 1,
            }
        if path.read_text(encoding="utf-8") != content:
            return {
                "skill": str(path),
                "status": "stale",
                "help": HelpBlock([f"Run `ha-axi setup skill --path {root}` to regenerate it"]),
                "__exit_code__": 1,
            }
        return {"skill": str(path), "status": "current"}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = not path.exists() or path.read_text(encoding="utf-8") != content
        if changed:
            path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise UsageError(
            f"could not write {path}: {exc.strerror or exc}",
            help_lines=["Pass a writable repository root with `--path <dir>`"],
            code="UNWRITABLE",
        ) from None

    return {
        "skill": str(path),
        "status": "written" if changed else "current",
        "help": HelpBlock(
            [
                f"Install it in an agent with `npx skills add dmealing/ha-axi --skill {skill.SKILL_NAME}`"
            ]
        ),
    }
