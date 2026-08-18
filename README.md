# ha-axi

An Agent eXperience Interface (AXI) CLI for Home Assistant.

`ha-axi` wraps the Home Assistant **REST** and **WebSocket** APIs in a command line built for
agents rather than people: token-efficient structured output, discoverable subcommands, useful
`--help`, no interactive prompts, and a non-zero exit on every failure.

The WebSocket half is the reason it exists. Home Assistant's **entity, area and device registries
are not reachable over REST** — an entity's user-set name and the area it belongs to live only
behind the WebSocket API. `ha-axi` puts both transports behind one consistent surface.

Nothing about any particular installation is baked in. The base URL and token come from the
environment, and the entity table is fetched at runtime.

---

## Install

```sh
pip install ha-axi
# or run it without installing
uvx ha-axi state list --domain light
```

From a checkout:

```sh
pip install -e ".[dev]"
```

## Configure

Two environment variables, and nothing else:

```sh
export HA_URL=https://homeassistant.example.com   # or HASS_SERVER
export HA_TOKEN=<long-lived access token>          # or HASS_TOKEN
```

Create the token in Home Assistant on your profile page, under **Security → Long-lived access
tokens**.

**There is deliberately no `--token` flag and no credential file.** A token on a command line
leaks into shell history and the process table; a token in a file leaks into commits. The
environment is the only channel. Anything token-shaped is redacted before it reaches stdout or
stderr, so a credential cannot escape through an error message or a debug line either.

Check both transports at once:

```sh
$ ha-axi doctor
healthy: true
checks[3]{check,status,detail}:
  environment,ok,HA_URL and HA_TOKEN are set
  rest,ok,API running. (version 2026.1.0)
  websocket,ok,"authenticated, 128 registry entries in 6 areas"
version: 2026.1.0
```

`doctor` exits non-zero when any leg fails, so it works as a gate in a script or a hook.

## Use

Running with no arguments shows live state, not a manual:

```sh
$ ha-axi
bin: ~/.local/bin/ha-axi
description: Agent ergonomic wrapper around the Home Assistant REST and WebSocket APIs. Prefer this over raw curl for Home Assistant operations.
url: https://homeassistant.example.com
entities: 128 in 14 domains
unavailable: 3
domains[8]{domain,entities}:
  sensor,42
  light,18
  ...
help[4]:
  Run `ha-axi state list --domain <domain>` to list entity states
  Run `ha-axi entity list --area <id|name>` to read the registry, which REST cannot reach
  ...
```

### Commands

| Command | Transport | What it does |
| --- | --- | --- |
| `ha-axi state list\|get` | REST | Entity states and attributes as they are right now |
| `ha-axi service list\|call` | REST | Discover services and call them |
| `ha-axi template render` | REST | Render a Jinja template server-side |
| `ha-axi entity list\|get\|update` | WebSocket | The entity registry: names, areas, platforms |
| `ha-axi area list\|get\|create\|update` | WebSocket | The area registry |
| `ha-axi device list` | WebSocket | The device registry |
| `ha-axi ws` | WebSocket | Any WebSocket command, declared or raw |
| `ha-axi api` | REST | Any authenticated REST path |
| `ha-axi doctor` | both | Environment and connection checks |
| `ha-axi setup` | — | Install the agent integrations |

`--help` on any command is the authoritative reference: it lists every flag per subcommand, with
defaults and two or three worked examples. Nothing here duplicates it.

```sh
ha-axi state list --domain light
ha-axi entity list --area 'Example Room' --fields entity_id,name,area,platform
ha-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room
ha-axi area create --name 'Example Study'
ha-axi service call light.turn_on --target-entity light.example_lamp --data brightness=180
ha-axi template render --template '{{ states("light.example_lamp") }}'
```

### `state` versus `entity`

They are different views of the same installation and both are needed:

- **`state`** is the runtime view over REST — what an entity is doing, and its attributes.
- **`entity`** is the registry view over WebSocket — an entity's stable identity: the name a user
  set, the area it belongs to, the integration that supplied it.

An entity with no area of its own **inherits its device's area**; `ha-axi entity get` reports which
of the two the area came from, and `entity list` resolves `area_id` to the area's name in the
default output so no second call is needed.

### Adding WebSocket commands

Every registry operation is one entry in the `REGISTRY` table in `src/ha_axi/ws.py`; the auth
handshake, id correlation and error translation are shared. `ha-axi ws --list` prints the table,
`ha-axi ws <name> --param k=v` sends a declared command, and `ha-axi ws --raw <api/type>` sends
anything Home Assistant supports that has no declared name yet.

