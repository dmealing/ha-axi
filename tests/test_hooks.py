"""Session-hook installation: idempotent, repairing, and never destructive.

Three of the assertions below are the sibling AXI CLI's, arrived at by a review
of the design this module handed it, and are stated here in the same order: a
hook this tool did not write is never claimed, a second managed entry is
collapsed rather than ending the scan, and the Codex features flag is rewritten
rather than duplicated into a file its own parser would refuse.
"""

from __future__ import annotations

import json
import sys

from ha_axi import hooks

EXECUTABLE = "ha-axi"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def commands_in(settings) -> list:
    return [
        hook["command"] for group in settings["hooks"]["SessionStart"] for hook in group["hooks"]
    ]


def write_settings(tmp_path, document):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(document), encoding="utf-8")
    return settings


# ------------------------------------------------------------- installation


def test_install_creates_hooks_for_every_default_target(tmp_path):
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert report["errors"] == []
    assert {t["target"] for t in report["targets"]} == {
        "claude-code",
        "codex",
        "codex-features",
        "opencode",
    }
    assert all(t["status"] == "installed" for t in report["targets"])

    claude = read(tmp_path / ".claude" / "settings.json")
    assert claude["hooks"]["SessionStart"][0]["hooks"][0] == {
        "type": "command",
        "command": EXECUTABLE,
        "timeout": hooks.DEFAULT_TIMEOUT_SECONDS,
        "managed_by": "ha-axi",
    }
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert "hooks = true" in (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert (tmp_path / ".config" / "opencode" / "plugins" / "axi-ha-axi.js").exists()


def test_repeat_installs_with_the_same_path_are_no_ops(tmp_path):
    hooks.install(tmp_path, command=EXECUTABLE)
    second = hooks.install(tmp_path, command=EXECUTABLE)
    assert all(t["status"] == "current" for t in second["targets"])


def test_a_changed_executable_path_is_repaired_not_duplicated(tmp_path):
    hooks.install(tmp_path, command="/old/bin/ha-axi")
    hooks.install(tmp_path, command="/new/bin/ha-axi")
    assert commands_in(read(tmp_path / ".claude" / "settings.json")) == ["/new/bin/ha-axi"]


def test_a_user_hook_that_names_this_tool_is_left_alone(tmp_path):
    """Ownership is decided by the marker key, never by a substring of the command.

    This tool takes its configuration from the environment, so an env-prefixed
    wrapper is a hook its users actually write; claiming it rewrote their
    wrapper out of their own global settings and reported the target installed.
    """
    wrapper = "env HA_URL=https://homeassistant.example.com ha-axi"
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": wrapper}]}
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == [wrapper, EXECUTABLE]


def test_a_shell_wrapper_and_another_interpreter_are_left_alone_too(tmp_path):
    """The wrapper above is not the only shape: what they share is not being our entry."""
    others = ["~/bin/ha-axi-wrapper.sh", "python -m ha_axi", "/usr/bin/env ha-axi"]
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": one} for one in others],
                    }
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == [*others, EXECUTABLE]


def test_a_hook_written_before_the_marker_existed_is_adopted_not_duplicated(tmp_path):
    """The one divergence from the sibling, and the reason is that this tool shipped first.

    Every release up to 0.5.1 wrote an unmarked entry, so a marker-only test of
    ownership would append a second hook beside it on every machine that had
    already followed the README. An entry in the exact shape those releases could
    produce -- the executable and nothing else -- is adopted once and carries the
    marker from then on.
    """
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "/old/bin/ha-axi", "timeout": 10}],
                    }
                ]
            }
        },
    )
    report = hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert commands_in(data) == [EXECUTABLE]
    entry = data["hooks"]["SessionStart"][0]["hooks"][0]
    assert entry[hooks.MANAGED_KEY] == hooks.MARKER
    assert next(t for t in report["targets"] if t["target"] == "claude-code")["status"] == (
        "installed"
    )
    # Adopted once: the marker it gained is what matches it on the next install.
    again = hooks.install(tmp_path, command=EXECUTABLE)
    assert all(t["status"] == "current" for t in again["targets"])


