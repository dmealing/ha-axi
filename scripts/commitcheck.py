#!/usr/bin/env python3
"""Reject a commit message that release-please cannot parse.

Release automation that reports success while shipping nothing is worse than
automation that fails, and that is exactly what happened here: a merged fix sat
on ``main`` unreleased while the release workflow exited 0 with ``Considering: 0
commits``. release-please catches every parse error, logs ``commit could not be
parsed`` at debug level, and carries on with the commits it *could* read -- so a
message it chokes on is silently dropped from the changelog and from the version
bump, and the run stays green.

The rule that bit us
--------------------

release-please 17.3.0 parses commit messages with ``@conventional-commits/parser``
(``^0.4.1``), the reference implementation of the Conventional Commits grammar.
That grammar tries to read **every physical line of the body** as a ``<footer>``::

    <footer> ::= <token>, <separator>, <whitespace>*, <value>
    <token>  ::= <type>, "(" <scope> ")", ["!"] | <type>
    <type>   ::= 1*<any UTF8-octet except newline, parens, "!", ":" or whitespace>
    <scope>  ::= 1*<any UTF8-octet except newline or parens>

``<type>`` swallows characters from the start of the line until it meets one of
whitespace, a newline, ``!``, ``:``, ``(`` or ``)``. If the character it stops on
is ``(``, the parser **commits** to reading a scope: it consumes up to the next
``(``, ``)`` or newline, and if that character is not ``)`` it *throws*::

    if (scanner.peek() !== ')') {
      throw scanner.abort(node, [')'])          // lib/parser.js:177
    }

That ``throw`` is one of only four in the whole grammar, and it is the only one
reachable from the body. Every other production reports failure by *returning* an
``Error`` its caller can back out of; this one unwinds the entire parse. So a line
like::

    `Decimal(repr(value))` inside the canonical range - the shortest round-tripping

is unparseable, while the identical phrase one word further along the line is
fine. It is not parentheses, not backticks, not the dash and not the position on
its own -- it is the interaction: **a body line whose first character begins an
unbroken run of non-space, non-parenthesis, non-``!``, non-``:`` characters that
is immediately followed by ``(``, where the parenthesis does not close before the
next ``(`` or the end of that line.**

Rich commit bodies are not the problem and are not what this guard restricts. A
line reflowed by one word, or a space before the parenthesis, satisfies the
grammar with the prose intact.

Two engines, one answer
-----------------------

``--engine node`` runs the real parser: ``vendor/conventional-commits-parser/``
holds a byte-for-byte copy of the four dependency-free modules that make it up,
so ``node`` alone runs it with no ``npm install`` and no network. That is the
authoritative answer, because it is upstream's own code rather than a description
of it.

``--engine python`` runs the transcription in this file -- a line-by-line port of
the same grammar -- so a machine with no ``node`` still gets a verdict rather than
a skip. ``tests/test_commit_message.py`` runs the whole corpus through both and
asserts they agree on the verdict *and* on the reported line, column and token; a
guard whose fallback disagreed with the thing it stands in for would be worse than
none.

``--engine auto`` (the default, and what the hooks use) prefers ``node``.

Usage:
  scripts/commitcheck.py --commit-msg PATH    check one message file (the hook)
  scripts/commitcheck.py --message TEXT       check a literal message
  scripts/commitcheck.py --range A..B         check every commit message in a range
  scripts/commitcheck.py --since-release      check every commit since the released tag
  scripts/commitcheck.py --rules              explain the rule, the engines and the allowances
  scripts/commitcheck.py --demo               self-test: prove it still rejects and still accepts

Exit status is 0 when every message parses and 1 when one does not, so the hooks
and the release workflow can both use it directly.

Standard library only, so the hooks run without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The vendored copy of the parser release-please runs. See its PROVENANCE.md.
VENDORED_PARSER = REPO_ROOT / "vendor" / "conventional-commits-parser" / "lib" / "parser.js"

#: ``throw`` statements in the vendored ``lib/parser.js``. Asserted by the suite:
#: a refreshed grammar that grew or lost one has changed where a message can be
#: rejected, and the transcription below has to be re-read rather than trusted.
THROW_SITES = 4

#: Commits that are known to be unparseable, already accounted for, and must not
#: re-fail the release audit for ever. Scoped exactly like ``PATH_ALLOWANCES`` in
#: ``leakcheck.py``: one full SHA, one reason, printed by ``--rules``, and pinned
#: by the suite so an entry that has outlived its cause fails rather than quietly
#: covering something new.
#:
#: Empty here, and that is a measured fact rather than an assumption: every
#: commit in this repository's history was run through the parser and all of them
#: parse. One of them parses by a single word -- see
#: ``tests/fixtures/commit-messages/`` -- which is why this guard is here despite
#: nothing having been lost yet.
KNOWN_UNPARSEABLE = {}


# ---------------------------------------------------------------------------
# The transcription.
#
# A port of `@conventional-commits/parser@0.4.1` `lib/parser.js`, `lib/scanner.js`,
# `lib/type-checks.js` and `lib/codes.js`, kept structurally identical to the
# original so the two can be read side by side. Upstream reports failure two
# ways and the difference is the whole point of this file: `scanner.abort`
# *returns* an `Error` the caller may ignore, and four sites `throw` one. Here,
# `_abort` returns `_Rejected` and the same four sites raise `CommitParseError`.
# ---------------------------------------------------------------------------

CR = "\u000d"
LF = "\u000a"
ZWNBSP = "\ufeff"
TAB = "\u0009"
VT = "\u000b"
FF = "\u000c"
SP = "\u0020"
NBSP = "\u00a0"

_WHITESPACE = frozenset({ZWNBSP, TAB, VT, FF, SP, NBSP})

#: What JavaScript's `String.prototype.trim` removes, which is not what Python's
#: `str.strip` removes: JS counts ZWNBSP and the line separators, Python does not
#: count ZWNBSP. `message()` trims before it scans, so the difference moves every
#: reported line number when a message happens to start with one.
_JS_TRIM = frozenset(
    {CR, LF, ZWNBSP, TAB, VT, FF, SP, NBSP, "\u2028", "\u2029"}
    | {chr(c) for c in range(0x11000) if unicodedata.category(chr(c)) == "Zs"}
)


def _is_whitespace(token):
    return token in _WHITESPACE


def _is_newline(token):
    return bool(token) and token[0] in (CR, LF)


def _is_parens(token):
    return token in ("(", ")")


class CommitParseError(Exception):
    """A message the grammar refuses -- what release-please silently drops.

    Carries the position and tokens upstream reports, so the two engines can be
    compared field by field rather than by string equality on an error message.
    """

    def __init__(self, message, line, column, found, valid):
        super().__init__(message)
        self.line = line
        self.column = column
        self.found = found  # the offending token, or None at end of input
        self.valid = valid  # what the grammar would have accepted there


class _Rejected:
    """A production that did not match. Upstream's ``Error`` return value."""

    def __init__(self, error):
        self.error = error


