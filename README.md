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
scripts/install-hooks.sh   # point core.hooksPath at .githooks
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
unknown: 9
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
| `ha-axi state list\|get` | REST | Entity states and attributes as they are right now (`--area` also reads the registry) |
| `ha-axi service list\|get\|call` | REST | Discover services, read one's fields, and call them |
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
ha-axi service get climate.set_temperature
ha-axi template render --template '{{ states("light.example_lamp") }}'
```

### Calling a service, and being told why not

Home Assistant publishes its whole service model at `GET /api/services`: every service, its fields,
which are required, whether it answers with a response payload, and the capability a target entity
must have. `ha-axi` does **not** turn that into commands — that would be 327 subcommands wrapping
one that already reaches all of them, each firing at a device without asking whether it can do the
thing. It reads the model in three narrow places instead:

- **On a refused call.** A refusal from Home Assistant is an empty `400`: the status and no body, so
  an unknown service, an undeclared field and a missing required one look identical on the wire.
  `ha-axi` fetches the model at that point and answers with the real service names, the field it
  does not accept, the field it needs, or the `--response` flag it was missing. A call that
  succeeds pays for none of this.
- **On `ha-axi service get <domain.service>`.** One service's field table, its target and its
  response mode, rendered from the installation itself — so it is never stale and never describes
  integrations you do not have.
- **Before dispatch, for `--target-area` and `--target-device` only.** Home Assistant refuses an
  entity you name outright that lacks the capability a service needs, but an entity it reached
  through an area or a device is dropped in silence. Where the requirement is published and no
  alternative is published with it, `ha-axi` says so before sending. `--no-check` skips it.

The published requirement is a list of alternatives, and it is read as one: `media_player.volume_up`
names both VOLUME_SET and VOLUME_STEP because Home Assistant backs a player that cannot step with
one that can set. A speaker with only the first is not gated, because it works.

An empty result is also read carefully. Home Assistant returns the states that actually changed, so
`[]` means both "everything was already as asked" and "nothing was reached at all", and it never
says which. When the change set is empty and a target was given, `ha-axi` resolves that target:
reaching nothing exits 1, and reaching something exits 0 with the count and any entity that was
`unavailable` and therefore skipped.

### `state` versus `entity`

They are different views of the same installation and both are needed:

- **`state`** is the runtime view over REST — what an entity is doing, and its attributes.
- **`entity`** is the registry view over WebSocket — an entity's stable identity: the name a user
  set, the area it belongs to, the integration that supplied it.

An entity with no area of its own **inherits its device's area**; `ha-axi entity get` and
`entity update` both report which of the two the area came from, and `entity list` resolves
`area_id` to the area's name in the default output so no second call is needed.

An entity's **name is composed from two registries the same way Home Assistant composes it**: a name
somebody set wins outright, and otherwise the device's name and the entity's own half are joined —
so most core entities are named entirely or partly by their device, and `--search` matches the name
that composition produces. `original_name` remains available as a field for the entity's half alone,
and `entity list --device <device_id>` lists what one device supplies.

`--area` works the same on `state list`, `entity list` and `device list`. On `state list` it costs
one extra registry round-trip, because areas exist only over WebSocket — paid only when the flag is
passed:

```sh
ha-axi state list --area 'Example Room' --domain light
```

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
is the shape the AXI standard and the sibling AXI CLIs use. Every **data** structure is strict TOON,
and "strict" is a test result rather than a claim: the specification's own conformance fixtures are
vendored into the suite and every one of them has to pass.

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
scripts/leakcheck.py --commit-msg <path>   # scan a commit message
scripts/leakcheck.py --pull-request <n>    # scan a pull request's title and body
scripts/leakcheck.py --rules    # list the rules and what each one catches
scripts/leakcheck.py --demo     # self-test: prove every rule still fires
```