def test_a_second_stale_managed_entry_gives_way_not_reported_current(tmp_path):
    """A restored backup, a hand repair or a partial install can leave two of ours.

    The scan used to stop at the first, so an already-correct entry ended it with
    nothing changed and the second was never repaired -- while every later
    install reported the target ``current`` with a dead path still in the file.
    """
    managed = {
        "type": "command",
        "command": EXECUTABLE,
        "timeout": hooks.DEFAULT_TIMEOUT_SECONDS,
        "managed_by": "ha-axi",
    }
    stale = {**managed, "command": "/old/bin/ha-axi"}
    settings = write_settings(
        tmp_path, {"hooks": {"SessionStart": [{"matcher": "", "hooks": [managed, stale]}]}}
    )
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert next(t for t in report["targets"] if t["target"] == "claude-code")["status"] == (
        "installed"
    )
    assert commands_in(read(settings)) == [EXECUTABLE]


def test_a_stale_entry_in_a_later_group_is_repaired_too(tmp_path):
    """Groups are scanned to the end, not only up to the one holding a managed entry."""
    managed = {
        "type": "command",
        "command": EXECUTABLE,
        "timeout": hooks.DEFAULT_TIMEOUT_SECONDS,
        "managed_by": "ha-axi",
    }
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [managed]},
                    {"matcher": "startup", "hooks": [{**managed, "command": "/old/bin/ha-axi"}]},
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == [EXECUTABLE]


def test_other_tools_hooks_are_left_alone(tmp_path):
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
                ]
            },
            "unrelated": True,
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert commands_in(data) == ["other-tool", EXECUTABLE]
    assert data["unrelated"] is True


