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

**A file that cannot carry a marker** — JSON has no comment syntax, and vendored third-party data
must stay byte-for-byte — is exempted in `PATH_ALLOWANCES` in `scripts/leakcheck.py` instead, per
path *and* per rule, and `--rules` prints the table so the exemption is visible where the rules
are. There is one entry today: the vendored TOON fixture whose backslash-escaping case is a
synthetic Windows drive path. `tests/test_leakcheck.py` re-scans each exempted file with the table
switched off and asserts the rules that fire are exactly the ones the entry names — an entry that
has outlived its cause fails the suite rather than quietly covering something new.

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
  command modules return plain JSON-shaped dicts. Do not loosen it to make output prettier. Two
  suites cover it and they are not interchangeable: `tests/test_toon.py` states the behaviour in
  this project's words, and `tests/test_toon_conformance.py` runs the specification's own encode
  fixtures — every one of them, vendored byte-for-byte from `toon-format/spec` under
  `tests/fixtures/toon-spec/` (MIT; provenance, checksums and the refresh recipe live in
  `PROVENANCE.md` beside them). `CASE_COUNT` there is the only place the case count is written,
  and it is asserted, so a fixture that stops being collected fails instead of shrinking the
  score. A rule nobody thought to write a test for reads as passing, which is how 0.3.0 shipped
  two failing cases while the README claimed strictness.
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

- **The canonical decimal range is wider than Python's float repr.** Spec section 2 makes decimal
  form a MUST for `0` and for `1e-6 <= |n| < 1e21`; `repr` leaves decimal form outside roughly
  `[1e-4, 1e16)`, so `json.dumps` alone violates that MUST in the band at each end — and both bands
  are ordinary sensor data (a current reading in amps, a byte counter), reachable through
  `state get --full`. `_number` formats through `Decimal(repr(value))` inside the range and defers
  to `json.dumps` outside it, where an exponent is permitted. `Decimal(value)` would be wrong:
  it expands the exact binary value instead of the shortest round-tripping digits.
- **Tabular form is not available in list-item position.** A tabular header on a hyphen line is a
  keyless fields-bearing header, which section 6 allows only at the document root, so section 9.4
  requires list form however uniform the items are. `array()` carries `allow_tabular` and
  `list_item()` passes `False`; the restriction is the position, not the depth, so a *key* inside a
  list-item object still reaches tabular form. Reachable through `api` and `ws --raw`, which hand
  arbitrary Home Assistant JSON to the encoder.
- **Null is meaningful over WebSocket.** `config/entity_registry/update` with `name: null` is how a
  user override is cleared, so `WsClient.send_command` must not filter `None` out of a payload.
  It did once; `--clear-name` silently did nothing. There is a test for this.
- **`entity_id` is not stable identity.** Filter by area or search; do not infer meaning from an id.
- **An entity's displayed name comes from two registries, and most entities do not carry their
  own.** `registry_name` transcribes Home Assistant's `_async_get_full_entity_name` as
  `async_get_full_entity_name` calls it — `parts=(DEVICE, ENTITY)`, `use_legacy_naming=True` — which
  is two rules: a `name` somebody set wins **outright**, device prefix and all, and everything else
  is the device's display name (`name_by_user or name`) joined to `original_name`, whichever of the
  two is present. There is no third rule, and in particular **`has_entity_name` is not a gate**:
  Home Assistant applies it on the way out, publishing `original_name_unprefixed` under the
  `original_name` key in `as_partial_dict` (and therefore in `extended_dict` too), so what arrives
  over the WebSocket is already the entity's half alone and both settings of the flag compose
  identically here. That the state's `friendly_name` and this view agree is not a coincidence to be
  maintained: `Entity.__async_calculate_state` calls the same `async_get_full_entity_name`, so there
  is one rule and two readers of it. Measured against a live 2026.8.3 instance, reading the entity
  row alone agreed with the displayed name for **14 of 88** entities; this rule agrees for **88 of
  88**. Every earlier proposal that gated composition on `has_entity_name` reaches 83 — the five it
  misses are exactly the entries whose whole name is their device's and whose flag is unset.
  `matches_search` sees the composed name, which is what makes `entity list --search '<the name a
  user sees>'` work; it answered `0` for four entities in five in 0.3.2.
