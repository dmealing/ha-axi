# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test,
release, architecture, and sharp-edge notes that should travel with the code.

## The hard constraint: this repository is public and must stay generic

`ha-axi` talks to home automation installations. The failure that matters is not a bug — it is a
commit that describes, or grants access to, someone's house. Before writing **anything** into this
repo, including tests, fixtures, docs, examples and commit messages:

- **No host addresses.** No RFC1918 addresses, no install-specific hostnames or ports. The base URL
  comes from `HA_URL`.
- **No credentials.** Home Assistant long-lived tokens are JWTs (`eyJ...`). One must never appear
  in a commit, test, fixture, doc, example or log line.
- **No real entity data.** No real `entity_id`s, friendly names, area names, device names or person
  names. Invent obviously-synthetic ones: `light.example_lamp`, area `Example Room`,
  `https://homeassistant.example.com`.
- **No local paths or personal identifiers.**

`scripts/leakcheck.py` enforces this — do not rely on remembering it:

```sh
scripts/leakcheck.py                     # every tracked file
scripts/leakcheck.py --staged            # what a commit would record (pre-commit hook)
scripts/leakcheck.py --commit-msg PATH   # the message itself (commit-msg hook)
scripts/leakcheck.py --rules             # the live rule list
scripts/leakcheck.py --demo              # self-test: proves every rule still fires
scripts/install-hooks.sh                 # sets core.hooksPath to .githooks
```

CI runs `--demo` before the real scan, so a scanner that stopped detecting anything fails the build
rather than passing silently. If the scanner flags a line that legitimately needs the shape, add
`leakcheck: allow=<rule>` on that line — scoped to that one rule, never blanket. Do not weaken a
rule to make a commit pass, and do not bypass the hooks.

**Writing tests for the guard:** build credential shapes at run time — `leakcheck.synthetic_jwt()`
base64-encodes a payload rather than embedding an `eyJ...` literal — because the condensed pass
joins the whole file before re-scanning and will (correctly) find a literal split across lines.
Address shapes may be written as fragments, since the condensed pass deliberately runs only the
token rules.

**Coverage is bounded, and the README says so.** Do not restore any claim that the guard makes
review unnecessary: it narrows how a leak can happen, and misses generic public hostnames, secrets
that are neither JWT-shaped nor bearer-prefixed, and anything inside a binary.

## Architecture

- `toon.py` — a strict TOON encoder (spec v4.1). Encoding happens **only** at the output boundary;
  command modules return plain JSON-shaped dicts. Covered by conformance tests in
  `tests/test_toon.py`; do not loosen it to make output prettier.
- `output.py` — the single place anything reaches stdout, and therefore the only place redaction
  has to hold. `HelpBlock` is the one deliberate departure from strict TOON: `help[N]:` blocks
  render one suggestion per line, matching the AXI standard and the sibling AXI CLIs, because the
  suggestions are command lines full of commas. Data structures stay strict TOON.
- `rest.py` — REST over the standard library. `ws.py` — WebSocket over `websockets`' sync client,
  the only runtime dependency.
- `argspec.py` — per-subcommand flag declarations. Unknown flags are rejected by name with the
  valid ones inlined; `RENAMED` maps plausible wrong guesses to the real flag.
- `commands/` — one module per noun, each exposing `COMMAND` and `run(ctx, sub, parsed)`.

### Security invariants — do not regress these

- **Every output path is redacted, stdout and stderr alike.** `output.write`, `output.write_text`,
  `output.debug` and `output.debug_exception` all pass through `redact()`. stderr is not a safe
  channel just because agents ignore it: it reaches terminals, logs and CI output.
- **`cli.main` has a last-resort `except Exception`** that renders a structured, redacted error on
  **stdout**. Without it an unexpected exception prints a raw traceback on stderr, bypassing
  redaction entirely and leaving stdout empty. Both halves are the documented contract.
