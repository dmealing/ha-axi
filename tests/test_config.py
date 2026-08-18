"""Environment-driven configuration, and the errors when it is absent."""

from __future__ import annotations

import pytest

from conftest import FAKE_TOKEN
from ha_axi import config
from ha_axi.errors import ConfigError


def test_loads_from_the_primary_variables():
    resolved = config.load({"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    assert resolved.base_url == "https://ha.example.com"
    assert resolved.token == FAKE_TOKEN


def test_falls_back_to_the_hass_cli_variable_names():
    resolved = config.load({"HASS_SERVER": "https://ha.example.com", "HASS_TOKEN": FAKE_TOKEN})
    assert resolved.base_url == "https://ha.example.com"


def test_primary_variable_wins_over_the_fallback():
    resolved = config.load(
        {
            "HA_URL": "https://primary.example.com",
            "HASS_SERVER": "https://fallback.example.com",
            "HA_TOKEN": FAKE_TOKEN,
        }
    )
    assert resolved.base_url == "https://primary.example.com"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "HA_URL and HA_TOKEN"),
        ({"HA_URL": "https://ha.example.com"}, "HA_TOKEN"),
        ({"HA_TOKEN": FAKE_TOKEN}, "HA_URL"),
        ({"HA_URL": "  ", "HA_TOKEN": FAKE_TOKEN}, "HA_URL"),
    ],
)
def test_missing_configuration_names_what_is_absent(environ, expected):
    with pytest.raises(ConfigError) as caught:
        config.load(environ)
    assert expected in caught.value.message
    assert caught.value.help_lines


def test_base_url_normalization_adds_a_scheme_and_trims_noise():
    assert config.normalize_base_url("ha.example.com") == "http://ha.example.com"
    assert config.normalize_base_url("https://ha.example.com/") == "https://ha.example.com"
    assert config.normalize_base_url("https://ha.example.com/api") == "https://ha.example.com"


def test_base_url_rejects_an_unusable_scheme():
    with pytest.raises(ConfigError, match="http or https"):
        config.normalize_base_url("ftp://ha.example.com")


def test_websocket_url_follows_the_scheme():
    plain = config.load({"HA_URL": "http://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    secure = config.load({"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    assert plain.ws_url == "ws://ha.example.com/api/websocket"
    assert secure.ws_url == "wss://ha.example.com/api/websocket"


def test_websocket_url_keeps_a_subpath():
    resolved = config.load({"HA_URL": "https://proxy.example.com/ha", "HA_TOKEN": FAKE_TOKEN})
    assert resolved.ws_url == "wss://proxy.example.com/ha/api/websocket"


def test_describe_environment_never_reveals_the_token():
    described = config.describe_environment(
        {"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN}
    )
    assert described == {
        "url_var": "HA_URL",
        "url_set": True,
        "token_var": "HA_TOKEN",
        "token_set": True,
    }
    assert FAKE_TOKEN not in repr(described)