def test_a_legacy_lowercase_hook_entry_is_cleaned_up(tmp_path):
    """The cleanup recognizes the entries this tool wrote, and only those.

    An entry under the old key that merely names this tool is a user's on that
    key as on any other, so the same wrapper survives here too.
    """
    wrapper = "env HA_URL=https://homeassistant.example.com ha-axi"
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "session_start": [
                    {"type": "command", "command": EXECUTABLE},
                    {"type": "command", "command": wrapper},
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert [hook["command"] for hook in data["hooks"]["session_start"]] == [wrapper]
    assert commands_in(data) == [EXECUTABLE]


def test_an_unmanaged_opencode_plugin_is_never_overwritten(tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-ha-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// hand written\n", encoding="utf-8")
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert plugin.read_text(encoding="utf-8") == "// hand written\n"
    assert any("refusing to overwrite" in error for error in report["errors"])


def test_setup_hooks_reports_failures_with_a_non_zero_exit(run_cli, tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-ha-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// hand written\n", encoding="utf-8")
    code, out = run_cli(["setup", "hooks", "--home", str(tmp_path)], {})
    assert code == 1
    assert "refusing to overwrite" in out


def test_setup_hooks_succeeds_and_says_what_to_do_next(run_cli, tmp_path):
    code, out = run_cli(["setup", "hooks", "--home", str(tmp_path)], {})
    assert code == 0
    assert "claude-code,installed" in out
    assert "Restart your agent session" in out


# ------------------------------------------------------- the Codex features flag


def _assert_features_enabled(content: str) -> None:
    """Assert the flag landed where Codex reads it, and that the file still parses.

    ``tomllib`` arrives with Python 3.11 and this project's floor is 3.9, so on
    the older legs the same claim is pinned on the shape that carries it: exactly
    one ``hooks`` key, set to the bare boolean. A second one is a duplicate key,
    which is what a TOML parser refuses.
    """
    if sys.version_info >= (3, 11):
        import tomllib

        assert tomllib.loads(content)["features"] == {"hooks": True}
    else:
        assert content.count("hooks =") == 1
        assert "hooks = true" in content


def test_codex_features_flag_is_added_without_disturbing_other_sections():
    updated, changed, problem = hooks.compute_codex_config_update('[model]\nname = "example"\n')
    assert changed and problem is None
    assert "[model]" in updated and 'name = "example"' in updated
    assert "[features]" in updated and "hooks = true" in updated


def test_codex_features_flag_already_true_is_a_no_op():
    content = "[features]\nhooks = true\n"
    assert hooks.compute_codex_config_update(content) == (content, False, None)


def test_codex_features_flag_set_to_false_is_flipped():
    updated, changed, problem = hooks.compute_codex_config_update("[features]\nhooks = false\n")
    assert changed and problem is None
    _assert_features_enabled(updated)


def test_codex_features_is_inserted_into_an_existing_features_table():
    updated, changed, problem = hooks.compute_codex_config_update(
        "[features]\nother = 1\n\n[model]\nx = 2\n"
    )
    assert changed and problem is None
    assert updated.index("hooks = true") < updated.index("[model]")


def test_a_features_flag_that_is_not_a_bare_boolean_is_rewritten_not_duplicated():
    """`hooks = "true"` and `hooks = 1` are not the bare boolean the flag needs.

    Recognizing only ``true``/``false`` let every other value fall through to the
    append at the end, which wrote a *second* ``hooks`` key into the same table.
    TOML rejects a duplicate key outright, so the tool broke the config it was
    configuring while exiting 0 and reporting the target ``installed``.
    """
    for value in ('"true"', "1", "0", '"yes"'):
        updated, changed, problem = hooks.compute_codex_config_update(
            f'[features]\nhooks = {value}\n\n[model]\nname = "example"\n'
        )
        assert changed and problem is None, value
        _assert_features_enabled(updated)
        assert updated.index("hooks = true") < updated.index("[model]"), value


def test_an_array_of_features_tables_is_refused_rather_than_corrupted(tmp_path):
    """`[[features]]` is not the features table, and nothing written beside it works.

    A key inside an array element enables nothing, and a `[features]` table
    appended beside the array is a declaration TOML refuses -- so the honest
    outcome is a refusal that leaves the file byte-identical.
    """
    content = '[[features]]\nname = "example"\n\n[model]\nname = "example"\n'
    updated, changed, problem = hooks.compute_codex_config_update(content)
    assert not changed and updated == content and problem is not None

    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")
    report = hooks.install(tmp_path, command=EXECUTABLE)
    target = next(t for t in report["targets"] if t["target"] == "codex-features")
    assert target["status"] == "skipped"
    assert any("array of tables" in error for error in report["errors"])
    assert config.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------- the command


def test_portable_command_prefers_a_path_entry_resolving_to_this_executable(tmp_path):
    binary = tmp_path / "ha-axi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    assert hooks.portable_command(str(binary), [str(tmp_path)]) == "ha-axi"


def test_portable_command_falls_back_to_the_absolute_path(tmp_path):
    binary = tmp_path / "ha-axi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    assert hooks.portable_command(str(binary), []) == str(binary)


def test_the_opencode_plugin_carries_a_managed_marker():
    source = hooks.opencode_plugin_source(EXECUTABLE, hooks.DEFAULT_TIMEOUT_SECONDS)
    assert hooks.OPENCODE_MANAGED_PREFIX in source
    assert '"ha-axi"' in source


def test_the_installed_hook_is_useful_and_leaks_nothing_with_no_credentials(run_cli, tmp_path):
    """A hook runs at the start of every session, including where nothing is configured.

    The command recorded is the no-argument home view, so what an unconfigured
    machine gets is that view's ``NOT_CONFIGURED`` document: the two variables
    named, no value of either, and nothing on stderr, which is the channel
    redaction has to hold on just as hard.

    The exit code is 1 and is pinned here rather than passed over. It is what the
    error taxonomy gives every ``config`` fault, and the sibling reached exit 0 by
    giving its hook a separate non-connecting subcommand -- a command-surface
    change, and a decision of its own rather than part of this fix.
    """
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert report["command"] == EXECUTABLE

    code, out = run_cli([], {})
    assert code == 1
    assert "HA_URL" in out and "HA_TOKEN" in out
    assert "NOT_CONFIGURED" in out
    assert "Run `ha-axi doctor`" in out
