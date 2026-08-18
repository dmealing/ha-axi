"""Session-hook installation: idempotent, repairing, and never destructive."""

from __future__ import annotations

import json

from ha_axi import hooks


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_creates_hooks_for_every_default_target(tmp_path):
    report = hooks.install(tmp_path, command="ha-axi")
    assert report["errors"] == []
    assert {t["target"] for t in report["targets"]} == {
        "claude-code",
        "codex",
        "codex-features",
        "opencode",
    }
    assert all(t["status"] == "installed" for t in report["targets"])

    claude = read(tmp_path / ".claude" / "settings.json")
    hook = claude["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook == {"type": "command", "command": "ha-axi", "timeout": 10}
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert "hooks = true" in (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert (tmp_path / ".config" / "opencode" / "plugins" / "axi-ha-axi.js").exists()


def test_repeat_installs_with_the_same_path_are_no_ops(tmp_path):
    hooks.install(tmp_path, command="ha-axi")
    second = hooks.install(tmp_path, command="ha-axi")
    assert all(t["status"] == "current" for t in second["targets"])


def test_a_changed_executable_path_is_repaired_not_duplicated(tmp_path):
    hooks.install(tmp_path, command="/old/path/ha-axi")
    hooks.install(tmp_path, command="/new/path/ha-axi")
    groups = read(tmp_path / ".claude" / "settings.json")["hooks"]["SessionStart"]
    commands = [h["command"] for group in groups for h in group["hooks"]]
    assert commands == ["/new/path/ha-axi"]


def test_other_tools_hooks_are_left_alone(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
                    ]
                },
                "unrelated": True,
            }
        ),
        encoding="utf-8",
    )
    hooks.install(tmp_path, command="ha-axi")
    data = read(settings)
    commands = [h["command"] for group in data["hooks"]["SessionStart"] for h in group["hooks"]]
    assert "other-tool" in commands and "ha-axi" in commands
    assert data["unrelated"] is True


def test_a_legacy_lowercase_hook_entry_is_cleaned_up(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"hooks": {"session_start": [{"type": "command", "command": "ha-axi"}]}}),
        encoding="utf-8",
    )
    hooks.install(tmp_path, command="ha-axi")
    data = read(settings)
    assert "session_start" not in data["hooks"]
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "ha-axi"


def test_an_unmanaged_opencode_plugin_is_never_overwritten(tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-ha-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// hand written\n", encoding="utf-8")
    report = hooks.install(tmp_path, command="ha-axi")
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


def test_codex_features_flag_is_added_without_disturbing_other_sections():
    content = '[model]\nname = "example"\n'
    updated, changed = hooks.compute_codex_config_update(content)
    assert changed
    assert "[model]" in updated and 'name = "example"' in updated
    assert "[features]" in updated and "hooks = true" in updated


def test_codex_features_flag_already_true_is_a_no_op():
    content = "[features]\nhooks = true\n"
    assert hooks.compute_codex_config_update(content) == (content, False)


def test_codex_features_flag_set_to_false_is_flipped():
    updated, changed = hooks.compute_codex_config_update("[features]\nhooks = false\n")
    assert changed and "hooks = true" in updated


def test_codex_features_is_inserted_into_an_existing_features_table():
    updated, changed = hooks.compute_codex_config_update(
        "[features]\nother = 1\n\n[model]\nx = 2\n"
    )
    assert changed
    assert updated.index("hooks = true") < updated.index("[model]")


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
    source = hooks.opencode_plugin_source("ha-axi", 10)
    assert hooks.OPENCODE_MANAGED_PREFIX in source
    assert '"ha-axi"' in source
