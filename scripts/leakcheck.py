#!/usr/bin/env python3
"""Block installation-specific data from entering this public repository.

ha-axi talks to home automation installations, so the failure mode that matters
is not a bug -- it is a commit that quietly describes, or grants access to, the
installation it was developed against. A rule a human has to remember is not a
control, so this scanner runs from a pre-commit hook, a commit-msg hook, and CI.

It looks for shapes rather than a denylist of known-bad strings, because the
strings that matter are the ones nobody thought to list. See RULES below for the
current set.

Two passes run over every file:

* a line pass, which reports the exact line; and
* a condensed pass, which strips whitespace, quotes, backslashes and ``+`` from
  the whole file before re-applying the token rules. A credential split over
  several source lines, or assembled by concatenation, is invisible to a line
  pass -- and splitting a token over fragments is exactly how someone hides one,
  deliberately or not. The condensed pass is restricted to the token rules
  because joining arbitrary lines can fuse unrelated digits into a plausible
  address, and a guard that cries wolf gets bypassed.

Usage:
  scripts/leakcheck.py                     scan every tracked file
  scripts/leakcheck.py --staged            scan the staged content of a commit
  scripts/leakcheck.py --commit-msg PATH   scan a commit message
  scripts/leakcheck.py PATH [PATH...]      scan explicit files or directories
  scripts/leakcheck.py --demo              scan a synthetic dirty tree and expect failure
  scripts/leakcheck.py --rules             list the rules and what each one catches

A line may carry `leakcheck: allow=<rule>[,<rule>]` to exempt itself from those
named rules. The exemption is deliberately per-rule: a blanket marker would
switch off every rule on the line, including one nobody was thinking about when
they wrote it, which is how a live credential hides behind a suppressed lint.

PATH_ALLOWANCES says the same thing for a file that cannot carry a marker at
all. It is per-path AND per-rule for the same reason, and `--rules` prints it,
because an exemption nobody can see is one nobody re-examines.

Standard library only, so the hooks run without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ALLOW_PREFIX = "leakcheck: allow="

#: Documentation-reserved domains (RFC 2606, RFC 6761). The local part of an
#: address is deliberately NOT consulted: `noreply@` on a real domain still
#: identifies a real organisation.
_ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "example",
    "invalid",
    "test",
    "localhost",
)


class Rule:
    """One detectable shape, and the guidance printed when it fires."""

    def __init__(self, name, pattern, message, allow=None, condensed=False):
        self.name = name
        self.pattern = re.compile(pattern)
        self.message = message
        self.allow = allow or (lambda match: False)
        #: Whether this rule also runs over the condensed whole-file view.
        self.condensed = condensed

    def scan(self, text):
        for match in self.pattern.finditer(text):
            if not self.allow(match):
                yield match


def _email_allowed(match):
    domain = match.group(2).lower()
    return any(
        domain == allowed or domain.endswith("." + allowed) for allowed in _ALLOWED_EMAIL_DOMAINS
    )


def _bearer_allowed(match):
    """Keep the bearer rule off prose and placeholders.

    The pattern restricts the value to the base64/hex alphabet, which excludes
    `Bearer <token>` and `Bearer $HA_TOKEN`. What is left to exclude is English
    prose, so a real value must also carry a digit, which encoded bytes do.
    """
    return not any(character.isdigit() for character in match.group(1))


def _hex_id_allowed(match):
    """Keep the MAC rule off content-addressed hex such as a git object id."""
    return match.group(0).count(":") not in (5, 7)


RULES = [
    Rule(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}",
        "a JSON Web Token, which is what a Home Assistant long-lived access token looks like",
        condensed=True,
    ),
    Rule(
        "bearer",
        r"[Bb]earer\s+([A-Za-z0-9._~+/=-]{16,})",
        "a literal bearer credential",
        allow=_bearer_allowed,
        condensed=False,
    ),
    Rule(
        "private-ip",
        r"\b(?:192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
        "an RFC1918 private address, which describes somebody's network",
    ),
    Rule(
        "cgnat-ip",
        r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
        "a carrier-grade NAT address (the 100.64/10 range), a common remote-access overlay",
    ),
    Rule(
        "link-local-ip",
        r"\b169\.254\.\d{1,3}\.\d{1,3}\b",
        "an IPv4 link-local address, which describes somebody's network",
    ),
    Rule(
        "private-ipv6",
        r"\b(?:[fF][cCdD][0-9a-fA-F]{2}|[fF][eE]80):[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){1,7}",
        "a unique-local or link-local IPv6 address, which describes somebody's network",
    ),
    Rule(
        "remote-ui-host",
        r"\b[0-9a-z]{8,}\.ui\.nabu\.casa\b",
        "a per-installation remote-access hostname, which grants a route to somebody's home",
    ),
    Rule(
        "lan-hostname",
        r"\b[A-Za-z0-9][A-Za-z0-9-]*\.(?:local|lan|localdomain)\b(?![.\w])",
        "a LAN or mDNS hostname, which names somebody's machine",
    ),
    Rule(
        "coordinates",
        r"(?i)\b(?:latitude|longitude|\blat\b|\blon\b|\blng\b)\W{0,4}[-+]?\d{1,3}\.\d{3,}",
        "a geographic coordinate; /api/config returns the coordinates of the house",
    ),
    Rule(
        "mac-address",
        r"\b(?:[0-9a-fA-F]{2}:){5,7}[0-9a-fA-F]{2}\b",
        "a MAC or Zigbee IEEE address, which uniquely identifies somebody's hardware",
        allow=_hex_id_allowed,
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
]

RULES_BY_NAME = {rule.name: rule for rule in RULES}

#: Files exempt from one named rule each, for content that cannot carry a
#: `leakcheck: allow=` marker. JSON has no comment syntax, and these files are
#: third-party data vendored byte-for-byte -- editing one to satisfy this
#: scanner would replace the specification's opinion with ours, which is the
#: opposite of what a conformance fixture is for. Scoped exactly like the
#: per-line marker: one path, one rule, every other rule still runs.
PATH_ALLOWANCES = {
    # One upstream case escapes backslashes in a synthetic Windows drive path
    # under the users directory. It names nobody and reaches nothing -- and the
    # shape is deliberately not repeated here, or this file would trip too.
    "tests/fixtures/toon-spec/encode/primitives.json": frozenset({"home-path"}),
}


def path_allowances(path):
    """Rule names ``path`` is exempt from.

    Compared exactly against the repository-relative name the caller holds. A
    path that merely ends with an allowed one is a different file -- a shadowing
    directory, a suffixed twin -- and exempting it would grant the entry every
    directory it is ever copied into.
    """
    return PATH_ALLOWANCES.get(str(path), frozenset())


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

#: Characters removed to build the condensed view: source syntax that can sit
#: between two halves of one secret without changing what it is.
_CONDENSE = re.compile(r"[\s\"'`\\+,]")


class Finding:
    def __init__(self, path, line_number, rule, excerpt, matched, pass_name="line"):
        self.path = path
        self.line_number = line_number
        self.rule = rule
        self.excerpt = excerpt
        self.matched = matched
        self.pass_name = pass_name

    @property
    def key(self):
        # Keyed on the matched value, not the excerpt: the same secret seen by
        # the line pass and the condensed pass has different surroundings but
        # is one leak, and reporting it twice buries the signal.
        return (self.path, self.rule.name, self.matched)


def allowed_rules(line):
    """Rule names this line exempts itself from."""
    index = line.find(ALLOW_PREFIX)
    if index < 0:
        return frozenset()
    tail = line[index + len(ALLOW_PREFIX) :]
    names = re.match(r"[A-Za-z0-9_,-]+", tail.strip())
    if not names:
        return frozenset()
    return frozenset(part for part in names.group(0).split(",") if part)


def _decoded_variants(text):
    """The text plus a percent-decoded view, so encoded tokens still register."""
    variants = [text]
    if "%" in text:
        try:
            decoded = urllib.parse.unquote(text)
        except (UnicodeDecodeError, ValueError):
            decoded = ""
        if decoded and decoded != text:
            variants.append(decoded)
    return variants


def scan_text(path, text):
    """Scan one file's content with both the line pass and the condensed pass."""
    findings = []
    seen = set()
    by_path = path_allowances(path)

    for number, line in enumerate(text.splitlines(), start=1):
        exempt = allowed_rules(line) | by_path
        for variant in _decoded_variants(line):
            for rule in RULES:
                if rule.name in exempt:
                    continue
                for match in rule.scan(variant):
                    finding = Finding(path, number, rule, _excerpt(variant, match), match.group(0))
                    if finding.key not in seen:
                        seen.add(finding.key)
                        findings.append(finding)

    findings.extend(_scan_condensed(path, text, seen, by_path))
    findings.sort(key=lambda f: (f.line_number, f.rule.name))
    return findings


