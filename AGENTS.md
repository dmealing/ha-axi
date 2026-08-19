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
- `commands/` — one module per noun, each exposing `COMMAND` and `run(ctx, sub, parsed)`. Adding a
  noun is one new file plus two lines in `cli.py` (`COMMAND_ORDER` and `_MODULES`); root help,
  `SKILL.md` and the parametrised test sweeps all derive from those. A `pkgutil` scan would save
  the two lines, cost static analysis, and still need an explicit order — it has been costed and
  is not worth it.
- `servicemodel.py` — a pure reader for what `GET /api/services` publishes. No I/O and no cache:
  the caller fetches, and decides whether the answer is worth the round-trip.

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
- **Every view that reports an entity's area builds it with `_row()`.** `effective_area_id` is
  applied there; its other call sites (`state list --area`'s filter, `area list`'s counts) never
  print a per-entity area. A view that reads `entry["area_id"]` directly reports `""` for an entity
  whose area comes from its device. `entity update` did exactly that: it answered from the update
  payload and the pre-update entry, without ever reading the device registry, so a rename of a
  device-placed entity replied `area: ""` while `entity get` on the same entity said otherwise. The
  data was never damaged — but an agent reads the update response, and an empty area there reads as
  "unassigned". Update and get now share `_row()` and both carry `area_source`.
- **`state` (REST) and `entity` (WebSocket) are different views.** Names and areas exist only in the
  registry; states exist only over REST. `state list --area` therefore reads the registry over the
  WebSocket — the only data command that crosses transports; `doctor` uses both too, but only to
  check them — and pays that round-trip only when the flag is passed. The flag exists because an
  agent that learns `--area` on `entity list` will reach for it on `state list`; a filter is not
  the same as importing registry columns into the runtime view, which is why `area` is still not a
  `state list --fields` choice.
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
- **Home Assistant refuses a service call with an *empty* 400.** `APIDomainServicesView.post`
  raises `HTTPBadRequest` from the underlying `ServiceNotFound` or `vol.Invalid`, and aiohttp
  renders the status line with no JSON body. An unknown service, an undeclared field and a missing
  required one are therefore indistinguishable on the wire. Everything `service call` says about a
  refusal comes from the model instead, read in `_explain` — which is why that path must never be
  made to depend on the message text or the status number.
- **The one message that must never be printed verbatim.** A response-only service refused without
  `?return_response` comes back with Home Assistant's own wording, naming a query parameter of its
  REST API that an agent driving this CLI cannot set. `_explain` answers that case *before* it
  reads the model, because it is knowable without one and the leak must not survive a failed fetch.
  The flag is `--response`, in both directions.
- **`supported_features` is published as integers, and the list is a disjunction.** Home Assistant
  resolves the enum names in `services.yaml` before publishing, and its own rule is
  `any(features & mask == mask for mask in masks)` — any one mask, but every bit of that one. That
  disjunction is how an upstream fallback is encoded: `media_player.volume_up` publishes VOLUME_SET
  *and* VOLUME_STEP because core backs a player that cannot step with one that can set. Reading the
  list as a conjunction would gate exactly the behaviour that works today, which is the A11 caveat
  in the maintainer's tool-design guide. `servicemodel.satisfies` is that rule and nothing else.
- **A capability requirement is only read for the service's own domain.** `reolink.ptz_move`
  targets `button` entities and names a `camera` feature; checking a button against a camera's bits
  would refuse every call. `feature_masks` returns nothing unless the published entity filter names
  exactly the service's own domain, and nothing if a value did not resolve to an integer.
- **The capability gate is a pre-check for area and device targets only.** Home Assistant refuses
  an entity *named outright* that lacks the feature, but skips one reached through an area or a
  device in silence — 200 with an empty list, and nothing said. So the pre-check covers the silent
  half, and the loud half is enriched on the failure path where it is free. `--no-check` exists
  because a published requirement is an integration's claim about itself, and a wrong one must not
  become a wall.
