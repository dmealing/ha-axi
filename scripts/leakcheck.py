#!/usr/bin/env python3
"""Block installation-specific data from entering this public repository.

ha-axi talks to home automation installations, so the failure mode that matters
is not a bug -- it is a commit that quietly describes, or grants access to, the
installation it was developed against. A rule a human has to remember is not a
control, so this scanner runs from a pre-commit hook and from CI.

It looks for shapes rather than a denylist of known-bad strings, because the
strings that matter are the ones nobody thought to list:

  jwt            a Home Assistant long-lived access token
  private-ip     an RFC1918 LAN address, which names someone's network
  home-path      an absolute home directory, which names a person and a machine
  personal-email an address outside the reserved documentation domains
  bearer         a literal Authorization header carrying a real-looking value

Usage:
  scripts/leakcheck.py                 scan every tracked file
  scripts/leakcheck.py --staged        scan the staged content of a commit
  scripts/leakcheck.py PATH [PATH...]  scan explicit files or directories
  scripts/leakcheck.py --demo          scan a synthetic dirty tree and expect failure

A line carrying the marker `leakcheck: allow` is exempt. Use it only where the
pattern is the subject, such as the pattern table below.

Standard library only, so the hook runs without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ALLOW_MARKER = "leakcheck: allow"

#: Documentation-reserved domains (RFC 2606, RFC 6761) plus the placeholder
#: forms that carry no personal information.
_ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "invalid",
    "test",
    "localhost",
)
_ALLOWED_EMAIL_LOCALS = ("noreply", "no-reply", "you", "user", "someone", "me")


class Rule:
    def __init__(self, name, pattern, message, allow=None):
        self.name = name
        self.pattern = re.compile(pattern)
        self.message = message
        self.allow = allow or (lambda match: False)

    def scan(self, line):
        for match in self.pattern.finditer(line):
            if not self.allow(match):
                yield match


def _email_allowed(match):
    local, domain = match.group(1).lower(), match.group(2).lower()
    if local in _ALLOWED_EMAIL_LOCALS:
        return True
    return any(
        domain == allowed or domain.endswith("." + allowed) for allowed in _ALLOWED_EMAIL_DOMAINS
    )


def _bearer_allowed(match):
    """Keep the bearer rule off prose and placeholders.

    The pattern already restricts the value to the base64/hex alphabet, which
    excludes `Bearer <token>` and `Bearer $HA_TOKEN`. What is left to exclude is
    English prose, so a real value must also carry at least one digit, which
    randomly encoded bytes effectively always do.
    """
    value = match.group(1)
    return not any(character.isdigit() for character in value)


RULES = [
    Rule(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}",
        "a JSON Web Token, which is what a Home Assistant long-lived access token looks like",
    ),
    Rule(
        "private-ip",
        r"\b(?:192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
        "an RFC1918 private address, which describes somebody's network",
    ),
    Rule(
        "home-path",
        r"(?:/home/|/Users/)[A-Za-z][A-Za-z0-9._-]*(?:/|\b)"
        r"|[A-Za-z]:\\\\?Users\\\\?[A-Za-z][A-Za-z0-9._-]*",
        "an absolute home directory, which names a person and a machine",
    ),
    Rule(
        "personal-email",
        r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
        "an email address outside the reserved documentation domains",
        allow=_email_allowed,
    ),
    Rule(
        "bearer",
        r"[Bb]earer\s+([A-Za-z0-9._~+/=-]{16,})",
        "a literal bearer credential",
        allow=_bearer_allowed,
    ),
]

SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".whl",
    ".woff",
    ".woff2",
    ".ttf",
    ".mo",
    ".pyc",
    ".so",
}


class Finding:
    def __init__(self, path, line_number, rule, excerpt):
        self.path = path
        self.line_number = line_number
        self.rule = rule
        self.excerpt = excerpt


def scan_text(path, text):
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for rule in RULES:
            for match in rule.scan(line):
                findings.append(Finding(path, number, rule, _excerpt(line, match)))
    return findings


def _excerpt(line, match):
    start = max(match.start() - 12, 0)
    end = min(match.end() + 12, len(line))
    text = line[start:end].strip()
    return (text[:100] + "...") if len(text) > 100 else text


def _readable(path):
    return Path(path).suffix.lower() not in SKIP_SUFFIXES


def _decode(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def tracked_files(root):
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def walk_files(target):
    target = Path(target)
    if target.is_file():
        yield target
        return
    for base, directories, names in os.walk(target):
        directories[:] = [d for d in directories if d not in SKIP_DIRECTORIES]
        for name in names:
            yield Path(base) / name


def scan_paths(paths, root="."):
    findings = []
    for path in paths:
        full = Path(root) / path
        if not full.is_file() or not _readable(full):
            continue
        text = _decode(full.read_bytes())
        if text is None:
            continue
        findings.extend(scan_text(str(path), text))
    return findings


#: git's constant hash for the empty tree, used to diff the very first commit,
#: which has no HEAD to compare against.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _staged_names(root):
    has_head = (
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"]
    if not has_head:
        command.append(EMPTY_TREE)
    result = subprocess.run(command, cwd=root, capture_output=True, check=True)
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def scan_staged(root="."):
    """Scan the content git would actually record, not the working tree."""
    names = _staged_names(root)
    findings = []
    for name in names:
        if not _readable(name):
            continue
        blob = subprocess.run(
            ["git", "show", f":{name}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0:
            continue
        text = _decode(blob.stdout)
        if text is None:
            continue
        findings.extend(scan_text(name, text))
    return findings, len(names)


DIRTY_FIXTURE = {
    # Assembled from fragments so the literal shapes never exist in this file,
    # and so the scanner can be proven against them without committing one.
    "config.md": "token: "
    + "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + "."
    + "eyJpc3MiOiJkZW1vIn0"
    + "."
    + "c2lnbmF0dXJl\n",
    "notes.txt": "host: " + "192." + "168." + "1.42" + ":8123\n",
    "run.sh": "source " + "/ho" + "me/" + "someone" + "/.env\n",
    "owner.txt": "contact " + "person" + "@" + "realcompany.co.uk" + "\n",
    "curl.md": "curl -H 'Authorization: " + "Bearer " + "abcd1234efgh5678ijkl" + "'\n",
}


def run_demo():
    """Prove the scanner fails on dirty content without committing any."""
    with tempfile.TemporaryDirectory() as directory:
        for name, content in DIRTY_FIXTURE.items():
            (Path(directory) / name).write_text(content, encoding="utf-8")
        findings = scan_paths(sorted(DIRTY_FIXTURE), root=directory)
    triggered = sorted({finding.rule.name for finding in findings})
    expected = sorted({rule.name for rule in RULES})
    report(findings, scanned=len(DIRTY_FIXTURE), label="synthetic dirty fixture")
    missing = [name for name in expected if name not in triggered]
    if missing:
        print(f"error: the demo fixture did not trigger {', '.join(missing)}")
        return 1
    print(f"demo: every rule fired ({', '.join(triggered)}); the scanner is working")
    return 0


def report(findings, *, scanned, label="tracked files"):
    if not findings:
        print(f"leakcheck: 0 findings in {scanned} {label}")
        return
    print(f"leakcheck[{len(findings)}]{{file,line,rule,excerpt}}:")
    for finding in findings:
        print(f"  {finding.path},{finding.line_number},{finding.rule.name},{finding.excerpt!r}")
    print("rules:")
    for name in sorted({finding.rule.name for finding in findings}):
        rule = next(r for r in RULES if r.name == name)
        print(f"  {name}: {rule.message}")
    print("help:")
    print("  Replace the value with a synthetic one: light.example_lamp, area 'Example Room',")
    print("  https://homeassistant.example.com, or read it from the environment instead.")
    print(f"  A line that must keep the shape can carry the marker: {ALLOW_MARKER}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="leakcheck",
        description="Scan for installation-specific data that must not enter a public repository.",
    )
    parser.add_argument("paths", nargs="*", help="files or directories to scan")
    parser.add_argument("--staged", action="store_true", help="scan staged content instead")
    parser.add_argument(
        "--demo", action="store_true", help="scan a synthetic dirty tree and expect failure"
    )
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)

    if args.demo:
        return run_demo()

    if args.staged:
        findings, scanned = scan_staged(args.root)
        report(findings, scanned=scanned, label="staged files")
        return 1 if findings else 0

    if args.paths:
        collected = []
        for target in args.paths:
            base = Path(target)
            if not base.is_absolute():
                base = Path(args.root) / target
            for found in walk_files(base):
                try:
                    collected.append(str(found.relative_to(args.root)))
                except ValueError:
                    collected.append(str(found))
        paths = sorted(set(collected))
        label = "files"
    else:
        label = "tracked files"
        paths = tracked_files(args.root)
        if paths is None:
            paths = sorted({str(p) for p in walk_files(args.root)})

    findings = scan_paths(paths, root=args.root)
    report(findings, scanned=len(paths), label=label)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