- **An entity with no `area_id` inherits its device's area.** Any per-area count or filter that
  ignores the device fallback will be wrong.
- **An `area_id` no area answers to is not a placement.** Home Assistant accepts
  `entity.update --param area_id=<typo>` without complaint, and the entity is then invisible to
  `--area <id>` (there is no such area) *and* to `--area none` (it has an `area_id`), while
  `area list`'s per-area counts and `unassigned_entities` quietly stop summing to the size of the
  registry. `area_is_placed` is the one predicate for this: `filter_by_area`'s `none` branch and
  `area._entity_counts` both treat an unplaced id as unassigned, so the totals reconcile and the
  entity is findable, and `entity get`/`entity update` report `area_source: no area has this id`
  rather than `entity`, because "unassigned" and "holding an id nothing answers to" are different
  facts. A real area delete does not cause this — Home Assistant clears the id itself — so it takes
  a typo, through a *declared* command rather than `--raw`.
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
- **A device id is opaque, so `entity list --device` is the only route from a device to its
  entities.** It is not searchable (and should not be: substring-matching an opaque hex id is an
  accident, not a filter), and `device list --fields entities` prints a *count*, not ids. Before the
  flag existed there was no route at all — and `service call`'s help line for a device target that
  reached nothing suggested `entity list --search <device_id>`, which answered
  `0 registry entries found` every time it was run. Suggestions have to be runnable; that one was
  checkable with no server at all.
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
  made to depend on the message text or the status number. **And the status number is not even
  always 400:** the two refusals that come from a `HomeAssistantError` rather than a `vol.Invalid` —
  a named entity lacking a capability, and a `--response` call that matched nothing — arrive as a
  plain-text `500` with a fixed apology for a body. Three distinct statuses, none of them carrying a
  reason. A client that switched on either would be right by accident at best.
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
  device in silence — 200 with an empty list, and nothing said. An `unavailable` entity is skipped
  just as silently however it was named — its capability is never read — so neither the pre-check
  nor the failure-path enrichment blames it for lacking one. The pre-check covers the silent half,
  and the loud half is enriched on the failure path where it is free. `--no-check` exists because
  a published requirement is an integration's claim about itself, and a wrong one must not become
  a wall.
- **An empty change set is two different answers.** Home Assistant returns the states that actually
  changed, so `[]` means both "everything was already as asked" and "nothing was reached at all" —
  and it never says which. `service call` resolves the target when, and only when, the change set
  is empty and a target was given: reaching nothing exits 1, reaching something exits 0 with the
  count. Which domains a service can reach is read from its published `target`, never guessed from
  its name — an integration is free to act on another domain's entities, and several do.
- **`--response` turns the empty-change-set question into a 500.** The two-worlds answer above is
  only reachable on the success path, because reaching nothing is a `200 []`. With `return_response`
  set it is not: `helpers/service.py` raises
  `HomeAssistantError("Service call requested response data but did not match any entities")` after
  filtering candidates by availability, device class and feature, and aiohttp renders that as a
  **bodyless 500** — the one command shape whose failure carries nothing at all to read. So
  `_explain` re-derives the same verdict from the target, last, after every check that explains the
  refusal from what was *sent*; `_no_entities_targeted` is shared with `_report_target` so the two
  sides of one outcome cannot be phrased differently. In 0.3.2 this fell through to `_generic_help`
  and offered help about *fields*, which were never the problem.
- **`unavailable` and `unknown` are different facts and the home view counts them apart.** `unknown`
  means reachable and not yet reporting, which is the common one — a live instance had 12 `unknown`
  and 0 `unavailable`. Summing them under the name of one of them made the landing view, the one
  `setup hooks` puts in front of every session, contradict `state list --state unavailable` outright.