- **An empty change set is two different answers.** Home Assistant returns the states that actually
  changed, so `[]` means both "everything was already as asked" and "nothing was reached at all" —
  and it never says which. `service call` resolves the target when, and only when, the change set
  is empty and a target was given: reaching nothing exits 1, reaching something exits 0 with the
  count. Which domains a service can reach is read from its published `target`, never guessed from
  its name — an integration is free to act on another domain's entities, and several do.
- **A diagnostic read must not fail a call that worked.** The target report needs the registries,
  which the REST-only path cannot supply. If that read fails, the report says so and the exit code
  stays 0: the call itself was accepted, and turning it into an error would be a fresh untruth.

## The command contract

`ha-axi` reaches every service through `service call` and every WebSocket type through `ws --raw`.
Coverage is already complete, so a typed command is never justified by reach. What the typed
commands add is judgement, and judgement is the one thing a generator cannot emit — measured:
Home Assistant's model is complete enough to generate every flag (99% of 1,939 declared fields
carry a typed selector) and structurally incapable of generating the checks that stop the bugs,
because the requirement Home Assistant actually *enforces* is the `required_features` argument to
`async_register_entity_service`, which 79 entity services pass and none export.

Two things are easy to confuse here, and the gate depends on telling them apart. The enforced
requirement lives in Python and is invisible. A **separate** declaration, `target.entity[]`'s
`supported_features` in `services.yaml`, *is* published — 91 services in the bundled catalogue carry
one, `media_player.media_next_track` among them — and Home Assistant resolves it to integers on the
way out. That published declaration is what the pre-check reads. It normally mirrors the enforced
one, but it is an integration's claim about itself and the two can disagree, which is the whole
reason `--no-check` exists.

**Do not generate commands from the service model.** At this scale it would mean 77 nouns and ~327
subcommands where there are 10 and 19, roughly 30× the `--help` budget, `light turn_on` colliding
with `service call light.turn_on` for every service, and 19 flags on one subcommand of which 17 are
conditional on capabilities nothing checks. Consuming the same model to *validate, explain and
recover* has none of those costs and is what `servicemodel.py` is for.

**The promotion rule.** A service becomes a typed command only when the command would do something
`service call` cannot — and that something must be named in the PR. In the order to check it:

1. **It crosses transports or registries.** The answer needs the WebSocket registry as well as
   REST, or needs the device-area fallback. (`state list --area` is the existing instance.)
2. **It needs a capability check before dispatch,** and Home Assistant publishes no fallback for it.
3. **It needs a state-aware no-op** — the idempotent "already matches the requested values" answer,
   which is only possible by reading current state first.
4. **Its failure needs candidates** from an open, installation-specific set — `source`,
   `sound_mode`, `effect`, `preset_mode`, `hvac_mode`. These live in the entity's *attributes*
   (`source_list`, `effect_list`, `hvac_modes`), not in the service schema.
5. **It needs a shaped result** — a derived summary rather than a change list, the way
   `entity update` answers with the resulting registry row.

If none of the five applies, it does not get a command: `service call` already covers it, and
adding one is two spellings for one operation.

**Demotion, and the standing cap.** If a typed command's body reduces to flag-mapping plus a
request, delete it — the measure is the diff, not the intention. Ten nouns fit in a root help block
an agent reads in one glance; an eleventh has to argue that it earns its line. `--data key=value`
stays first-class in every case, because it reaches every field of every service forever with no
metadata to go stale.

**Never validate an argument against metadata you cannot refresh.** Undeclared flags are rejected by
name, so stale metadata converts a valid operation into a hard failure with a "valid flags" list
that is wrong. Either read the model live at the moment you enforce it — as `service call` and
`service get` do — or do not enforce it and let the value through to Home Assistant, which owns the
schema. This is also why the model is never cached: an integration added or removed rewrites it and
nothing signals when.

## Build, test, lint