def _scan_condensed(path, text, seen, by_path):
    """Re-scan the whole file with source-level separators removed."""
    condensed_chars = []
    line_of = []
    line_number = 1
    for character in text:
        if character == "\n":
            line_number += 1
            continue
        if _CONDENSE.match(character):
            continue
        condensed_chars.append(character)
        line_of.append(line_number)
    condensed = "".join(condensed_chars)
    if not condensed:
        return []

    findings = []
    for variant, offsets in _condensed_variants(condensed, line_of):
        for rule in RULES:
            if not rule.condensed:
                continue
            for match in rule.scan(variant):
                start = offsets[match.start()] if match.start() < len(offsets) else 1
                if rule.name in by_path or _line_is_exempt(text, start, rule.name):
                    continue
                finding = Finding(
                    path, start, rule, _excerpt(variant, match), match.group(0), pass_name="joined"
                )
                if finding.key not in seen:
                    seen.add(finding.key)
                    findings.append(finding)
    return findings


def _condensed_variants(condensed, line_of):
    variants = [(condensed, line_of)]
    if "%" in condensed:
        decoded = urllib.parse.unquote(condensed)
        if decoded != condensed:
            # Offsets shift once characters are decoded; attribute the whole
            # match to the first line of the condensed region instead.
            variants.append((decoded, [line_of[0] if line_of else 1] * (len(decoded) + 1)))
    return variants


