"""The public-repository guard.

The scanner is the control that keeps installation-specific data out of this
repository, so it is tested adversarially: every rule against the shape it
claims, every rule against content that must NOT trip it, and the evasions that
a line-by-line scanner misses.

Nothing dirty is ever committed. Address shapes are assembled from fragments at
run time (the condensed pass deliberately does not join digits, so a fragmented
address is invisible to it), and JWTs are built by actually base64-encoding a
payload, so no `eyJ` literal exists in this file at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import leakcheck

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN = leakcheck.synthetic_jwt()
HEAD, PAYLOAD, SIGNATURE = TOKEN.split(".")


#: One realistic leak per rule. The name is the rule that must fire.
DIRTY = {
    "jwt": f"token: {TOKEN}",
    "bearer": "header " + "Bearer " + "aa11bb22cc33dd44ee55",
    "private-ip": "host " + "192." + "168.1.10",
    "cgnat-ip": "peer " + "100." + "101.102.103",
    "link-local-ip": "addr " + "169." + "254.10.20",
    "private-ipv6": "addr " + "fd12" + ":3456:789a::1",
    "remote-ui-host": "url https://" + "a1b2c3d4e5f6a7b8" + ".ui.nabu.casa",
    "lan-hostname": "url http://" + "homeassistant" + ".local:8123",
    "coordinates": "latitude: " + "51.5" + "0736",
    "mac-address": "mac " + "a4:c1" + ":38:9f:2b:7e",
    "home-path": "path " + "/ho" + "me/" + "someone" + "/notes",
    "personal-email": "mail " + "noreply" + "@" + "realcompany.co.uk",
}

#: Content this project legitimately contains, or that reads like a leak but is
#: not. A guard that fires on these gets ignored, which is its own failure mode.
CLEAN = [
    "host 127.0.0.1:8123",
    "bind 0.0.0.0 for the test double",
    "host 8.8.8.8",
    "host 172.15.0.1",
    "host 172.32.0.1",
    "host 100.63.255.1",
    "host 100.128.0.1",
    "url https://homeassistant.example.com",
    "entity light.example_lamp in area Example Room",
    "mail you@example.com",
    "mail noreply@anything.example",
    "curl -H 'Authorization: Bearer <token>'",
    'curl -H "Authorization: Bearer $HA_TOKEN"',
    "a bearer of good news arrives",
    "relative path src/ha_axi/cli.py",
    "version 2026.1.0 released",
    "python 3.9, 3.10, 3.12 supported",
    "line-length = 100",
    "timeout 30.5 seconds",
    "coverage rose from 51.5 to 90.2 percent",
    "the git empty tree is 4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "ff02::1 is the all-nodes multicast group",
    "fdfd is hex, not an address",
    "settings.local.json is ignored",
    "Path.home() resolves the home directory",
    "from .commands.home import DESCRIPTION",
    "a timestamp like 12:34:56 is not a MAC",
]


def rule_names(findings):
    return sorted({finding.rule.name for finding in findings})


@pytest.mark.parametrize(("rule", "content"), sorted(DIRTY.items()))
def test_every_rule_detects_its_shape(rule, content):
    assert rule in rule_names(leakcheck.scan_text("f.txt", content + "\n"))


def test_every_declared_rule_is_covered_by_this_suite():
    """A rule added without a test would otherwise look proven."""
    assert sorted(DIRTY) == sorted(rule.name for rule in leakcheck.RULES)


@pytest.mark.parametrize("content", CLEAN)
def test_legitimate_content_does_not_trip_the_scanner(content):
    assert leakcheck.scan_text("f.txt", content + "\n") == []


# --------------------------------------------------------------- evasions


def test_a_token_split_across_lines_is_caught():
    """A line pass cannot see this; the condensed pass is why it exists."""
    source = f'TOKEN = (\n    "{HEAD}."\n    "{PAYLOAD}."\n    "{SIGNATURE}"\n)\n'
    findings = leakcheck.scan_text("f.py", source)
    assert "jwt" in rule_names(findings)
    assert any(finding.pass_name == "joined" for finding in findings)


def test_a_token_joined_by_concatenation_is_caught():
    source = f'T = "{HEAD}" + "." + "{PAYLOAD}" + "." + "{SIGNATURE}"\n'
    assert "jwt" in rule_names(leakcheck.scan_text("f.py", source))


def test_a_token_continued_over_a_backslash_is_caught():
    source = f"token: {HEAD}.{PAYLOAD}.\\\n{SIGNATURE}\n"
    assert "jwt" in rule_names(leakcheck.scan_text("f.txt", source))


def test_a_percent_encoded_token_is_caught():
    source = f"u={HEAD}%2E{PAYLOAD}%2E{SIGNATURE}\n"
    assert "jwt" in rule_names(leakcheck.scan_text("f.txt", source))


def test_a_finding_is_reported_once_even_when_both_passes_see_it():
    findings = leakcheck.scan_text("f.txt", f"token: {TOKEN}\n")
    assert len(findings) == 1


# ----------------------------------------------------------- allow marker


def test_the_allow_marker_exempts_only_the_rule_it_names():
    dirty = "host " + "192." + "168.1.10" + " and " + "100." + "101.102.103"
    assert rule_names(leakcheck.scan_text("f.txt", dirty + "\n")) == ["cgnat-ip", "private-ip"]

    scoped = f"{dirty}  # {leakcheck.ALLOW_PREFIX}private-ip\n"
    assert rule_names(leakcheck.scan_text("f.txt", scoped)) == ["cgnat-ip"]


def test_the_allow_marker_accepts_several_named_rules():
    dirty = "host " + "192." + "168.1.10" + " and " + "100." + "101.102.103"
    scoped = f"{dirty}  # {leakcheck.ALLOW_PREFIX}private-ip,cgnat-ip\n"
    assert leakcheck.scan_text("f.txt", scoped) == []


def test_an_unscoped_marker_exempts_nothing():
    """A blanket marker would switch off rules nobody was thinking about."""
    dirty = "host " + "192." + "168.1.10" + "  # leakcheck: allow\n"
    assert rule_names(leakcheck.scan_text("f.txt", dirty)) == ["private-ip"]


def test_the_allow_marker_also_applies_to_the_condensed_pass():
    source = f"token: {TOKEN}  # {leakcheck.ALLOW_PREFIX}jwt\n"
    assert leakcheck.scan_text("f.txt", source) == []


# -------------------------------------------------------- path allowances
#
# The marker is a comment, so a JSON file cannot carry one. PATH_ALLOWANCES is
# how vendored data that must stay byte-for-byte is exempted instead, and these
# tests hold it to the same scope the marker has.


ALLOWED_PATH = "tests/fixtures/toon-spec/encode/primitives.json"
TWO_SHAPES = "path " + "/ho" + "me/" + "someone" + "/notes and " + "192." + "168.1.10"


def test_a_path_allowance_exempts_only_the_rule_it_names():
    assert rule_names(leakcheck.scan_text(ALLOWED_PATH, TWO_SHAPES + "\n")) == ["private-ip"]


def test_a_path_allowance_exempts_no_other_file():
    findings = leakcheck.scan_text("src/ha_axi/toon.py", TWO_SHAPES + "\n")
    assert rule_names(findings) == ["home-path", "private-ip"]


def test_a_path_allowance_holds_when_the_scan_is_rooted_outside_the_repository():
    assert leakcheck.path_allowances(f"/scan/root/{ALLOWED_PATH}") == frozenset({"home-path"})
    assert leakcheck.path_allowances(f"decoy/{ALLOWED_PATH}.bak") == frozenset()


@pytest.mark.parametrize("path", sorted(leakcheck.PATH_ALLOWANCES))
def test_every_path_allowance_is_still_earning_its_place(path, monkeypatch):
    """Stale is as bad as blanket: an exemption outlives what it was granted for.

    Scanning the real file with allowances switched off must fire exactly the
    rules the entry names -- no fewer, or the entry is dead and should go; no
    more, or it is covering something nobody agreed to.
    """
    allowed = leakcheck.PATH_ALLOWANCES[path]
    assert allowed <= set(leakcheck.RULES_BY_NAME), "allowance names an undeclared rule"
    target = REPO_ROOT / path
    assert target.is_file(), "allowance names a file that is no longer here"

    monkeypatch.setattr(leakcheck, "PATH_ALLOWANCES", {})
    findings = leakcheck.scan_text(path, target.read_text(encoding="utf-8"))
    assert rule_names(findings) == sorted(allowed)


# ------------------------------------------------------------------ email


def test_an_allowlisted_local_part_no_longer_bypasses_the_domain_check():
    """`noreply@` on a real domain still identifies a real organisation."""
    dirty = "mail " + "noreply" + "@" + "realcompany.co.uk"
    assert rule_names(leakcheck.scan_text("f.txt", dirty + "\n")) == ["personal-email"]


# ------------------------------------------------------------- interfaces


def test_scanning_a_dirty_tree_exits_non_zero(tmp_path, capsys):
    fixture = leakcheck.dirty_fixture()
    for name, content in fixture.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    exit_code = leakcheck.main(["--root", str(tmp_path), *sorted(fixture)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "help:" in out


def test_the_self_test_confirms_every_rule_still_fires(capsys):
    assert leakcheck.main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "every rule fired" in out
    for rule in leakcheck.RULES:
        assert rule.name in out


def test_the_rules_listing_names_every_rule(capsys):
    assert leakcheck.main(["--rules"]) == 0
    out = capsys.readouterr().out
    for rule in leakcheck.RULES:
        assert rule.name in out


def test_this_repository_is_clean(capsys):
    assert leakcheck.main(["--root", str(REPO_ROOT)]) == 0
    assert "0 findings" in capsys.readouterr().out


def test_binary_files_are_skipped(tmp_path):
    dirty = ("192." + "168.1.1").encode()
    (tmp_path / "image.png").write_bytes(b"\x89PNG" + dirty)
    assert leakcheck.scan_paths(["image.png"], root=tmp_path) == []


def test_a_commit_message_is_scanned(tmp_path, capsys):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("fix: tested against " + "192." + "168.1.10" + "\n", encoding="utf-8")
    assert leakcheck.main(["--commit-msg", str(message)]) == 1
    assert "private-ip" in capsys.readouterr().out


def test_commit_message_comment_lines_are_ignored(tmp_path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("fix: something\n# host " + "192." + "168.1.10" + "\n", encoding="utf-8")
    assert leakcheck.main(["--commit-msg", str(message)]) == 0


# ------------------------------------------------------------------ hooks


def _hook_repo(tmp_path, *hooks):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".githooks").mkdir()
    (repo / "scripts" / "leakcheck.py").write_text(
        (REPO_ROOT / "scripts" / "leakcheck.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name in hooks:
        target = repo / ".githooks" / name
        target.write_text(
            (REPO_ROOT / ".githooks" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
        target.chmod(0o755)
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "you@example.com"),
        ("config", "user.name", "Test"),
        ("config", "core.hooksPath", ".githooks"),
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return repo


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=check)


def test_the_pre_commit_hook_blocks_a_dirty_commit(tmp_path):
    repo = _hook_repo(tmp_path, "pre-commit")
    (repo / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-m", "clean").returncode == 0

    (repo / "dirty.txt").write_text("host " + "192." + "168.1.10" + "\n", encoding="utf-8")
    _git(repo, "add", "dirty.txt")
    blocked = _git(repo, "commit", "-m", "dirty", check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr


def test_the_pre_commit_hook_scans_the_very_first_commit(tmp_path):
    """A repository with no HEAD yet has nothing to diff against."""
    repo = _hook_repo(tmp_path, "pre-commit")
    (repo / "first.txt").write_text("host " + "10." + "1.2.3" + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    blocked = _git(repo, "commit", "-m", "first", check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr


def test_the_commit_msg_hook_blocks_a_dirty_message(tmp_path):
    """File content and the commit message are separate channels."""
    repo = _hook_repo(tmp_path, "pre-commit", "commit-msg")
    (repo / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    _git(repo, "add", "-A")
    blocked = _git(repo, "commit", "-m", "tested on " + "192." + "168.1.10", check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr

    assert _git(repo, "commit", "-m", "tested on a local instance").returncode == 0


def test_identity_trailers_do_not_block_a_commit(tmp_path):
    """Git records author and committer addresses anyway; blocking these
    would stop ordinary attribution without preventing any leak."""
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "fix: something\n\nCo-Authored-By: Someone <someone@example.org>\n"
        "Signed-off-by: Other <" + "other" + "@" + "realcompany.co.uk" + ">\n",
        encoding="utf-8",
    )
    assert leakcheck.main(["--commit-msg", str(message)]) == 0


def test_an_address_in_the_message_body_is_still_caught(tmp_path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "fix: reported by " + "firstname.lastname" + "@" + "realcompany.co.uk" + "\n",
        encoding="utf-8",
    )
    assert leakcheck.main(["--commit-msg", str(message)]) == 1


def test_a_trailer_is_only_exempt_from_the_email_rule(tmp_path):
    """The exemption is about attribution, not a way to smuggle anything else."""
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "fix: x\n\nCo-Authored-By: host " + "192." + "168.1.10" + "\n", encoding="utf-8"
    )
    assert leakcheck.main(["--commit-msg", str(message)]) == 1