**What it catches.** Run `--rules` for the live list; today it is JWTs (what a Home Assistant token
looks like), RFC1918 and CGNAT addresses, IPv4/IPv6 link-local and unique-local addresses, `.local`
/`.lan`/`.localdomain` hostnames, `*.ui.nabu.casa` remote-access hostnames, geographic coordinates
(`/api/config` returns the house's), MAC and Zigbee IEEE addresses, absolute home directories,
emails outside the reserved documentation domains, and literal bearer credentials.

Each file is scanned twice: once **per line**, and once **condensed**, with whitespace, quotes,
backslashes and `+` removed from the whole file. A credential split across lines or assembled by
concatenation is invisible to a line pass, and splitting a token across fragments is exactly how one
hides — deliberately or not. The condensed pass runs only the token rules, because joining arbitrary
lines can fuse unrelated digits into a plausible address, and a guard that cries wolf gets bypassed.

**What it does not catch,** stated plainly so the coverage is not mistaken for more than it is: a
generic public hostname or IP that happens to be someone's instance, a secret that is neither
JWT-shaped nor bearer-prefixed, anything inside a binary or an image, and any shape no rule
describes. The scanner narrows the ways a leak can happen; it does not make review unnecessary.

A line that must legitimately keep one of these shapes carries `leakcheck: allow=<rule>`. The
exemption is **per rule on purpose** — a blanket marker would switch off every rule on that line,
including one nobody was thinking about, which is how a live credential hides behind a suppressed
lint.

It runs in four places:

```sh
scripts/install-hooks.sh   # points core.hooksPath at .githooks
```

- **`.githooks/pre-commit`** blocks the commit locally, including the very first commit in a
  repository, which has no `HEAD` to diff against.
- **`.githooks/commit-msg`** scans the message, which is a separate channel from file content and
  just as public.
- **CI** runs `--demo` first — proving the scanner still detects what it claims — and then scans
  the whole tree. The demo's own output is published too, so it reports findings without the
  values. Bypassing the local hooks only delays the failure.
- **The pull request itself**, on every open, push *and edit*. A title and a body are published the
  moment they are written, are in no checkout and pass under no hook, so nothing above reaches them
  — and tooling routinely pastes captured output into a body, where a `pytest` header carries a
  `rootdir:` line holding an absolute path. It fails the check when it cannot read the pull request
  rather than reporting a clean it cannot support, and it reports the field, line and rule of a
  match — plus the offset when the finding's pass read the text as written — without printing the
  match: a CI log is more public than the page it came from. For the same
  reason a pull request cannot carry an `allow=` marker — in a file that marker is committed and
  reviewed, and in a body it is an off-switch anyone can add after every check has run.

A commit message is checked as well as scanned. release-please builds the changelog and the version
bump from commit messages, and when its parser cannot read one it says so at debug level, drops the
commit and **exits 0** — a merged fix that is never published, with a green release run over it. So
`.githooks/commit-msg` also runs `scripts/commitcheck.py`, and the release workflow re-checks every
commit since the last tag. Rich commit bodies are the point of this history and nothing here
restricts them; the one shape to know is that a body line must not *begin* with a word run straight
into an unclosed or nested parenthesis — `` `Decimal(repr(v))` `` at a line start is refused, and the
same phrase one word further along the line is fine. This repository has never lost a release to it,
and `tests/fixtures/commit-messages/` records how narrowly. Run `scripts/commitcheck.py --rules` for
the grammar rule and its citation.

Fixtures, tests, docs and examples use invented identifiers throughout:
`light.example_lamp`, area `Example Room`, `https://homeassistant.example.com`. Test fixtures build
credential shapes at run time rather than embedding literals, so the suite that proves the scanner
works does not itself trip it.

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
  forms, quoting, escaping, delimiters, root forms — **and against the specification's own encode
  fixtures**, every one of them, vendored byte-for-byte from
  [`toon-format/spec`](https://github.com/toon-format/spec) and run on every `pytest`. The case
  count is asserted too, so a fixture that stops being collected fails the suite instead of
  quietly lowering the score;
- every command's output shape, filters, field selection, limits and empty states;
- the WebSocket protocol beyond the happy path: the handshake, an unexpected greeting, a message
  arriving mid-authentication, auth rejection, event and pong frames interleaved with results, id
  correlation, a non-JSON frame, a socket closed mid-command, both directions of the error
  boundary, implicit connect, and a registry larger than the library's default frame size;
- **credential containment**: a cross-origin redirect refused rather than followed, a token that
  cannot be a header rejected before it reaches one, URL userinfo stripped and redacted, an
  unexpected exception rendered as a structured error on stdout instead of a traceback, and
  **stderr asserted clean and redacted** — the gap that let two escapes ship;
- flag validation, renamed-flag hints, exit codes, `--help` in every position (and never stolen
  from a flag value), and `--help` for every command without any configuration present;
- the leak scanner adversarially: every rule against the shape it claims, every rule against
  content that must not trip it, the split/concatenated/percent-encoded evasions, the scoped allow
  marker, and both git hooks end to end through a real `git commit`;
- hook installation: idempotency, path repair, atomic writes, and leaving other tools' hooks alone.

**Would need a live installation to confirm:** that a real Home Assistant accepts the exact request
bodies built here — `config/entity_registry/update` field names, `return_response` behaviour, and
the service-data keys individual integrations validate — how a very large registry behaves in
practice, and whether an integration's published `supported_features` requirement agrees with the
one its Python actually enforces. The doubles implement the documented protocol and enforce the
parts of it that are known: the REST double rejects a nested service-call `target` the way Home
Assistant does, refuses an unknown service with the same empty `400` and no body, and drops an
`unavailable` or incapable entity in the same silence. So they verify this client against the
specification rather than against a particular server build.

What the doubles do *not* get to invent is the shape of ordinary data. Their fixtures carry the
distribution a real installation has — entries named entirely by their device, entries that name
only their own half, a `has_entity_name` of each setting, a disabled entry with no state at all, an
`unknown` state as well as an `unavailable` one, services and fields that publish no prose — and
`tests/test_double_fidelity.py` asserts each of those shapes is still present. Every one of them was
absent once, and each absence cost a defect that a green suite could not see.

## Continuous integration

| Workflow | Runner | Triggers | What runs |
| --- | --- | --- | --- |
| `ci.yml` | self-hosted | push to `main`, nightly, manual | leak scan, lint, `pytest` on 3.9–3.12, generated-skill check |
| `hygiene.yml` | `ubuntu-latest` | `pull_request` | the leak scan |
| `release.yml` | `ubuntu-latest` | push to `main`, manual | release-please, and the OIDC publish when a release PR merges |

A pull request therefore shows **one** hosted check, and that is deliberate. The leak scan is the
gate that has to run before a human reads a diff; everything heavier runs on the maintainer's own
machine, where the full matrix is free and does not queue behind anyone. `ci.yml` never
triggers on `pull_request` and must not start to — this repository is public and that runner is a
personal workstation, so a pull-request trigger would give any contributor code execution on it.

## Releasing

Version bumps and the changelog are driven from conventional commits by
[release-please](https://github.com/googleapis/release-please), which opens a release PR on every
push to `main`. Merging that PR builds the distribution, smoke-tests the built wheel, and publishes
to PyPI through [trusted publishing](https://docs.pypi.org/trusted-publishers/) — an OIDC exchange,
so **no long-lived PyPI token exists in this repository or anywhere else**.

Trusted publishing requires a one-time configuration on PyPI by the repository owner (project
`ha-axi`, owner `dmealing`, workflow `release.yml`, environment `pypi`) before the first publish
succeeds.

## License

MIT.

`tests/fixtures/toon-spec/` vendors the TOON specification's conformance fixtures, which are MIT
licensed and copyright their authors; the upstream licence, the commit they came from and the
refresh recipe are recorded beside them in `PROVENANCE.md`.