- **A 404 that carries a message is not a wrong path.** aiohttp answers an unrouted path with
  plain-text `404: Not Found` and no body; a routed path whose *subject* is missing answers in JSON
  — `/states/<id>` says `Entity not found.`. `rest._http_error` quotes the message when there is one
  and only says `no such API path` when there is not, because telling an agent the path is wrong
  sends it looking for a spelling mistake that is not there.
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

**Open, and deliberately not answered here:** `service get`'s default field list is
`field,required,type,description`, and `description` is empty on **every row of a real
installation** — the prose moved into the translation files years ago and `/api/services` does not
serve them, while `example` (published by more than half of all fields) and the
`filter.supported_features` marker are not shown by default. The double now publishes a service and
fields with no prose, so the emptiness is visible in the suite rather than being a surprise. Changing
the default is a judgement about what an agent most needs to see, not a defect, and it belongs in its
own change with its own argument.

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
pytest                                   # ~590 tests, a couple of seconds
ruff check . && ruff format --check .
ha-axi setup skill --check               # SKILL.md is generated, never hand-edited
```

**Do not edit a vendored conformance fixture.** If one fails, the encoder is wrong until proven
otherwise; the checksum test will catch the edit anyway. Refreshing them from upstream is its own
commit, separate from any encoder change made to satisfy it, and `PROVENANCE.md` carries the recipe.

**Tests never need a live installation or a live token, and must not start to.** They run against
real loopback servers in `tests/conftest.py`: an `http.server` for REST and a real `websockets`
server that performs the Home Assistant `auth_required` / `auth` / `auth_ok` handshake. If a
behaviour cannot be tested that way, say so in the PR rather than reaching for real credentials.

**Calibrating a fixture against reality is a different job from testing, and it has its own lab.**
The suite must stay offline; deciding *what shape the data has* cannot be done offline at all, and
guessing it is what produced every defect below. The recipe is a throwaway container on loopback,
which mints its own credential and is discarded afterwards — never a real installation:

```sh
docker run -d --name ha-lab -p 127.0.0.1:<port>:8123 -v "$PWD/haconfig":/config \
  ghcr.io/home-assistant/home-assistant:stable