class _Node:
    __slots__ = ("start", "type", "value")

    def __init__(self, node_type, start):
        self.type = node_type
        self.value = ""
        self.start = start


class _Scanner:
    def __init__(self, text):
        self.text = text
        self.line = 1
        self.column = 1
        self.offset = 0

    def eof(self):
        return self.offset >= len(self.text)

    def next(self, n=None):
        token = self.text[self.offset : self.offset + n] if n else self.peek()
        self.offset += len(token)
        self.column += len(token)
        if _is_newline(token):
            self.line += 1
            self.column = 1
        return token

    def peek(self):
        token = self.text[self.offset : self.offset + 1]
        if token == CR and self.text[self.offset + 1 : self.offset + 2] == LF:
            token += LF
        return token

    def peek_literal(self, literal):
        return self.text[self.offset : self.offset + len(literal)] == literal

    def position(self):
        return (self.line, self.column, self.offset)

    def rewind(self, position):
        self.line, self.column, self.offset = position

    def enter(self, node_type):
        return _Node(node_type, self.position())

    def abort(self, node, expected=None):
        """Upstream ``Scanner.abort``: build the error, then rewind."""
        line, column, _ = self.position()
        valid = [t for t in expected if t] if expected is not None else [f"<{node.type}>"]
        joined = ", ".join(valid)
        if self.eof():
            found = None
            text = f"unexpected token EOF at {line}:{column}, valid tokens [{joined}]"
        else:
            found = self.peek()
            text = f"unexpected token '{found}' at {line}:{column}, valid tokens [{joined}]"
        self.rewind(node.start)
        return _Rejected(CommitParseError(text, line, column, found, valid))


