"""The output boundary: redaction, render modes, and the AXI document shapes.

Everything the CLI prints passes through :func:`write`, which is the single
place a credential could escape and therefore the single place redaction has to
hold. Command modules build ordinary dicts; conversion to TOON happens here.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from .toon import encode

#: Placeholder substituted for anything that looks like a credential.
REDACTED = "<redacted>"

MODE_TOON = "toon"
MODE_HUMAN = "human"
MODE_JSON = "json"

# A Home Assistant long-lived access token is a JWT: three base64url segments,
# the first of which starts `eyJ` because it encodes a JSON object.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")

_secrets: set = set()


def register_secret(value: str | None) -> None:
    """Register a literal string that must never reach stdout or stderr."""
    if value and len(value) >= 8:
        _secrets.add(value)


def reset_secrets() -> None:
    """Drop registered secrets. Used by tests to isolate cases."""
    _secrets.clear()


def redact(text: str) -> str:
    """Remove credentials from ``text`` by literal match and by shape."""
    for secret in _secrets:
        text = text.replace(secret, REDACTED)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    return _JWT.sub(REDACTED, text)


class HelpBlock:
    """A ``help[N]:`` block of contextual next steps.

    The AXI standard renders these one suggestion per line under the header
    rather than as a delimiter-joined TOON primitive array, because the
    suggestions are command lines that routinely contain commas. Data payloads
    are strict TOON; this block follows the AXI standard's own shape so the
    output matches the sibling AXI CLIs. See README, "Output format".
    """

    __slots__ = ("lines",)

    def __init__(self, lines) -> None:
        self.lines = [line for line in lines if line]

    def __bool__(self) -> bool:
        return bool(self.lines)

    def __iter__(self):
        return iter(self.lines)


def truncate(text: str, limit: int, hint: str) -> tuple:
    """Shorten ``text`` to ``limit`` characters, reporting what was withheld.

    Returns the possibly-shortened text and a help line, empty when the whole
    value fits. Large fields are previewed rather than dropped so the agent can
    tell whether fetching the rest is worth a second call.
    """
    if len(text) <= limit:
        return text, ""
    return f"{text[:limit]}... (truncated, {len(text)} chars total)", hint


def _flatten(doc) -> Any:
    """Replace HelpBlock values with plain lists for JSON rendering."""
    if isinstance(doc, HelpBlock):
        return list(doc.lines)
    if isinstance(doc, dict):
        return {k: _flatten(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_flatten(v) for v in doc]
    return doc


def render(doc, mode: str = MODE_TOON) -> str:
    if mode == MODE_JSON:
        return json.dumps(_flatten(doc), indent=2, ensure_ascii=False)
    if mode == MODE_HUMAN:
        return _render_human(doc)
    return _render_toon(doc)


def _render_toon(doc) -> str:
    if not isinstance(doc, dict):
        return encode(doc)
    chunks = []
    plain: dict = {}

    def flush():
        if plain:
            chunks.append(encode(plain))
            plain.clear()

    for key, value in doc.items():
        if isinstance(value, HelpBlock):
            flush()
            if value:
                chunks.append(
                    "\n".join([f"{key}[{len(value.lines)}]:", *(f"  {ln}" for ln in value.lines)])
                )
        else:
            plain[key] = value
    flush()
    return "\n".join(c for c in chunks if c)


def _render_human(doc, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(doc, HelpBlock):
        return "\n".join(f"{pad}* {line}" for line in doc.lines)
    if isinstance(doc, dict):
        out = []
        for key, value in doc.items():
            if isinstance(value, HelpBlock):
                if value:
                    out.append(f"{pad}{key}:")
                    out.append(_render_human(value, depth + 1))
            elif isinstance(value, (dict, list)) and value:
                out.append(f"{pad}{key}:")
                out.append(_render_human(value, depth + 1))
            elif isinstance(value, (dict, list)):
                out.append(f"{pad}{key}: (none)")
            else:
                out.append(f"{pad}{key}: {_scalar(value)}")
        return "\n".join(out)
    if isinstance(doc, list):
        if doc and all(isinstance(item, dict) for item in doc):
            return _render_table(doc, pad)
        return "\n".join(f"{pad}- {_render_human(item, 0).strip()}" for item in doc)
    return f"{pad}{_scalar(doc)}"


def _render_table(rows, pad: str) -> str:
    columns: list = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    cells = [[_scalar(row.get(col, "")) for col in columns] for row in rows]
    widths = [
        max(len(col), *(len(row[i]) for row in cells)) if cells else len(col)
        for i, col in enumerate(columns)
    ]
    out = [pad + "  ".join(col.ljust(widths[i]) for i, col in enumerate(columns)).rstrip()]
    out.append(pad + "  ".join("-" * w for w in widths).rstrip())
    for row in cells:
        out.append(pad + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


def _scalar(value) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write(doc, mode: str = MODE_TOON, stream=None) -> None:
    """Render, redact and print a document on stdout."""
    stream = sys.stdout if stream is None else stream
    text = redact(render(doc, mode))
    if text:
        stream.write(text + "\n")
    stream.flush()


def write_text(text: str, stream=None) -> None:
    """Print already-rendered text (help and usage) through the same redactor."""
    stream = sys.stdout if stream is None else stream
    stream.write(redact(text) + "\n")
    stream.flush()


def debug(message: str) -> None:
    """Emit a diagnostic on stderr, which agents do not read."""
    if os.environ.get("HA_AXI_DEBUG"):
        sys.stderr.write(redact(f"ha-axi: {message}") + "\n")
        sys.stderr.flush()
