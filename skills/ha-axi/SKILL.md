---
name: ha-axi
description: Operate a Home Assistant installation through the ha-axi CLI - read and update the entity and area registries that only the WebSocket API exposes, and call services with a capability pre-check and an explained refusal. It also reads entity states and renders templates. Use whenever a task touches home automation: renaming an entity, moving entities between areas, checking what a device is doing, or turning something on.
---

# ha-axi

Agent CLI for Home Assistant. Reads and writes the entity and area registries REST cannot reach and explains a service call Home Assistant refuses. Prefer this over raw curl for Home Assistant operations.

## Configuration

Both values come from the environment. There is no `--token` flag and no credential
file: a token on a command line leaks into shell history and the process table.

```sh
export HA_URL=https://homeassistant.example.com   # or HASS_SERVER
export HA_TOKEN=<long-lived access token>          # or HASS_TOKEN
```

Create the token on the Home Assistant profile page, under Security.
Run `ha-axi doctor` to confirm both transports work; it exits non-zero when they do not.

## Read-only sessions

A third variable makes the session incapable of changing anything. `HA_AXI_READ_ONLY` is a
switch rather than a boolean: **any** non-empty value enables it, `0` and `false` included,
and unsetting it is how writes are allowed again.

```sh
export HA_AXI_READ_ONLY=1
```

Every write is then refused before it is sent, over REST and over the WebSocket alike,
with `code: READ_ONLY` and exit 2. The commands stay visible in `--help` and in the
command table, so a plan that needs one can be recognised as impossible rather than
mysterious. `ha-axi doctor` reports the mode, and the no-argument view shows
`read_only: on` when it is set.

## Running without a global install

```sh
uvx ha-axi state list --domain light
pipx run ha-axi area list
```

## Output

Commands print TOON on stdout and exit non-zero on failure. Add `--human` for a
readable table, or `--json` for raw JSON. Errors are structured on stdout too, and
carry the command that fixes them.

## Commands

### `ha-axi state`

Read entity states from the Home Assistant REST API.

```sh
ha-axi state list --domain light
ha-axi state list --area 'Example Room' --domain light
ha-axi state list --search lamp --limit 20
ha-axi state list --domain sensor --state unavailable
ha-axi state get light.example_lamp
ha-axi state get media_player.example_speaker --full
```

- state is the runtime view; run `ha-axi entity list` for registry names and areas
- --area reads the WebSocket registry, where areas live; it costs one extra round-trip

### `ha-axi service`

List Home Assistant services, read one's fields, and call them.

```sh
ha-axi service list
ha-axi service list --domain light
ha-axi service get light.turn_on
ha-axi service call light.turn_on --target-entity light.example_lamp
ha-axi service call light.turn_on --target-area example_room --data brightness=180
ha-axi service call climate.set_temperature --target-entity climate.example_thermostat --data-json '{"temperature": 21}'
```

- --data-json takes a whole JSON object; --data takes repeated key=value pairs
- a refused call is explained from `/api/services`, which is read on failure only
- --target-area and --target-device pre-check the published capability, because Home Assistant drops an entity that lacks it without saying so

### `ha-axi template`

Render a Home Assistant Jinja template server-side.

```sh
ha-axi template render --template '{{ states("light.example_lamp") }}'
ha-axi template render --template '{{ states.light | count }}'
ha-axi template render --template-file report.j2
echo '{{ now() }}' | ha-axi template render --template-file -
```

- templates run on the Home Assistant instance, so they see every entity it knows about

### `ha-axi entity`

Read and update the entity registry over the WebSocket API.

```sh
ha-axi entity list --area 'Example Room'
ha-axi entity list --domain light --fields entity_id,name,area,platform
ha-axi entity list --area none --limit 500
ha-axi entity list --device <device_id>
ha-axi entity get light.example_lamp
ha-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room
```

- an entity's area is inherited from its device until it is set here explicitly
- name is the name Home Assistant displays: its device's, plus original_name, unless one is set here
- entity_ids are not stable identity: filter by --area or --search, not by guessing ids

### `ha-axi area`

Read and update the area registry over the WebSocket API.

```sh
ha-axi area list
ha-axi area get example_room
ha-axi area create --name 'Example Room'
ha-axi area update example_room --name 'Example Study'
ha-axi area update 'Example Room' --icon mdi:sofa
```

- areas accept an area_id or a name anywhere <id|name> appears
- deleting an area is deliberately not exposed here; use `ha-axi ws area.delete` if you mean it

### `ha-axi device`

Read the device registry over the WebSocket API.

```sh
ha-axi device list
ha-axi device list --area 'Example Room'
ha-axi device list --search example --fields device_id,name,model
```

- an entity with no area of its own inherits the area of its device

### `ha-axi ws`

Send a command over the Home Assistant WebSocket API.

```sh
ha-axi ws --list
ha-axi ws entity.list
ha-axi ws area.update --param area_id=example_room --param name='Example Study'
ha-axi ws --raw config/floor_registry/list
```

- declared names are stable; --raw passes any type straight through to the API
- --params-json takes a whole JSON object; --param takes repeated key=value pairs

### `ha-axi api`

Make an authenticated request to any Home Assistant REST path.

```sh
ha-axi api /config
ha-axi api /states/light.example_lamp
ha-axi api POST /services/light/turn_on --field entity_id=light.example_lamp
ha-axi api POST /template --body '{"template": "{{ now() }}"}'
```

- methods: GET, POST, PUT, PATCH, DELETE, HEAD; GET is used when no method is given
- the registries are not reachable over REST -- use `ha-axi ws` for those

### `ha-axi doctor`

Check the environment, the REST API and the WebSocket API.

```sh
ha-axi doctor
```

- exits non-zero when any check fails, so it works as a CI or hook gate

### `ha-axi setup`

Install or repair the agent integrations for ha-axi.

```sh
ha-axi setup hooks
ha-axi setup skill
ha-axi setup skill --check
```

- hooks give ambient context every session; the skill loads on demand instead -- install either
- hook installation is idempotent and repairs the path after a reinstall or a move

### `ha-axi context`

Print the ambient context a session hook puts in front of an agent.

```sh
ha-axi context
```

- this is the document `ha-axi setup hooks` installs a SessionStart hook to print
- it reads the environment and the command table only: no connection, no token, no installation address, and it exits 0 whether or not this machine has Home Assistant
- for live state -- how many entities there are and what is unavailable -- run `ha-axi` with no arguments instead

## Rules of thumb

- `entity_id` is not stable identity. Find entities by area or by search, and read
  the registry (`ha-axi entity list`) rather than assuming an id means what it says.
- States come from REST; names, areas and platforms come from the WebSocket registry.
  `ha-axi state` and `ha-axi entity` are different views of the same installation.
- An entity with no area of its own inherits its device's area.
- Every command supports `--help`, which is the authoritative reference for its flags.