def _message(commit_text):
    """``<summary>`` then an optional body and footers. Upstream ``message``."""
    scanner = _Scanner(_js_trim(commit_text))
    node = scanner.enter("message")

    result = _summary(scanner)
    if isinstance(result, _Rejected):
        raise result.error  # lib/parser.js:17
    if scanner.eof():
        return

    nl = _newline(scanner)
    if isinstance(nl, _Rejected):
        raise nl.error  # lib/parser.js:30
    body = _body(scanner)
    if isinstance(body, _Rejected):
        body = None
    if scanner.eof():
        return

    if body is not None:
        nl = _newline(scanner)
        if isinstance(nl, _Rejected):
            raise nl.error  # lib/parser.js:48

    while not scanner.eof():
        if isinstance(_footer(scanner), _Rejected):
            break
        if isinstance(_newline(scanner), _Rejected):
            break
    del node


def _summary(scanner):
    """``<type> ["(" <scope> ")"] ["!"] ":" <whitespace>* <text>``."""
    node = scanner.enter("summary")

    result = _type(scanner)
    if isinstance(result, _Rejected):
        return result

    scope = _scope(scanner)
    if isinstance(scope, _Rejected):
        scope = None

    bang = _breaking_change(scanner)
    if isinstance(bang, _Rejected):
        bang = None

    if isinstance(_separator(scanner), _Rejected):
        return scanner.abort(node, [None if scope else "(", None if bang else "!", ":"])

    _whitespace(scanner)
    _text(scanner)
    return node


def _type(scanner):
    """``1*<any octet except newline, parens, "!", ":" or whitespace>``."""
    node = scanner.enter("type")
    while not scanner.eof():
        token = scanner.peek()
        if _is_parens(token) or _is_whitespace(token) or _is_newline(token) or token in ("!", ":"):
            break
        node.value += scanner.next()
    if node.value == "":
        return scanner.abort(node)
    return node


def _text(scanner):
    node = scanner.enter("text")
    while not scanner.eof():
        if _is_newline(scanner.peek()):
            break
        node.value += scanner.next()
    return node


def _scope(scanner):
    """``"(" 1*<any octet except newline or parens> ")"``.

    The one production in the grammar that raises rather than returns. Once the
    opening parenthesis is consumed the parser is committed: anything but ``)``
    where the closing parenthesis belongs unwinds the whole parse, and that is
    the failure this script exists to catch before it reaches ``main``.
    """
    if scanner.peek() != "(":
        return scanner.abort(scanner.enter("scope"))
    scanner.next()

    node = scanner.enter("scope")
    while not scanner.eof():
        token = scanner.peek()
        if _is_parens(token) or _is_newline(token):
            break
        node.value += scanner.next()

    if scanner.peek() != ")":
        raise scanner.abort(node, [")"]).error  # lib/parser.js:177
    scanner.next()

    if node.value == "":
        return scanner.abort(node)
    return node


def _body(scanner):
    """Every body line, each first offered to ``<footer>``. Upstream ``body``."""
    node = scanner.enter("body")

    if not isinstance(_pre_footer(scanner), _Rejected):
        return scanner.abort(node)

    breaking = _breaking_change(scanner, allow_bang=False)
    if not isinstance(breaking, _Rejected) and scanner.peek() == ":":
        _separator(scanner)
        _whitespace(scanner)

    _text(scanner)
    nl = _newline(scanner)
    if not isinstance(nl, _Rejected) and isinstance(_body(scanner), _Rejected):
        scanner.abort(nl)
    return node


def _pre_footer(scanner):
    node = scanner.enter("pre-footer")
    while not scanner.eof():
        _newline(scanner)
        if isinstance(_footer(scanner), _Rejected):
            return scanner.abort(node)
    return node