#  POST /api/onboarding/users {client_id,name,username,password,language} -> auth_code
#  POST /auth/token  grant_type=authorization_code                        -> access_token
#  WS   auth -> {"type": "auth/long_lived_access_token", "client_name": ..., "lifespan": 30}
#  POST /api/onboarding/core_config and /analytics; then append `demo:` plus a couple of
#  template sensors to /config/configuration.yaml *inside* the container and restart.
```

That yields ~126 states over ~91 registry entries with the real distribution, and the upstream source
is readable at `/usr/src/homeassistant/homeassistant/` in the same container — which is how the name
rule above was transcribed rather than guessed. Nothing measured there may be written into this
repository: the numbers are, the data is not.

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
- **Not every refusal is a `400`, and two of them carry nothing.** A `HomeAssistantError` is not
  caught anywhere, so aiohttp renders it as a plain-text `500` with a fixed apology and no message:
  that is what a named entity lacking a capability gets (`ServiceNotSupported`), and what a
  `return_response` call that matched no entity gets. The double answers both with `_server_error`.
  Modelling them as helpful JSON `400`s — which it did — licensed a client to read a status number
  and a message that never arrive, and made the second case unreachable altogether.

**Make the fixtures less convenient, not more.** The refusals above were modelled with unusual care
and the *data* was not, and that is where every defect in the 0.3.2 audit came from: a registry where
every entry named itself, a state that was never `unknown`, a service that documented itself, an
entity that was never disabled. All four are the majority case upstream, all four were reachable by
hand, and none of them was visible to 715 passing tests. The fixture set now carries the
distribution a real installation has — entries named entirely by their device, entries naming only
their own half, both settings of `has_entity_name`, a disabled entry with **no state at all**, a
state with no registry entry, an entity with no device, a device in no area, an `unknown` alongside
an `unavailable`, a service and fields publishing no prose, all 21 keys of `as_partial_dict`, and the
larger `extended_dict` (with `aliases: [null]`) from `get`/`update` but not from `list`.
`tests/test_double_fidelity.py` asserts each of those shapes is still present and that every
registry entry's composed name equals the `friendly_name` on its state, so a fixture edit that
quietly tidies one away fails where the reason is written down. The bar for a fixture change is that
**the shipped code before the fix would fail against it**; if the suite still passes against the old
behaviour, the fixtures have not been corrected.

Two more rules that fall out of that:

- **Every command in `ha_axi.ws.REGISTRY` gets a branch in `_respond`.** Six of the fourteen fell
  through to `unknown_command` and therefore had no coverage at all, five of them while being
  declared in `WS_COMMAND_KEYS`. `test_every_websocket_command_the_cli_ships_is_modelled` is
  parametrised over the registry, so a new command is untestable until the double answers it.
- **The double's own helpers are transcriptions, not imports.** `displayed_name` and `slugify` in
  `tests/conftest.py` are written from `helpers/entity_registry`, the same way `capability_masks` is
  written from `helpers/service`. `displayed_name` is what makes the state/registry agreement an
  assertion rather than a coincidence; importing `ha_axi.commands._common.registry_name` there would
  have made the test pass with the bug in place.

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
— those hold the version a release will *write*. During bootstrap, before the first publish, the
manifest deliberately trailed the source: baseline `0.0.0` with source `0.1.0` meant "nothing
released yet, the next `feat:` lands 0.1.0". That period is over — PyPI hosts 0.1.0 and 0.2.0, and
the manifest, `pyproject.toml` and `src/ha_axi/__init__.py` all sit at `0.2.0` — but the rule it
taught still holds: never "fix" a mismatch by raising the baseline to match the source; that tells
release-please the version is already out and it bumps past it, permanently skipping a version
number PyPI will never let us reuse.

**A commit message release-please cannot parse is dropped silently, and the run stays green.**
`parseConventionalCommits` wraps every parse in `try { … } catch { logger.debug(…) }`, so an
unreadable message costs a commit and reports nothing: no changelog entry, no version bump, and a
release run that exits 0. It cost the sibling AXI project a release — a merged fix left unpublished
behind a green workflow — and **this repository's history parses by one word.** `46c25f9` carries
the same paragraph about the same fix as the message that broke over there, and the only difference
is that the term reaching the parser sits a few words into its line here rather than starting one.
Both messages are vendored under `tests/fixtures/commit-messages/` and the suite asserts exactly
that: same prose, same term, opposite verdicts.

**The rule, established against the parser rather than guessed at.** release-please 17.3.0 parses
with `@conventional-commits/parser` (`^0.4.1`), whose grammar offers **every physical body line** to
`<footer> ::= <token> <separator> <whitespace>* <value>`. `<token>` is `<type> ["(" <scope> ")"]`,
and `<type>` consumes from the line start until whitespace, a newline, `!`, `:`, `(` or `)`. If it
stops on `(` the parser is committed to a scope: it reads to the next `(`, `)` or newline, and if
that is not `)` it **throws** (`lib/parser.js:177`) — the only `throw` reachable from the body, and
the only production that raises rather than returning an `Error` its caller can back out of. So

- `` `Decimal(repr(value))` inside … `` at a line start — **refused**;
- `… through `Decimal(repr(value))` inside … ` — **fine**, one word further along.

It is not parentheses, not backticks, not the `-` used as a dash, and not position alone: it is the
interaction. This repository's own copy of that paragraph parsed for exactly this reason, which is
luck, not design. `scripts/commitcheck.py --rules` prints the rule with its citation, and
`--demo` proves the checker still tells the shapes apart.

**Two engines, and the reason there are two.** `vendor/conventional-commits-parser/` is a
byte-for-byte copy of the four dependency-free upstream modules (ISC; provenance, checksums,
refresh recipe and the reason `utils.js` is excluded are in its `PROVENANCE.md`), so `--engine node`
runs *the* parser with no `npm install` and no network. `--engine python` is a transcription of the
same grammar, so a machine without `node` gets a verdict rather than a skip. `--engine auto` — the
default, and what the hooks use — prefers `node`. `tests/test_commit_message.py` runs the whole
corpus through both and compares the verdict, line, column and token; the transcription is only
worth anything because that comparison passes, and CI installs `node` so it is never skipped there.
It has already earned its keep: it caught the node path's error regex failing to match when the
offending token was a newline, which would have silently downgraded a real rejection.

**Do not solve this by banning rich commit bodies.** The bodies carry the reasoning that makes this
history worth reading, and a guard that made prose the problem would be answered by writing less of
it. `DEMO_ACCEPTED` in `commitcheck.py` pins the shapes that must keep working — nested parentheses
mid-line, markdown bullets, footers, breaking-change notes, a full rich body — and
`test_the_rule_is_the_interaction_and_not_any_one_ingredient` asserts each ingredient alone is fine.
A change that makes one of those fail is a regression in the guard, not a discovery about the prose.

**Three layers, and one of them is the real fix.**

- `.githooks/commit-msg` runs `commitcheck.py` after `leakcheck.py`. This is the one that matters:
  it rejects the message before it can reach `main`, and it names the line, the column and what to
  change.
- `.github/workflows/release.yml` has a `commit-audit` job that re-checks every commit since the
  last release tag. It deliberately does **not** `needs:` the release-please job — it has to fail on
  its own account, including on a run where release-please itself errored. It exists because a hook
  cannot see a message typed into GitHub's squash-merge box.
- `.github/workflows/ci.yml` runs the same audit nightly, so an allowance that has outlived its
  cause surfaces without waiting for a merge, and installs `node` in the `test` job so the
  agreement between the engines is enforced rather than skipped. Nightly matters more than it looks
  now that the audit reads pull request bodies: a body edited a week after the merge changes what
  the next release contains, with nothing else having run in between.
- `.github/workflows/hygiene.yml` gained a step, which is the one exception to "keep this workflow
  to the one cheap job". It is not coverage for its own sake: it checks the pull request body, which
  exists *only* on a pull request, never passes under a hook, and can replace the merged commit
  message outright. Its trigger list carries `edited` for the same reason. Still one job, still
  seconds.

The claim that used to sit here — "`hygiene.yml` was deliberately left alone; the release audit
already covers what a PR-time check would" — was false, and cost the sibling project its second
release in a row. The release audit runs *after* the merge. Nothing looked at the body before it.

**The audit's verdict is only as wide as its reach, and it has to say when it is narrower.**
`resolve_bodies` has three modes and no fourth: `require` (the workflows) fails without a token
rather than checking a different artefact and calling it green, `auto` (a developer's checkout)
consults GitHub when it can and prints `NOT consulted` in the output when it cannot, and `skip` is
git only, on purpose, which is what the unit tests pass so they never reach the network. There is
deliberately no silent fallback: silent fallback to the wrong string is precisely the state the
first version of this guard shipped in. A per-commit miss is not an
outage either: the commits endpoint answers 422 for a SHA GitHub does not have, which is the
ordinary state of a local branch, so `resolve_bodies` reaches the repository once before the loop
and only then reads a miss as "no pull request" — and names the commits it applied to. Reading a 404
as "no pull request" without that probe would let a token with no access report an all-clear for
every commit, which is this same blind spot from the other side.

**The audit reads `--first-parent`, because that is what release-please reads.** It asks GitHub for
the *merge commits on the branch*, not for everything reachable from it. A plain `git log` would
report a work-in-progress message inside a merged branch as a commit release-please dropped, and a
guard that cries wolf gets switched off.

**`KNOWN_UNPARSEABLE` is the `PATH_ALLOWANCES` pattern applied to a commit.** One full SHA, one
reason, printed by `--rules`, and pinned by the suite: an entry whose commit now parses fails
`test_every_known_unparseable_entry_is_still_earning_its_place` rather than quietly covering
something new. It is matched on the **full** SHA — a prefix is not an identifier, and that is the
same defect the leak scanner's trailing path match had, one layer down. **It is empty here, and that
is a measured fact**: every commit in this repository was run through the parser and all of them
parse, which `test_every_commit_in_this_repositorys_history_is_readable` re-checks on every run. An
entry is only ever added for a message that has already been lost and whose content has been
accounted for somewhere the changelog names.

**The message is not always the message, and that is what the first version of this guard got
wrong.** It diagnosed the grammar rule correctly, shipped three layers built on it, and then passed
— green, twice, in the sibling project — on a release that considered zero commits. release-please
does not parse the commit message. It parses `splitMessages(preprocessCommitMessage(commit))`, and
`preprocessCommitMessage` is four lines:

```js
const overrideMessage = (commit.pullRequest.body.split('BEGIN_COMMIT_OVERRIDE')[1] || '')
  .split('END_COMMIT_OVERRIDE')[0]
  .trim()
