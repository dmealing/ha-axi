"""Registry commands, exercised against a real local WebSocket server.

These run the full protocol: the auth handshake, id correlation, and the
command/result exchange. Nothing is mocked, so the client's framing and error
translation are actually covered.
"""

from __future__ import annotations

import time

import pytest

from conftest import FAKE_TOKEN


def test_entity_list_resolves_area_names_in_the_default_view(run_cli, ws_env):
    code, out = run_cli(["entity", "list"], ws_env)
    assert code == 0
    assert "entities[3]{entity_id,name,area}:" in out
    assert "light.example_lamp,Example Lamp,Example Room" in out


def test_entity_list_falls_back_to_the_original_name(run_cli, ws_env):
    _, out = run_cli(["entity", "list"], ws_env)
    assert "light.example_ceiling,Example Ceiling," in out


def test_entity_inherits_its_device_area_when_it_has_none_of_its_own(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--fields", "entity_id,area,area_id"], ws_env)
    assert code == 0
    # light.example_ceiling has no area_id but its device sits in Example Hall.
    assert "light.example_ceiling,Example Hall,example_hall" in out


def test_entity_list_filters_by_area_name_or_id(run_cli, ws_env):
    _, by_name = run_cli(["entity", "list", "--area", "Example Room"], ws_env)
    _, by_id = run_cli(["entity", "list", "--area", "example_room"], ws_env)
    assert "light.example_lamp" in by_name and "light.example_ceiling" not in by_name
    assert by_name.splitlines()[1:] == by_id.splitlines()[1:]


def test_entity_list_finds_entities_with_no_area(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--area", "none"], ws_env)
    assert code == 0
    assert "sensor.example_temperature" in out
    assert "light.example_lamp" not in out


def test_entity_list_rejects_an_unknown_area_with_a_way_forward(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--area", "Nowhere"], ws_env)
    assert code == 1
    assert "no area with id or name 'Nowhere'" in out
    assert "ha-axi area list" in out


def test_entity_list_filters_by_domain_and_platform_and_search(run_cli, ws_env):
    _, out = run_cli(["entity", "list", "--domain", "sensor"], ws_env)
    assert "sensor.example_temperature" in out and "light.example_lamp" not in out
    _, out = run_cli(["entity", "list", "--platform", "demo"], ws_env)
    assert "count: 3 of 3 matched (3 total)" in out
    _, out = run_cli(["entity", "list", "--search", "lamp"], ws_env)
    assert "light.example_lamp" in out and "sensor.example" not in out


def test_entity_list_states_the_zero_explicitly(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--domain", "vacuum"], ws_env)
    assert code == 0
    assert "entities: 0 registry entries found in domain vacuum" in out


def test_entity_get_shows_where_the_area_came_from(run_cli, ws_env):
    code, out = run_cli(["entity", "get", "light.example_lamp"], ws_env)
    assert code == 0
    assert "area_source: entity" in out
    code, out = run_cli(["entity", "get", "light.example_ceiling"], ws_env)
    assert "area_source: device" in out


def test_entity_get_on_a_missing_entry_suggests_a_search(run_cli, ws_env):
    code, out = run_cli(["entity", "get", "light.absent"], ws_env)
    assert code == 1
    assert "no registry entry for light.absent" in out
    assert "--search absent" in out


def test_entity_update_sets_the_name_and_the_area(run_cli, ws_env, ws_server):
    code, out = run_cli(
        [
            "entity",
            "update",
            "light.example_ceiling",
            "--name",
            "Reading Lamp",
            "--area",
            "Example Room",
        ],
        ws_env,
    )
    assert code == 0
    assert "updated[2]: area_id,name" in out
    updates = [c for c in ws_server.received if c["type"] == "config/entity_registry/update"]
    assert updates[0]["name"] == "Reading Lamp"
    assert updates[0]["area_id"] == "example_room"
    assert ws_server.entities[1]["name"] == "Reading Lamp"


def test_entity_update_reports_the_area_inherited_from_the_device(run_cli, ws_env):
    # light.example_ceiling has no area of its own; its device sits in Example
    # Hall. The update response is what an agent reads to decide whether the
    # entity still needs placing, so an empty area here reads as "unassigned"
    # and invites a helpful reassignment of an entity that was never homeless.
    code, out = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], ws_env
    )
    assert code == 0
    assert "updated[1]: name" in out
    assert "name: Reading Lamp" in out
    assert "area: Example Hall" in out
    assert "area_id: example_hall" in out
    assert "area_source: device" in out


def test_entity_update_and_entity_get_agree_about_the_area(run_cli, ws_env):
    """The two views are built from the same row, so they cannot drift apart."""
    _, updated = run_cli(
        ["entity", "update", "light.example_ceiling", "--icon", "mdi:lamp"], ws_env
    )
    _, fetched = run_cli(["entity", "get", "light.example_ceiling"], ws_env)

    def area_lines(text):
        wanted = ("area:", "area_id:", "area_source:")
        return [line.strip() for line in text.splitlines() if line.strip().startswith(wanted)]

    assert (
        area_lines(updated)
        == area_lines(fetched)
        == ["area: Example Hall", "area_id: example_hall", "area_source: device"]
    )


def test_entity_update_reports_the_inherited_area_in_json_too(run_cli, ws_env):
    import json

    code, out = run_cli(
        ["--json", "entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], ws_env
    )
    assert code == 0
    doc = json.loads(out)
    assert doc["area"] == "Example Hall"
    assert doc["area_id"] == "example_hall"


def test_a_no_op_update_reports_the_inherited_area_as_well(run_cli, ws_env, ws_server):
    # --clear-name on an entity that has no name override changes nothing, so
    # this takes the no-op branch, which made the same wrong area claim.
    code, out = run_cli(["entity", "update", "light.example_ceiling", "--clear-name"], ws_env)
    assert code == 0
    assert "no change made" in out
    assert "area: Example Hall" in out
    assert "area_id: example_hall" in out
    assert "area_source: device" in out
    assert [c for c in ws_server.received if c["type"] == "config/entity_registry/update"] == []


def test_entity_update_reports_no_area_when_there_genuinely_is_none(run_cli, ws_env):
    # sensor.example_temperature has neither an area nor a device, so an empty
    # area is the truth here rather than a lost inheritance.
    code, out = run_cli(
        ["entity", "update", "sensor.example_temperature", "--name", "Hall Sensor"], ws_env
    )
    assert code == 0
    assert 'area: ""' in out
    assert 'area_source: ""' in out


def test_the_double_answers_an_update_with_the_stored_entry_not_the_request(run_cli, ws_env):
    """A double that echoed the request could not contradict a wrong client.

    The answer carries fields the request never mentioned, and an `area_id`
    that is still null because this entity's area belongs to its device.
    """
    import json

    code, out = run_cli(
        [
            "--json",
            "ws",
            "entity.update",
            "--param",
            "entity_id=light.example_ceiling",
            "--param",
            "name=Reading Lamp",
        ],
        ws_env,
    )
    assert code == 0
    entry = json.loads(out)["result"]["entity_entry"]
    assert entry["name"] == "Reading Lamp"
    assert entry["platform"] == "demo"
    assert entry["unique_id"] == "unique-two"
    assert entry["device_id"] == "device_two"
    assert entry["area_id"] is None


def test_the_double_rejects_a_key_the_api_does_not_declare(run_cli, ws_env):
    # Home Assistant validates every command against a PREVENT_EXTRA schema.
    code, out = run_cli(
        [
            "ws",
            "--raw",
            "config/entity_registry/update",
            "--param",
            "entity_id=light.example_lamp",
            "--param",
            "nickname=Nope",
        ],
        ws_env,
    )
    assert code == 1
    assert "rejected the arguments" in out
    assert "INVALID_FORMAT" in out
    assert "nickname" in out


def test_entity_update_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(
        ["entity", "update", "light.example_lamp", "--name", "Example Lamp"], ws_env
    )
    assert code == 0
    assert "no change made" in out
    assert [c for c in ws_server.received if c["type"] == "config/entity_registry/update"] == []


def test_entity_update_can_clear_the_name_and_the_area(run_cli, ws_env, ws_server):
    code, _ = run_cli(
        ["entity", "update", "light.example_lamp", "--clear-name", "--clear-area"], ws_env
    )
    assert code == 0
    update = next(c for c in ws_server.received if c["type"] == "config/entity_registry/update")
    assert update["name"] is None and update["area_id"] is None


def test_entity_update_needs_something_to_change(run_cli, ws_env):
    code, out = run_cli(["entity", "update", "light.example_lamp"], ws_env)
    assert code == 2
    assert "nothing to update" in out


def test_entity_update_rejects_conflicting_area_flags(run_cli, ws_env):
    code, out = run_cli(
        ["entity", "update", "light.example_lamp", "--area", "example_room", "--clear-area"], ws_env
    )
    assert code == 2
    assert "mutually exclusive" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["entity", "update", "light.example_lamp", "--name", "Reading Lamp", "--clear-name"],
        ["entity", "update", "light.example_lamp", "--icon", "mdi:lamp", "--clear-icon"],
        ["area", "update", "example_room", "--icon", "mdi:sofa", "--clear-icon"],
        ["area", "update", "example_room", "--floor", "ground", "--clear-floor"],
    ],
)
def test_update_rejects_a_set_flag_paired_with_its_clear(run_cli, ws_env, argv):
    code, out = run_cli(argv, ws_env)
    assert code == 2
    assert "mutually exclusive" in out
    assert "CONFLICTING_FLAGS" in out


def test_area_list_counts_entities_including_device_inheritance(run_cli, ws_env):
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 0
    assert "areas[2]{area_id,name,entities,devices,floor_id}:" in out
    assert "example_hall,Example Hall,1,1,ground" in out
    assert "example_room,Example Room,1,1," in out


def test_area_get_accepts_a_name_as_well_as_an_id(run_cli, ws_env):
    _, by_id = run_cli(["area", "get", "example_room"], ws_env)
    _, by_name = run_cli(["area", "get", "Example Room"], ws_env)
    assert by_id == by_name
    assert "name: Example Room" in by_id


def test_area_create_makes_a_new_area(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "create", "--name", "Example Study"], ws_env)
    assert code == 0
    assert "area_id: example_study" in out
    assert any(a["name"] == "Example Study" for a in ws_server.areas)