def _line_is_exempt(text, line_number, rule_name):
    lines = text.splitlines()
    if 1 <= line_number <= len(lines):
        return rule_name in allowed_rules(lines[line_number - 1])
    return False


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
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
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
            ["git", "show", f":{name}"], cwd=root, capture_output=True, check=False
        )
        if blob.returncode != 0:
            continue
        text = _decode(blob.stdout)
        if text is None:
            continue
        findings.extend(scan_text(name, text))
    return findings, len(names)


#: Trailers whose whole purpose is to carry a person's identity. Git already
#: records author and committer addresses in every commit, so flagging these
#: would block ordinary attribution without preventing anything -- and a guard
#: that blocks routine commits is a guard people learn to bypass.
_IDENTITY_TRAILER = re.compile(
    r"^(?:co-authored-by|signed-off-by|reported-by|reviewed-by|acked-by|tested-by"
    r"|suggested-by|helped-by|author|committer|cc)\s*:",
    re.IGNORECASE,
)


def scan_commit_message(path):
    """Scan a commit message.

    Comment lines (which git strips) and identity trailers are ignored; every
    other line is scanned exactly as file content is.
    """
    text = _decode(Path(path).read_bytes()) or ""
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if _IDENTITY_TRAILER.match(line.strip()):
            # Exempt the address, not the line: dropping the whole line would
            # make a trailer a place to smuggle anything else. Reuses the same
            # per-rule marker the scanner already understands.
            line = f"{line}  {ALLOW_PREFIX}personal-email"
        lines.append(line)
    return scan_text(str(path), "\n".join(lines))


def _b64(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def synthetic_jwt():
    """Build a structurally valid, entirely fake JWT at run time.

    Encoding it here rather than writing the literal keeps the ``eyJ`` shape out
    of this file, so the scanner's own source stays clean under the condensed
    pass that this fixture exists to exercise.
    """
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64({'sub': 'example'})}.c2lnbmF0dXJlaGVyZQ"