- **The token is registered as a secret in `config.load`**, at the moment it is read, so no later
  code path can print it. It is also rejected there if it contains whitespace or a control
  character, because `http.client` raises a `ValueError` embedding the whole `Bearer ...` header.
- **Redirects never carry the token off-origin.** `rest._SameOriginRedirectHandler` refuses any
  redirect that changes scheme or netloc; urllib would otherwise copy `Authorization` onto it.
- **URL userinfo is stripped in `normalize_base_url` and registered as a secret.** The no-argument
  home view prints the base URL, and `setup hooks` runs that view into every agent session.
- **A bare host defaults to `https://`**, never `http://`.
- Tests for all of this live in `tests/test_credentials.py`, which asserts `capsys` **stderr** is
  clean — the assertion that was missing when two escapes shipped.

### Sharp edges

- **Null is meaningful over WebSocket.** `config/entity_registry/update` with `name: null` is how a
  user override is cleared, so `WsClient.send_command` must not filter `None` out of a payload.
  It did once; `--clear-name` silently did nothing. There is a test for this.
- **`entity_id` is not stable identity.** Filter by area or search; do not infer meaning from an id.
- **An entity with no `area_id` inherits its device's area.** Any per-area count or filter that
  ignores the device fallback will be wrong.
- **`state` (REST) and `entity` (WebSocket) are different views.** Names and areas exist only in the
  registry; states exist only over REST.
- **Adding a WebSocket command** is one entry in `REGISTRY` in `src/ha_axi/ws.py`. It becomes
  reachable through `ha-axi ws <name>` immediately; a typed subcommand is optional on top.
- **`--json` is the global output mode.** Command flags carrying JSON payloads are named
  `--data-json` and `--params-json` so no precedence rule is needed. The output mode is decided by
  a pre-scan of the whole argv before parsing, so a usage error still honours it.
- **REST service targets go flat.** `entity_id` / `area_id` / `device_id` sit at the top level of
  the body; the REST endpoint hands the body to the service as its data and never unwraps a
  `target` key, so a nested one is rejected as an extra key. (The WebSocket `call_service` command
  *does* take a nested target — the shapes genuinely differ.) The REST test double enforces this,
  so a client that only agrees with itself cannot pass.
- **Exit codes follow one rule.** A static invocation problem — unknown flag, unknown subcommand,
  unknown WebSocket command name — exits 2. An outcome of a lookup against live state — no such
  area, an ambiguous area name — exits 1. Put a new error on the right side of that line.
- **`--help` obeys value consumption.** `_help_requested` skips the value of any declared
  value-taking flag, so `template render --template --help` renders the literal.

## Build, test, lint

```sh
pip install -e ".[dev]"
pytest                                   # ~240 tests, about 1.5s
ruff check . && ruff format --check .
ha-axi setup skill --check               # SKILL.md is generated, never hand-edited
```

**Tests never need a live installation or a live token, and must not start to.** They run against
real loopback servers in `tests/conftest.py`: an `http.server` for REST and a real `websockets`
server that performs the Home Assistant `auth_required` / `auth` / `auth_ok` handshake. If a
behaviour cannot be tested that way, say so in the PR rather than reaching for real credentials.

Two testing gotchas already paid for:

- `websockets`' sync `Server.serve_forever()` takes **no** arguments. Only the stdlib HTTP server
  accepts `poll_interval`, which the REST double uses to keep teardown off the critical path.
- The two doubles listen on different ports, so a test needing both transports healthy has to bind
  each one explicitly rather than sharing a single `HA_URL`.

`skills/ha-axi/SKILL.md` is generated from the CLI's command table. Change the commands, then run
`ha-axi setup skill` and commit the result; CI fails if the two disagree.

Supported Pythons are 3.9 through 3.12. `from __future__ import annotations` is what makes the
`X | None` annotation syntax safe on 3.9 — keep it at the top of every module.