```sh
pip install -e ".[dev]"
pytest                                   # ~380 tests, a couple of seconds
ruff check . && ruff format --check .
ha-axi setup skill --check               # SKILL.md is generated, never hand-edited
```

**Tests never need a live installation or a live token, and must not start to.** They run against
real loopback servers in `tests/conftest.py`: an `http.server` for REST and a real `websockets`
server that performs the Home Assistant `auth_required` / `auth` / `auth_ok` handshake. If a
behaviour cannot be tested that way, say so in the PR rather than reaching for real credentials.

**The doubles must answer like Home Assistant, not like the client.** A double that accepts and
echoes whatever it is sent can only prove the client agrees with itself, which is how the service
call wire-shape defect and the `entity update` area defect both reached a live installation with a
green suite. Two rules follow, and neither is optional:

- **Model the refusals, not just the successes.** The REST double rejects a nested `target` because
  Home Assistant's `PREVENT_EXTRA` schema does; the WebSocket double rejects any key outside
  `WS_COMMAND_KEYS` with `invalid_format` for the same reason. That table is deliberately *not*
  imported from `ha_axi.ws.REGISTRY` — a second opinion that is a copy of the first is not one. A
  new client parameter will be refused here until it is added to the table too; adding it is how
  the parameter gets confirmed rather than assumed.
- **Answer with resulting state.** `config/entity_registry/update` returns the stored entry — every
  field, including ones the request never mentioned, and an `area_id` that stays `null` when the
  area belongs to the device. Every result is JSON round-tripped on the way out, so a client can
  never hold a reference into the double's state and pass by sharing an object with it.
- **Refuse the way Home Assistant refuses, including when that means saying nothing.** The REST
  double answers an unknown service, an undeclared field and a missing required one with a bare
  `400` and no body, because that is what `HTTPBadRequest from vol.Invalid` renders. A double that
  helpfully explained itself would let a client pass that could never explain a real refusal. It
  also returns only the states that actually *changed*, skips an `unavailable` entity in silence,
  and skips one lacking a published capability — which is what makes "nothing to do" and "nothing
  targeted" two testable worlds rather than one string. `SERVICES`, `capability_masks`,
  `target_domains` and `entities_targeted` in `tests/conftest.py` are that second opinion and are
  deliberately not imported from `ha_axi.servicemodel`.

Three testing gotchas already paid for:

- `websockets`' sync `Server.serve_forever()` takes **no** arguments. Only the stdlib HTTP server
  accepts `poll_interval`, which the REST double uses to keep teardown off the critical path.
- The two doubles listen on different ports. `FakeInstallation` puts a front door in front of both
  and gives out one `HA_URL`: it reads each connection's request line and splices the connection to
  the WebSocket double for `/api/websocket` and to the REST double for everything else, which is
  the topology a real instance has. Use the `installation_env` fixture for anything that crosses
  transports — `state list --area`, `doctor` — and `rest_env` / `ws_env` when only one is in play.
  Routing on the request line alone is what keeps request bodies out of it; do not teach the front
  door to parse a body.
- Closing a listening socket does not reliably wake a thread blocked in `accept()`, so
  `FakeInstallation.stop()` opens one throwaway connection to knock, then joins. A fixture that
  merely closes the socket leaks a thread per test.

`skills/ha-axi/SKILL.md` is generated from the CLI's command table. Change the commands, then run
`ha-axi setup skill` and commit the result; CI fails if the two disagree.

Supported Pythons are 3.9 through 3.12. `from __future__ import annotations` is what makes the
`X | None` annotation syntax safe on 3.9 — keep it at the top of every module.

## Continuous integration

Three workflows, split by where the work is cheap:

- **`.github/workflows/ci.yml`** — the heavy matrix (leak scan, lint, `pytest` on 3.9 through 3.12,
  the generated-skill check) on the maintainer's self-hosted runner. Triggers: push to `main`, a
  nightly `schedule`, and `workflow_dispatch`. Never pull requests.