def test_area_create_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "create", "--name", "Example Room"], ws_env)
    assert code == 0
    assert "already exists" in out
    assert [c for c in ws_server.received if c["type"] == "config/area_registry/create"] == []


def test_area_create_requires_a_name(run_cli, ws_env):
    code, out = run_cli(["area", "create"], ws_env)
    assert code == 2
    assert "--name is required" in out


def test_area_update_renames(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "update", "Example Room", "--name", "Example Study"], ws_env)
    assert code == 0
    assert "updated[1]: name" in out
    assert ws_server.areas[0]["name"] == "Example Study"


def test_area_update_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "update", "example_room", "--name", "Example Room"], ws_env)
    assert code == 0
    assert "no change made" in out
    assert [c for c in ws_server.received if c["type"] == "config/area_registry/update"] == []


def test_area_update_needs_something_to_change(run_cli, ws_env):
    code, out = run_cli(["area", "update", "example_room"], ws_env)
    assert code == 2
    assert "nothing to update" in out


def test_device_list_shows_areas_and_entity_counts(run_cli, ws_env):
    code, out = run_cli(["device", "list"], ws_env)
    assert code == 0
    assert "devices[2]{device_id,name,area}:" in out
    assert "device_two,Renamed Device,Example Hall" in out


def test_device_list_works_without_the_subcommand_name(run_cli, ws_env):
    code, out = run_cli(["device"], ws_env)
    assert code == 0
    assert "devices[2]" in out


