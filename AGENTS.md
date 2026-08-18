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
scripts/leakcheck.py            # every tracked file
scripts/leakcheck.py --staged   # what a commit would actually record (used by the hook)
scripts/leakcheck.py --demo     # self-test: proves every rule still fires
scripts/install-hooks.sh        # sets core.hooksPath to .githooks
```

CI runs `--demo` before the real scan, so a scanner that stopped detecting anything fails the build
rather than passing silently. If the scanner flags a line that legitimately needs the shape, add
the marker `leakcheck: allow` on that line — do not weaken a rule to make a commit pass, and do not
bypass the hook.

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
  `--data-json` and `--params-json` so no precedence rule is needed.

## Build, test, lint

```sh
pip install -e ".[dev]"
pytest                                   # ~230 tests, about 1.5s
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