- **`.github/workflows/hygiene.yml`** — the leak scan alone, on `ubuntu-latest`, on `pull_request`.
  Exactly one GitHub-hosted check per PR, and it takes seconds.
- **`.github/workflows/release.yml`** — GitHub-hosted, and to stay that way: OIDC trusted publishing
  needs `id-token: write` on a GitHub-hosted runner.

**`ci.yml` must never gain a `pull_request` trigger.** This repository is public and the runner
is the maintainer's own workstation. Every trigger it has requires write access, so fork-submitted
code cannot reach the machine; `pull_request` would hand any contributor on the internet code
execution on it, in one line, with no other visible symptom. The reasoning is repeated at the top of
the file so it survives someone later "helpfully" adding PR coverage.

**A thin PR check is the design, not an oversight.** Every change goes through the local no-mistakes
gate — review, tests, lint, docs — before a PR is opened, so GitHub-hosted CI is not the primary
quality signal here. Do not add jobs to `hygiene.yml` to make pull requests look better covered. The
arrangement this replaced triggered the full matrix on both `push: branches: ["**"]` and
`pull_request`, so every PR branch ran it twice on identical commits; one copy went green while its
twin sat queued for over an hour, leaving the PR permanently "unstable".

The nightly cron deliberately avoids 08:17 UTC, which a sibling project's self-hosted workflow holds
on the same workstation.

**A workflow can only be dispatched if the file is already on the default branch.** `workflow_dispatch`
resolves the workflow id against `main`, so a brand-new workflow file 404s on its own branch and
cannot be proven to work until after it merges. That is why the self-hosted workflow kept the
filename `ci.yml` instead of taking the sibling project's `local-ci.yml`: reusing the registered name
is what allowed the real file to be dispatched on its branch and watched through to completion on the
runner before anyone merged it. Same trick applies to any future workflow worth verifying early.

`actions/setup-python` does supply all four versions on that runner — actions/python-versions has
`linux` / `x64` / `22.04` builds for 3.9 through 3.12, and they land in the runner's persistent tool
cache, so only the first run pays the download. If that ever stops holding, `uv` is on the runner's
`PATH` and `uv python install` is the fallback; do not answer it by dropping a version from the
matrix.

Checkouts on the self-hosted runner pass `persist-credentials: false`. That workspace outlives the
job, and a token left behind in its `.git/config` would outlive it too.

**A self-hosted runner runs as a real user, and that user's `~/.local` is on every job's path.**
`~/.local/lib/python3.X/site-packages` is keyed by X.Y only, so it is picked up by an interpreter
`actions/setup-python` just unpacked into the tool cache, and `~/.local/bin` sits ahead of that
interpreter's `bin` on `PATH`. A bare `pytest`, `ruff` or `ha-axi` therefore runs the maintainer's
copy, under `/usr/bin/python3.X`, against whatever checkout that copy points at. The first run of
this workflow demonstrated both halves: py3.9 and py3.12 passed because no user site exists for those
versions, while py3.10 and py3.11 failed with `ModuleNotFoundError: No module named 'ha_axi'`, and
the lint and skill jobs went green having exercised the maintainer's binaries rather than the
commit's. Every job that needs third-party packages therefore does `python -m venv --clear .venv` and
calls tools as `.venv/bin/<tool>`; a venv sets `ENABLE_USER_SITE = False`, so the leak cannot happen.
`--clear` because the workspace is reused between jobs. Do not "simplify" these back to bare tool
names.

## Releasing

release-please owns the version. `.release-please-manifest.json` records the **last released**
version, which is not the same thing as the version in `pyproject.toml` and `src/ha_axi/__init__.py`
— those hold the version a release will *write*. Until the first publish the manifest trails the
source on purpose: baseline `0.0.0` with source `0.1.0` means "nothing released yet, the next
`feat:` lands 0.1.0". Do not "fix" that mismatch by raising the baseline to match the source; that
tells release-please the version is already out and it bumps past it, permanently skipping a version
number PyPI will never let us reuse.