def test_ws_list_needs_no_connection(run_cli):
    code, out = run_cli(["ws", "--list"], {})
    assert code == 0
    assert "entity.update,config/entity_registry/update" in out


def test_ws_sends_a_declared_command(run_cli, ws_env, ws_server):
    code, out = run_cli(["ws", "area.list"], ws_env)
    assert code == 0
    assert "type: config/area_registry/list" in out
    assert ws_server.received[0]["type"] == "config/area_registry/list"


def test_ws_passes_parameters_through(run_cli, ws_env, ws_server):
    code, _ = run_cli(
        ["ws", "area.update", "--param", "area_id=example_room", "--param", "name=Example Study"],
        ws_env,
    )
    assert code == 0
    assert ws_server.received[0]["name"] == "Example Study"


def test_ws_requires_declared_parameters_up_front(run_cli, ws_env, ws_server):
    code, out = run_cli(["ws", "area.update"], ws_env)
    assert code == 2
    assert "area.update needs area_id" in out
    assert ws_server.received == []


def test_ws_rejects_an_undeclared_name_and_lists_what_exists(run_cli, ws_env):
    code, out = run_cli(["ws", "nope"], ws_env)
    assert code == 2
    assert "unknown websocket command: nope" in out
    assert "entity.list" in out


def test_ws_points_a_raw_type_at_the_raw_flag(run_cli, ws_env):
    code, out = run_cli(["ws", "config/floor_registry/list"], ws_env)
    assert code == 2
    assert "--raw config/floor_registry/list" in out