def _footer(scanner):
    """``<token> <separator> <whitespace>* <value>``."""
    node = scanner.enter("footer")

    result = _token(scanner)
    if isinstance(result, _Rejected):
        return result

    separator = _separator(scanner)
    if isinstance(separator, _Rejected):
        scanner.abort(node)
        return separator

    _whitespace(scanner)
    _value(scanner)
    return node


def _token(scanner):
    """``<breaking-change> | <type> ["(" <scope> ")"] ["!"]``.

    The route from an ordinary body line into :func:`_scope`.
    """
    node = scanner.enter("token")

    breaking = _breaking_change(scanner)
    if isinstance(breaking, _Rejected):
        scanner.abort(node)
    else:
        return node

    result = _type(scanner)
    if isinstance(result, _Rejected):
        return result
    _scope(scanner)
    _breaking_change(scanner)
    return node


def _breaking_change(scanner, allow_bang=True):
    """``"!" | "BREAKING CHANGE" | "BREAKING-CHANGE"``."""
    node = scanner.enter("breaking-change")
    if scanner.peek() == "!" and allow_bang:
        node.value = scanner.next()
    elif scanner.peek_literal("BREAKING CHANGE") or scanner.peek_literal("BREAKING-CHANGE"):
        node.value = scanner.next(len("BREAKING CHANGE"))
    if node.value == "":
        return scanner.abort(node, ["BREAKING CHANGE"])
    return node


def _value(scanner):
    node = scanner.enter("value")
    _text(scanner)
    while not isinstance(_continuation(scanner), _Rejected):
        pass
    return node


def _continuation(scanner):
    node = scanner.enter("continuation")
    nl = _newline(scanner)
    if isinstance(nl, _Rejected):
        return nl
    whitespace = _whitespace(scanner)
    if isinstance(whitespace, _Rejected):
        scanner.abort(node)
        return whitespace
    _text(scanner)
    return node


def _separator(scanner):
    """``":" | " #"``."""
    node = scanner.enter("separator")
    if scanner.peek() == ":":
        node.value = scanner.next()
        return node
    if scanner.peek() == " ":
        scanner.next()
        if scanner.peek() == "#":
            scanner.next()
            node.value = " #"
            return node
        return scanner.abort(node)
    return scanner.abort(node)


def _whitespace(scanner):
    node = scanner.enter("whitespace")
    while _is_whitespace(scanner.peek()):
        node.value += scanner.next()
    if node.value == "":
        return scanner.abort(node, [" "])
    return node


def _newline(scanner):
    node = scanner.enter("newline")
    while _is_newline(scanner.peek()):
        node.value += scanner.next()
    if node.value == "":
        return scanner.abort(node, ["<CR><LF>", "<LF>"])
    return node


def _js_trim(text):
    return text.strip("".join(_JS_TRIM))


# ---------------------------------------------------------------------------
# release-please's own preprocessing, and the two engines.
# ---------------------------------------------------------------------------

#: The types release-please 17.3.0 splits a single message on, from
#: `build/src/commit.js` `splitMessages`. One commit can carry several
#: conventional commits, and each is parsed -- and can fail -- on its own.
_SPLIT = re.compile(
    r"\r?\n\r?\n"
    r"(?=(?:feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(?:\(.*?\))?: )"
)


def split_messages(message):
    """Transcription of release-please's ``splitMessages``.

    A commit body may hold further conventional commits, either after a blank
    line or inside ``BEGIN_NESTED_COMMIT``/``END_NESTED_COMMIT``. Each part is
    parsed separately, so one unparseable part loses only that part -- which is
    still a silent loss, and still a rejection here.
    """
    parts = message.split("BEGIN_NESTED_COMMIT")
    messages = [parts[0]]
    for part in parts[1:]:
        nested, *rest = part.split("END_NESTED_COMMIT")
        messages.append(nested)
        messages[0] = messages[0] + "END_NESTED_COMMIT".join(rest)
    conventional = [part for part in _SPLIT.split(messages[0]) if part]
    return conventional + messages[1:]


#: Upstream's error string, taken apart so the two engines can be compared field
#: by field. DOTALL is load-bearing: the offending token is very often the
#: newline the scope ran into, and a pattern that could not match one would round
#: every such rejection off to "an error shape this script has never seen".
_ERROR = re.compile(
    r"\Aunexpected token (?:'(?P<found>.*?)'|(?P<eof>EOF)) at (?P<line>\d+):(?P<column>\d+), "
    r"valid tokens \[(?P<valid>.*)\]\Z",
    re.DOTALL,
)

