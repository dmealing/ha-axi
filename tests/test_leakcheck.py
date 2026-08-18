"""The public-repository guard.

The scanner is the control that keeps installation-specific data out of this
repository, so it is tested in both directions: it must fail on dirty content,
and it must pass on this tree. Nothing dirty is ever committed -- the dirty
fixtures are assembled at run time from fragments and written to a temp path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import leakcheck

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("rule", "content"),
    [
        (
            "jwt",
            "token: "
            + "eyJ"
            + "hbGciOiJIUzI1NiJ9"
            + "."
            + "eyJzdWIiOiJhIn0"
            + "."
            + "c2lnbmF0dXJl",
        ),
        ("private-ip", "host " + "192." + "168." + "1.10"),
        ("private-ip", "host " + "10." + "0.0.4"),
        ("private-ip", "host " + "172." + "20.3.9"),
        ("home-path", "path " + "/ho" + "me/" + "someone" + "/notes"),
        ("home-path", "path " + "/Us" + "ers/" + "someone" + "/notes"),
        ("personal-email", "mail " + "firstname.lastname" + "@" + "realcompany.co.uk"),
        ("bearer", "header " + "Bearer " + "aa11bb22cc33dd44ee55"),
    ],
)
def test_every_shape_is_detected(rule, content):
    findings = leakcheck.scan_text("f.txt", content + "\n")
    assert [f.rule.name for f in findings] == [rule]


@pytest.mark.parametrize(
    "content",
    [
        "host 127.0.0.1:8123",
        "host 8.8.8.8",
        "host 172.15.0.1",
        "host 172.32.0.1",
        "url https://homeassistant.example.com",
        "entity light.example_lamp in area Example Room",
        "mail noreply@anything.example",
        "mail you@example.com",
        "curl -H 'Authorization: Bearer <token>'",
        'curl -H "Authorization: Bearer $HA_TOKEN"',
        "a bearer of good news arrives",
        "relative path src/ha_axi/cli.py",
    ],
)
def test_legitimate_content_does_not_trip_the_scanner(content):
    assert leakcheck.scan_text("f.txt", content + "\n") == []


def test_the_allow_marker_exempts_a_line():
    dirty = "host " + "192." + "168." + "1.10"
    assert leakcheck.scan_text("f.txt", dirty + "\n")
    assert leakcheck.scan_text("f.txt", f"{dirty}  # {leakcheck.ALLOW_MARKER}\n") == []


def test_scanning_a_dirty_tree_exits_non_zero(tmp_path, capsys):
    for name, content in leakcheck.DIRTY_FIXTURE.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    exit_code = leakcheck.main(["--root", str(tmp_path), *sorted(leakcheck.DIRTY_FIXTURE)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "leakcheck[5]" in out
    assert "help:" in out


def test_the_self_test_confirms_every_rule_still_fires(capsys):
    assert leakcheck.main(["--demo"]) == 0
    assert "every rule fired" in capsys.readouterr().out


def test_this_repository_is_clean(capsys):
    assert leakcheck.main(["--root", str(REPO_ROOT)]) == 0
    assert "0 findings" in capsys.readouterr().out


def test_binary_and_vendored_paths_are_skipped(tmp_path):
    dirty = ("192." + "168." + "1.1").encode()
    (tmp_path / "image.png").write_bytes(b"\x89PNG" + dirty)
    assert leakcheck.scan_paths(["image.png"], root=tmp_path) == []


def test_the_pre_commit_hook_blocks_a_dirty_commit(tmp_path):
    """End-to-end: the hook git actually runs must reject the commit."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".githooks").mkdir()
    (repo / "scripts" / "leakcheck.py").write_text(
        (REPO_ROOT / "scripts" / "leakcheck.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    hook = repo / ".githooks" / "pre-commit"
    hook.write_text(
        (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8"), encoding="utf-8"
    )
    hook.chmod(0o755)

    def git(*args, check=True):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=check)

    git("init", "-q", ".")
    git("config", "user.email", "you@example.com")
    git("config", "user.name", "Test")
    git("config", "core.hooksPath", ".githooks")

    (repo / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    git("add", "-A")
    assert git("commit", "-m", "clean").returncode == 0

    (repo / "dirty.txt").write_text("host " + "192." + "168." + "1.10" + "\n", encoding="utf-8")
    git("add", "dirty.txt")
    blocked = git("commit", "-m", "dirty", check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr


def test_the_hook_scans_the_first_commit_too(tmp_path):
    """A repository with no HEAD yet still gets scanned."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "you@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "first.txt").write_text("host " + "10." + "1.2.3" + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    findings, scanned = leakcheck.scan_staged(str(repo))
    assert scanned == 1
    assert [f.rule.name for f in findings] == ["private-ip"]