def test_ws_raw_sends_an_undeclared_type(run_cli, ws_env, ws_server):
    code, _out = run_cli(["ws", "--raw", "config/floor_registry/list"], ws_env)
    assert code == 0
    assert ws_server.received[0]["type"] == "config/floor_registry/list"


def test_a_command_error_is_translated_not_leaked(run_cli, ws_env, ws_server):
    ws_server.fail_next = {"code": "invalid_format", "message": "expected str for name"}
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 1
    assert "rejected the arguments" in out
    assert "Traceback" not in out


def test_an_unauthorized_command_names_the_permission_problem(run_cli, ws_env, ws_server):
    ws_server.fail_next = {"code": "unauthorized", "message": "nope"}
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 1
    assert "not permitted" in out
    assert "administrator" in out


def test_a_rejected_token_fails_the_handshake_cleanly(run_cli, ws_server):
    env = {"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": "wrong-token-value"}
    code, out = run_cli(["entity", "list"], env)
    assert code == 1
    assert "rejected the access token" in out
    assert "wrong-token-value" not in out


def test_an_unreachable_websocket_reports_the_transport(run_cli):
    env = {"HA_URL": "http://127.0.0.1:1", "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["entity", "list"], env)
    assert code == 1
    assert "could not open a WebSocket" in out
    assert "Traceback" not in out


def test_a_socket_closed_between_commands_reports_the_transport_not_a_traceback(
    run_cli, ws_env, ws_server
):
    # The server answers entity.list and area.list, then drops the connection;
    # whatever the client does next has to fail as structured output.
    ws_server.close_after = 2
    code, out = run_cli(
        ["entity", "update", "light.example_lamp", "--name", "Renamed Lamp"], ws_env
    )
    assert code == 1
    assert "error:" in out
    assert "WebSocket connection to Home Assistant closed" in out
    assert "WS_CLOSED" in out
    assert "Traceback" not in out


def test_writing_to_a_closed_connection_is_a_structured_failure(ws_env, ws_server):
    from ha_axi.cli import Context
    from ha_axi.errors import ConnectionFailed

    ws_server.close_after = 1
    ctx = Context(ws_env)
    with ctx.ws() as client:
        assert len(client.run("entity.list")) == 3
        # Give the client's reader time to observe the reset, so the write is
        # the first operation to touch the dead socket.
        time.sleep(0.1)
        with pytest.raises(ConnectionFailed) as raised:
            client.run("area.list")
    assert raised.value.code == "WS_CLOSED"


def test_device_list_filters_by_area(run_cli, ws_env):
    code, out = run_cli(["device", "list", "--area", "Example Room"], ws_env)
    assert code == 0
    assert "device_one" in out and "device_two" not in out
    assert "count: 1 of 1 matched (2 total)" in out


def test_device_list_finds_devices_with_no_area(run_cli, ws_env, ws_server):
    ws_server.devices.append(
        {
            "id": "device_three",
            "name": "Example Device Three",
            "name_by_user": None,
            "area_id": None,
            "manufacturer": "Example Co",
            "model": "Model Z",
        }
    )
    code, out = run_cli(["device", "list", "--area", "none"], ws_env)
    assert code == 0
    assert "device_three" in out and "device_one" not in out


def test_device_list_searches_name_manufacturer_and_model(run_cli, ws_env):
    _, by_model = run_cli(["device", "list", "--search", "Model Y"], ws_env)
    assert "device_two" in by_model and "device_one" not in by_model

    _, by_maker = run_cli(["device", "list", "--search", "Example Co"], ws_env)
    assert "device_one" in by_maker and "device_two" in by_maker


def test_device_list_states_the_zero_explicitly(run_cli, ws_env):
    code, out = run_cli(["device", "list", "--search", "nothing-matches"], ws_env)
    assert code == 0
    assert "0 devices found" in out
    assert "2 devices in the device registry" in out


def test_device_list_rejects_an_unknown_area(run_cli, ws_env):
    code, out = run_cli(["device", "list", "--area", "Nowhere"], ws_env)
    assert code == 1
    assert "no area with id or name" in out