def dirty_fixture():
    """Synthetic dirty content, one file per rule, assembled at run time."""
    token = synthetic_jwt()
    head, payload, signature = token.split(".")
    return {
        "token.md": f"token: {token}\n",
        # The same token, split the way a careless paste or a source literal
        # splits one. Only the condensed pass sees this.
        "split.py": f'TOKEN = (\n    "{head}."\n    "{payload}."\n    "{signature}"\n)\n',
        "encoded.txt": f"u={urllib.parse.quote(token, safe='')}\n",
        "lan.txt": "host " + "192." + "168.1.42:8123\n",
        "cgnat.txt": "peer " + "100." + "101.102.103\n",
        "linklocal.txt": "addr " + "169." + "254.10.20\n",
        "ipv6.txt": "addr " + "fd12" + ":3456:789a::1\n",
        "remoteui.txt": "url https://" + "a1b2c3d4e5f6a7b8" + ".ui.nabu.casa\n",
        "mdns.txt": "url http://" + "homeassistant" + ".local:8123\n",
        "coords.txt": "latitude: " + "51.5" + "0736\n",
        "mac.txt": "mac " + "a4:c1" + ":38:9f:2b:7e\n",
        "run.sh": "source " + "/ho" + "me/" + "someone" + "/.env\n",
        "owner.txt": "contact " + "noreply" + "@" + "realcompany.co.uk\n",
        "curl.md": "curl -H 'Authorization: " + "Bearer " + "abcd1234efgh5678ijkl" + "'\n",
    }


def run_demo():
    """Prove the scanner fails on dirty content without committing any."""
    fixture = dirty_fixture()
    with tempfile.TemporaryDirectory() as directory:
        for name, content in fixture.items():
            (Path(directory) / name).write_text(content, encoding="utf-8")
        findings = scan_paths(sorted(fixture), root=directory)
    triggered = sorted({finding.rule.name for finding in findings})
    report(findings, scanned=len(fixture), label="synthetic dirty fixture")
    missing = [rule.name for rule in RULES if rule.name not in triggered]
    if missing:
        print(f"error: the demo fixture did not trigger {', '.join(missing)}")
        return 1
    joined = [f for f in findings if f.pass_name == "joined"]
    if not joined:
        print("error: the condensed pass did not fire; a split token would go unnoticed")
        return 1
    print(f"demo: every rule fired ({', '.join(triggered)}); the scanner is working")
    return 0


def list_rules():
    print(f"rules[{len(RULES)}]{{rule,passes,detects}}:")
    for rule in RULES:
        passes = "line+joined" if rule.condensed else "line"
        print(f"  {rule.name},{passes},{rule.message}")
    print(f"allowances[{len(PATH_ALLOWANCES)}]{{path,rules}}:")
    for candidate, names in sorted(PATH_ALLOWANCES.items()):
        print(f"  {candidate},{'|'.join(sorted(names))}")
    print("help:")
    print(f"  Exempt one line from one rule with `{ALLOW_PREFIX}<rule>`")
    print("  A file that cannot carry a marker is exempted per rule in PATH_ALLOWANCES")
    return 0


def report(findings, *, scanned, label="tracked files"):
    if not findings:
        print(f"leakcheck: 0 findings in {scanned} {label}")
        return
    print(f"leakcheck[{len(findings)}]{{file,line,rule,pass,excerpt}}:")
    for finding in findings:
        print(
            f"  {finding.path},{finding.line_number},{finding.rule.name},"
            f"{finding.pass_name},{finding.excerpt!r}"
        )
    print("rules:")
    for name in sorted({finding.rule.name for finding in findings}):
        print(f"  {name}: {RULES_BY_NAME[name].message}")
    print("help:")
    print("  Replace the value with a synthetic one: light.example_lamp, area 'Example Room',")
    print("  https://homeassistant.example.com, or read it from the environment instead.")
    print(f"  A line that must keep one shape can carry `{ALLOW_PREFIX}<rule>`.")
    print("  A `joined` finding was assembled across lines; the line shown is where it starts.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="leakcheck",
        description="Scan for installation-specific data that must not enter a public repository.",
    )
    parser.add_argument("paths", nargs="*", help="files or directories to scan")
    parser.add_argument("--staged", action="store_true", help="scan staged content instead")
    parser.add_argument("--commit-msg", metavar="PATH", help="scan a commit message file")
    parser.add_argument("--demo", action="store_true", help="scan a synthetic dirty tree")
    parser.add_argument("--rules", action="store_true", help="list the rules and exit")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)

    if args.rules:
        return list_rules()
    if args.demo:
        return run_demo()
    if args.commit_msg:
        findings = scan_commit_message(args.commit_msg)
        report(findings, scanned=1, label="commit message")
        return 1 if findings else 0
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
        paths = tracked_files(args.root)
        label = "tracked files"
        if paths is None:
            paths = sorted({str(p) for p in walk_files(args.root)})

    findings = scan_paths(paths, root=args.root)
    report(findings, scanned=len(paths), label=label)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