_NODE_RUNNER = """
const parser = require(process.env.COMMITCHECK_PARSER);
let data = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { data += chunk; });
process.stdin.on('end', () => {
  try {
    parser(data);
    process.stdout.write(JSON.stringify({ok: true}));
  } catch (err) {
    process.stdout.write(JSON.stringify({ok: false, message: err.message}));
  }
});
"""


def node_available():
    """Whether ``--engine node`` can run: an interpreter and the vendored copy."""
    if not VENDORED_PARSER.exists():
        return False
    try:
        subprocess.run(
            ["node", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def resolve_engine(requested):
    """``auto`` prefers the real parser and falls back to the transcription."""
    if requested != "auto":
        return requested
    return "node" if node_available() else "python"


def _parse_python(part):
    try:
        _message(part)
    except CommitParseError as error:
        return error
    return None


def _parse_node(part):
    environment = dict(os.environ, COMMITCHECK_PARSER=str(VENDORED_PARSER))
    result = subprocess.run(
        ["node", "-e", _NODE_RUNNER],
        input=part,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"the vendored parser could not be run: {result.stderr.strip()}")
    verdict = json.loads(result.stdout)
    if verdict["ok"]:
        return None
    text = verdict["message"]
    match = _ERROR.match(text)
    if not match:
        # An error shape this script has never seen. Report it rather than
        # rounding it off to a pass -- an unrecognised refusal is still one.
        return CommitParseError(text, 0, 0, None, [])
    return CommitParseError(
        text,
        int(match["line"]),
        int(match["column"]),
        None if match["eof"] else match["found"],
        [t for t in match["valid"].split(", ") if t],
    )


def parse_part(part, engine="python"):
    """Parse one conventional-commit part. ``None`` when it parses."""
    return _parse_node(part) if engine == "node" else _parse_python(part)


# ---------------------------------------------------------------------------
# The verdict a caller acts on.
# ---------------------------------------------------------------------------


class Problem:
    """One unparseable part of one message, located in the original message."""

    def __init__(self, error, *, part_index, part_count, line, source_line):
        self.error = error
        self.part_index = part_index
        self.part_count = part_count
        self.line = line  # absolute line in the message the author wrote
        self.source_line = source_line

    @property
    def column(self):
        return self.error.column

    def advice(self):
        """What to change. Named for the shape, not for the parser's vocabulary."""
        if ")" in self.error.valid:
            return [
                "A body line that begins with text followed straight away by '(' is read as a",
                "conventional-commit footer with a scope, and that scope must close with ')'",
                "before the next '(' or the end of the line.",
                "Fix it by keeping the prose and moving the parenthesis off the line start:",
                "  * reflow so the line does not begin with the parenthesised term; or",
                "  * put a word or a space before it -- `foo(bar(baz))` is fine mid-line; or",
                "  * close the parentheses on the same line, without nesting.",
            ]
        if "<CR><LF>" in self.error.valid or "<LF>" in self.error.valid:
            return [
                "The subject line has to be followed by a blank line before any body.",
            ]
        return [
            "The first line has to be a conventional-commit summary: 'type: subject', or",
            "'type(scope): subject', optionally with '!' before the colon for a breaking",
            "change. release-please skips anything else without saying so.",
        ]


def check(message, engine="python"):
    """Every problem release-please would hit in ``message``."""
    problems = []
    parts = split_messages(message)
    cursor = 0
    for index, part in enumerate(parts):
        error = parse_part(part, engine=engine)
        offset = message.find(part, cursor)
        if offset >= 0:
            cursor = offset + len(part)
        if error is None:
            continue
        line = _absolute_line(message, part, offset, error.line)
        source = message.splitlines()[line - 1] if 0 < line <= len(message.splitlines()) else ""
        problems.append(
            Problem(
                error,
                part_index=index,
                part_count=len(parts),
                line=line,
                source_line=source,
            )
        )
    return problems


def _absolute_line(message, part, offset, parsed_line):
    """Map a line number the parser reported back onto the message as written.

    The parser numbers lines within the *trimmed* part it was handed, so both the
    split and the trim have to be undone before the number means anything to the
    person who has to fix the line. A part that cannot be located (only reachable
    through the ``BEGIN_NESTED_COMMIT`` concatenation, which rebuilds part 0 from
    pieces) keeps the part-relative number rather than inventing one.
    """
    if offset < 0:
        return parsed_line
    trimmed = _js_trim(part)
    stripped = part[: len(part) - len(part.lstrip("".join(_JS_TRIM)))] if trimmed else part
    before = message[: offset + len(stripped)]
    return before.count("\n") + parsed_line


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------


def report(problems, label, stream=None):
    """Print every problem with the offending line under a caret, and the fix.

    ``stream`` is resolved at call time rather than bound as a default: a default
    argument captures whatever ``sys.stderr`` was when this module was imported,
    which is not the stream a caller redirected afterwards.
    """
    stream = sys.stderr if stream is None else stream
    print(f"commitcheck: {label} cannot be parsed by release-please", file=stream)
    for problem in problems:
        where = f"line {problem.line}"
        if problem.column:
            where += f", column {problem.column}"
        if problem.part_count > 1:
            where += f" (conventional-commit section {problem.part_index + 1})"
        print(f"  {where}: {problem.error}", file=stream)
        if problem.source_line:
            print(f"    {problem.source_line}", file=stream)
            print(f"    {' ' * max(problem.column - 1, 0)}^", file=stream)
        for line in problem.advice():
            print(f"  {line}", file=stream)
    print(
        "  release-please would log 'commit could not be parsed', drop this commit\n"
        "  from the changelog and the version bump, and still exit 0.",
        file=stream,
    )
    return 1


# ---------------------------------------------------------------------------
# Modes.
# ---------------------------------------------------------------------------


def released_version(root):
    manifest = Path(root) / ".release-please-manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["."]


def _tag_exists(tag, root):
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def release_range(root=".", stream=None):
    """The range to audit, and the tag it starts from.

    The manifest names the last *released* version, so ``v<version>..HEAD`` is
    the set release-please is about to read. On the run that publishes a release
    the manifest has already been bumped while the matching tag may not exist
    yet -- the audit and the release job run at the same time -- so a missing tag
    falls back to the newest release tag that does exist rather than failing on a
    race. Either way the audit covers a superset of what is still unreleased, and
    it says which tag it used.
    """
    stream = sys.stdout if stream is None else stream
    tag = f"v{released_version(root)}"
    if _tag_exists(tag, root):
        return f"{tag}..HEAD"
    existing = subprocess.run(
        ["git", "-C", str(root), "tag", "--list", "v*", "--sort=-v:refname"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for candidate in existing:
        if _tag_exists(candidate, root):
            print(
                f"commitcheck: {tag} does not exist yet; auditing from {candidate} instead",
                file=stream,
            )
            return f"{candidate}..HEAD"
    print("commitcheck: no release tag exists yet; auditing the whole history", file=stream)
    return "HEAD"


def commits_since(rev_range, root="."):
    """``[(sha, message)]`` for a revision range, newest first.

    ``--first-parent`` because that is the set release-please reads: it asks
    GitHub for the *merge commits on the branch*, not for everything reachable
    from it. Without it a work-in-progress message inside a merged branch would
    be reported as a commit release-please dropped, when release-please was never
    going to look at it -- a guard that cries wolf gets switched off.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--first-parent", "-z", "--format=%H%n%B", rev_range],
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for record in result.stdout.split("\0"):
        if not record.strip():
            continue
        sha, _, body = record.partition("\n")
        commits.append((sha.strip(), body))
    return commits


def audit_range(rev_range, *, engine, root=".", stream=None):
    """Fail when release-please would silently drop a commit from ``rev_range``.

    The pathology this exists for is a *green* release run that considered zero
    commits while commits were waiting, so the count is printed either way. The
    healthy no-op -- nothing since the tag -- and the broken one now read
    differently instead of both reading as success.
    """
    stream = sys.stdout if stream is None else stream
    commits = commits_since(rev_range, root=root)
    dropped = []
    allowed = []
    for sha, message in commits:
        problems = check(message, engine=engine)
        if not problems:
            continue
        if sha in KNOWN_UNPARSEABLE:
            allowed.append(sha)
            continue
        dropped.append((sha, message, problems))

    considered = len(commits) - len(dropped) - len(allowed)
    print(f"commitcheck: {rev_range} -- {len(commits)} commit(s)", file=stream)
    print(f"  release-please would consider: {considered}", file=stream)
    print(f"  silently dropped:              {len(dropped)}", file=stream)
    if allowed:
        print(f"  known-unparseable, accounted for: {len(allowed)}", file=stream)
        for sha in allowed:
            print(f"    {sha[:12]} {KNOWN_UNPARSEABLE[sha].splitlines()[0]}", file=stream)

    if not dropped:
        if commits and considered == 0:
            print(
                "  Nothing here for release-please to read: every commit in the range is one of\n"
                "  the known-unparseable entries above, and each of those says where its content\n"
                "  was accounted for. A release run reporting 'no commits' is telling the truth.",
                file=stream,
            )
        return 0

    if considered == 0:
        print(
            "\ncommitcheck: release-please would consider ZERO of these commits and still\n"
            "exit 0. That is the failure this check exists for: a green run that released\n"
            "nothing, and a merged fix left unpublished.",
            file=stream,
        )
    for sha, message, problems in dropped:
        subject = message.splitlines()[0] if message.splitlines() else "(empty)"
        report(problems, f"{sha[:12]} {subject}", stream=stream)
    return 1


# ---------------------------------------------------------------------------
# Self-test.
#
# Built from shapes rather than from remembered verdicts: a checker that stopped
# detecting anything reads as a checker with nothing to report, which is how a
# guard dies quietly. `--demo` runs in CI ahead of the real audit for the same
# reason `leakcheck.py --demo` does.
# ---------------------------------------------------------------------------

#: Messages the grammar must refuse, each with the shape it is there to pin.
DEMO_REJECTED = {
    "nested parens at the start of a body line": (
        "fix(toon): a subject\n\n`Decimal(repr(value))` inside the canonical range\n"
    ),
    "a parenthesis left open at the end of a body line": (
        "fix: a subject\n\nrows_for(machine_identifier\nis required\n"
    ),
    "a parenthesis closed only on the following line": (
        "fix: a subject\n\nencode(document,\nindent=2)\n"
    ),
    "a bullet run into the parenthesised term": (
        "fix: a subject\n\n*array(allow_tabular(False))* is the change\n"
    ),
    "nested parens in the summary's own scope": "fix(toon(spec)): a subject\n",
    "no conventional-commit summary at all": "tidy up the encoder\n\nbody text\n",
    "an unparseable second conventional-commit section": (
        "fix: the first section\n\nbody\n\nfix: the second section\n\nf(g(h)) breaks it\n"
    ),
}

#: Messages that must keep working. Rich bodies are the point of this project's
#: history, so the guard is worth nothing if it also refuses them.
DEMO_ACCEPTED = {
    "the same phrase one word further along the line": (
        "fix(toon): a subject\n\nformats through `Decimal(repr(value))` inside the range\n"
    ),
    "a single parenthesised term at a line start": (
        "fix: a subject\n\n`Decimal(value)` is the shortest round-tripping form\n"
    ),
    "two flat parenthesised terms on one line": (
        "fix: a subject\n\nstars(rating) and unstars(value) agree in both directions\n"
    ),
    "an empty pair of parentheses": "fix: a subject\n\nitem.rate() clears a rating\n",
    "a markdown bullet, which has a space after the marker": (
        "fix: a subject\n\n- `array(allow_tabular=False)` is the change\n"
    ),
    "footers, trailers and a breaking-change note": (
        "feat(api)!: a subject\n\nbody text\n\nBREAKING CHANGE: the flag moved\nRefs: #10\n"
    ),
    "two conventional-commit sections, both parseable": (
        "fix(ci): the first section\n\nbody\n\nfix(toon): the second section\n\nmore body\n"
    ),
    "a rich body of the kind this fleet writes": (
        "fix(toon): satisfy a MUST the encoder was violating\n\n"
        "**Section 2 - canonical decimal form.** The spec makes decimal form mandatory\n"
        "for `0` and for `1e-6 <= |n| < 1e21`, so the encoder now formats through the\n"
        "shortest round-tripping digits, `Decimal(repr(value))`, inside that range and\n"
        "defers to `json.dumps` outside it, where an exponent is permitted.\n\n"
        "Verified against a built wheel, not only the checkout.\n"
    ),
}


def run_demo(engine, stream=None):
    """Prove the checker still rejects the bad shapes and still accepts the good."""
    stream = sys.stdout if stream is None else stream
    failures = []
    for label, message in DEMO_REJECTED.items():
        if not check(message, engine=engine):
            failures.append(f"accepted a message it must reject: {label}")
    for label, message in DEMO_ACCEPTED.items():
        problems = check(message, engine=engine)
        if problems:
            failures.append(f"rejected a message it must accept: {label} ({problems[0].error})")
    total = len(DEMO_REJECTED) + len(DEMO_ACCEPTED)
    if failures:
        print(f"commitcheck --demo: {len(failures)} of {total} cases wrong ({engine})", file=stream)
        for failure in failures:
            print(f"  {failure}", file=stream)
        return 1
    print(
        f"commitcheck --demo: {len(DEMO_REJECTED)} rejected, {len(DEMO_ACCEPTED)} accepted, "
        f"as expected ({engine} engine)",
        file=stream,
    )
    return 0


def list_rules(engine, stream=None):
    stream = sys.stdout if stream is None else stream
    print("commitcheck: what this refuses, and why\n", file=stream)
    print(__doc__.split("Usage:")[0].strip(), file=stream)
    print(f"\nengine in use: {engine}", file=stream)
    print(f"vendored parser: {VENDORED_PARSER.relative_to(REPO_ROOT)}", file=stream)
    print(f"  present: {'yes' if VENDORED_PARSER.exists() else 'no'}", file=stream)
    print(f"  node on PATH: {'yes' if node_available() else 'no'}", file=stream)
    print("\nknown-unparseable commits, exempt from --range and --since-release:", file=stream)
    if not KNOWN_UNPARSEABLE:
        print("  (none)", file=stream)
    for sha, reason in KNOWN_UNPARSEABLE.items():
        print(f"  {sha}", file=stream)
        print(f"    {reason}", file=stream)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="commitcheck.py",
        description="Reject a commit message release-please cannot parse.",
    )
    parser.add_argument("--commit-msg", metavar="PATH", help="check a commit message file")
    parser.add_argument("--message", metavar="TEXT", help="check a literal message")
    parser.add_argument("--range", metavar="A..B", help="check every commit message in a range")
    parser.add_argument(
        "--since-release",
        action="store_true",
        help="check every commit since the tag named by .release-please-manifest.json",
    )
    parser.add_argument("--rules", action="store_true", help="explain the rule and exit")
    parser.add_argument("--demo", action="store_true", help="self-test and exit")
    parser.add_argument(
        "--engine",
        choices=("auto", "node", "python"),
        default="auto",
        help="auto (default) runs the vendored upstream parser when node is available",
    )
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)

    engine = resolve_engine(args.engine)

    if args.rules:
        return list_rules(engine)
    if args.demo:
        return run_demo(engine)

    if args.since_release:
        return audit_range(release_range(args.root), engine=engine, root=args.root)
    if args.range:
        return audit_range(args.range, engine=engine, root=args.root)

    if args.commit_msg:
        message = Path(args.commit_msg).read_text(encoding="utf-8", errors="replace")
        label = "this commit message"
    elif args.message is not None:
        message = args.message
        label = "this message"
    else:
        parser.error("nothing to check: pass --commit-msg, --message, --range or --since-release")

    # A message is only what git will record: everything from the first comment
    # line onwards is stripped by git before the commit exists, and refusing a
    # commit for a parenthesis inside the instructions git itself wrote would be
    # a guard nobody could satisfy.
    message = "\n".join(line for line in message.splitlines() if not line.startswith("#"))

    problems = check(message, engine=engine)
    if not problems:
        return 0
    return report(problems, label)


if __name__ == "__main__":
    sys.exit(main())