## Output format

Structured [TOON](https://toonformat.dev/) on stdout by default, which is roughly 40% cheaper in
tokens than the equivalent JSON:

```
count: 2 of 128 total
states[2]{entity_id,name,state}:
  light.example_lamp,Example Lamp,on
  sensor.example_temperature,Example Temperature,"21.5"
```

- `--human` renders aligned tables for a person.
- `--json` emits raw JSON.
- **Errors go to stdout too**, in the same structured shape, and carry the command that fixes
  them. stderr carries only diagnostics (`--debug`), which agents do not read.
- Exit codes: `0` success — including idempotent no-ops — `1` error, `2` usage error.
- Unknown flags and extra arguments are **rejected by name** rather than ignored, with the
  subcommand's valid flags listed inline so the correction takes one turn, not two.

One documented deviation: `help[N]:` blocks render one suggestion per line rather than as a
delimiter-joined TOON array. Suggestions are command lines that routinely contain commas, and this
is the shape the AXI standard and the sibling AXI CLIs use. Every **data** structure is strict TOON.

## Agent integration

Two ways to make this discoverable. **You only need one.**

**Session hook** — ambient context in every session, with live state, for agents that support
hooks:

```sh
ha-axi setup hooks
```

Installs a `SessionStart` hook for Claude Code (`~/.claude/settings.json`) and Codex
(`~/.codex/hooks.json`, plus `[features] hooks = true`), and a managed ambient-context plugin for
OpenCode. It is idempotent, repairs the recorded path after a reinstall or a move, and refuses to
overwrite a plugin it does not manage.

**Agent Skill** — loads on demand, no per-session token cost, works in any agent that supports
skills:

```sh
npx skills add dmealing/ha-axi --skill ha-axi
```

`skills/ha-axi/SKILL.md` is generated from the CLI's own command table by `ha-axi setup skill`, and
CI runs `ha-axi setup skill --check` so it can never drift from the commands it documents.

## This repository is public, and stays generic

This tool talks to home automation installations, so the failure that matters is not a bug — it is
a commit that describes, or grants access to, someone's house. That is enforced by a scanner, not
by a convention:

```sh
scripts/leakcheck.py            # scan every tracked file
scripts/leakcheck.py --staged   # scan what a commit would actually record
scripts/leakcheck.py --demo     # self-test: prove every rule still fires
```

It rejects five shapes: **JWTs** (what a Home Assistant token looks like), **RFC1918 addresses**,
**absolute home directories**, **emails outside the reserved documentation domains**, and literal
**bearer credentials**. Loopback, public addresses and `example.com` are all fine. A line that
must legitimately keep one of these shapes can carry the marker `leakcheck: allow`.

It runs in two places:

```sh
scripts/install-hooks.sh   # points core.hooksPath at .githooks
```

- **`.githooks/pre-commit`** blocks the commit locally, including the very first commit in a
  repository, which has no `HEAD` to diff against.
- **CI** runs `--demo` first — proving the scanner still detects what it claims — and then scans
  the whole tree. Bypassing the local hook only delays the failure.

Fixtures, tests, docs and examples use invented identifiers throughout:
`light.example_lamp`, area `Example Room`, `https://homeassistant.example.com`.

## Testing

```sh
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

**No live installation and no live token are needed, which is the point.** The suite runs against
real local servers on loopback: an `http.server` for REST, and a real `websockets` server that
performs the same `auth_required` / `auth` / `auth_ok` handshake Home Assistant does.

Covered by tests:

- the TOON encoder against the specification's rules — tabular, keyed tabular, list and inline
  forms, quoting, escaping, delimiters, root forms;
- every command's output shape, filters, field selection, limits and empty states;
- the full WebSocket protocol: handshake, auth rejection, id correlation, registry reads and
  updates, idempotent no-ops, and error translation;
- flag validation, renamed-flag hints, exit codes, and `--help` for every command without any
  configuration present;
- redaction, including that a rejected token never appears in its own error message;
- the leak scanner in both directions, and the pre-commit hook end to end via a real `git commit`;
- hook installation: idempotency, path repair, and leaving other tools' hooks alone.

**Would need a live installation to confirm:** that the real Home Assistant server accepts the
exact request bodies built here — service-call `target` shapes, `config/entity_registry/update`
field names, `return_response` behaviour — and how a very large registry behaves in practice. The
doubles implement the documented protocol, so they verify this client against the specification,
not against a particular server build.

## License

MIT