if (overrideMessage) return overrideMessage
```

`String.split` finds that literal **anywhere in a pull request body**, including in a sentence that
merely names it. The pull request that shipped the guard had a body explaining this very mechanism,
so release-please threw the commit message away and parsed the paragraph after the word instead. It
began `block from the PR body when there is one`; `block` is five characters; the parser stopped on
the space after it and reported `unexpected token ' ' at 1:6`. Column 6 makes no sense on a
`fix(ci):` subject, which is the clue that the text being parsed was not the subject at all. The
commit message parsed perfectly — and a checker that reads commit messages therefore said so.

**Three artefacts reach release-please and only one of them passes under a commit-msg hook.**

| artefact | written | checked by |
| --- | --- | --- |
| the commit message | locally, by a developer | `--commit-msg` (the hook) |
| the merge commit's message | in GitHub's squash box | `--since-release` (after the merge) |
| the pull request body | in GitHub's editor | `--pull-request` (`hygiene.yml`) |

The body is the dangerous one. It *replaces* the other two, it can be edited after every check has
run, and nothing in the repository records it — so `--since-release` and `--commit` resolve it from
the GitHub API rather than trusting `git log`, and `--pull-requests require` (what the workflows
pass) makes a missing credential a failure. This is also why
`test_every_commit_in_this_repositorys_history_is_readable` is not the whole claim it looks like: it
proves every *message* here parses, which is a statement about git, not about what release-please
read.

**One rule here is stricter than upstream, deliberately.** Upstream is content with an override
block that is never closed — it simply reads to the end of the body. That is exactly the shape an
accidental mention takes, so `override_faults` refuses a block that names the marker and never closes
it. Without that rule an accidental mention whose next paragraph *happened* to parse would silently
become the changelog entry. An empty block is not refused: upstream's `if (overrideMessage)` is falsy
on an empty string, so a body ending on the marker loses nothing and crying wolf at it would train
somebody to stop reading the output.

**The other fidelity note.** One commit may carry several conventional commits, split on
`BEGIN_NESTED_COMMIT` or on a blank line before a new `type:` line. `split_messages` transcribes that
so a message that loses only *part* of itself is still refused.

**If a fix ever is dropped here, releasing it.** Landing a parseable commit makes release-please
re-scan the range, but the unparseable commit is dropped again and never reaches the changelog — so
the release notes would omit the very fix being shipped. The route is to restate it: give the new
commit message a second conventional-commit section for the dropped work, which `splitMessages`
turns into its own changelog entry, and record the dropped SHA in `KNOWN_UNPARSEABLE` saying where
its content went. Do not rewrite `main` to fix the original message; a tag or a published sha is not
worth the history.

**`scripts/commitcheck.py` and `vendor/conventional-commits-parser/` are shared with the sibling AXI
project, and are byte-identical apart from `KNOWN_UNPARSEABLE`.** Two copies that behave differently
are worse than one that is wrong — the same rule `toon.py` is held to. A change to the grammar
transcription, the engines or the audit belongs in both repositories in the same sitting.
