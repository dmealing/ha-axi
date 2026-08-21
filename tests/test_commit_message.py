"""The guard that keeps an unreleasable commit message off `main`.

`scripts/commitcheck.py` answers one question -- would release-please read this
commit, or silently drop it? -- and the answer is only worth anything if it is
the same answer release-please's own parser gives. So this suite is built the
way `test_toon_conformance.py` is: the project's opinion is stated in its own
words *and* checked against a vendored copy of the upstream implementation.

Three things are pinned here, and they are not interchangeable:

* **The regression.** The real, byte-for-byte message of the commit that was
  lost, with the exact position the release workflow reported. A guard written
  from a description of a bug is a guard against the description. It is the
  *sibling* project's commit, because that is where the loss happened -- and
  beside it sits this repository's own commit describing the same fix, which
  parsed. The two differ by where one phrase sits on its line. That is the whole
  case for this guard here: nothing has been lost yet, and the reason is luck.
* **The corpus.** Shapes that must be refused and, just as important, the rich
  bodies this fleet writes that must keep working. A validator that quietly
  started refusing prose would be the more expensive failure, because the answer
  to it is to stop writing the prose.
* **The agreement.** Every corpus entry through both engines, compared on the
  verdict *and* on the reported line, column and token. The Python transcription
  stands in for the real parser wherever `node` is absent, and a stand-in that
  disagreed would be worse than a skip.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import commitcheck

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "vendor" / "conventional-commits-parser"
CHECKSUMS = VENDOR_ROOT / "checksums.txt"
MESSAGES = Path(__file__).parent / "fixtures" / "commit-messages"

#: The sibling project's commit that release-please dropped, and the error it
#: printed while exiting 0. Both are transcribed from that workflow run, not
#: reconstructed. The commit is not in this repository's history; the message is
#: vendored because the shape is what has to be pinned, not the provenance.
LOST_SHA = "41bcb73e6b1797bee245bea2cd4797460b5cdbb5"
LOST_ERROR = "unexpected token '(' at 13:14, valid tokens [)]"

#: This repository's own commit describing the same two TOON fixes. It parses,
#: and the only reason is that the phrase that broke the sibling sits one word
#: further along its line here.
LUCKY_SHA = "46c25f9"

#: Engines to run every shared assertion under. `node` is skipped rather than
#: failed where it is unavailable -- but CI installs it, so the agreement is
#: enforced on every push rather than only where somebody happens to have it.
ENGINES = ["python", "node"]

NODE = pytest.mark.skipif(not commitcheck.node_available(), reason="node is not available")


def engine_param(engine):
    return pytest.param(engine, marks=[NODE] if engine == "node" else [])


ENGINE_PARAMS = [engine_param(engine) for engine in ENGINES]


@pytest.fixture(scope="module")
def lost_message():
    return (MESSAGES / "sibling-41bcb73.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The regression: the message that was actually lost.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
def test_the_lost_commit_message_is_refused(lost_message, engine):
    problems = commitcheck.check(lost_message, engine=engine)
    assert problems, "the message that cost a release must not be accepted"
    assert len(problems) == 1
    assert str(problems[0].error) == LOST_ERROR


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
def test_the_lost_commit_message_is_located_where_the_workflow_said(lost_message, engine):
    problem = commitcheck.check(lost_message, engine=engine)[0]
    assert (problem.line, problem.column) == (13, 14)
    # Column 14 is the *second* parenthesis, the one the scope cannot contain.
    assert problem.source_line.startswith("`Decimal(repr(value))`")
    assert problem.source_line[problem.column - 1] == "("


def test_the_report_names_the_line_and_says_what_to_change(lost_message, capsys):
    problems = commitcheck.check(lost_message, engine="python")
    assert commitcheck.report(problems, "this commit message", stream=sys.stdout) == 1
    out = capsys.readouterr().out
    assert "line 13, column 14" in out
    assert "`Decimal(repr(value))`" in out
    # Advice a reader can act on without knowing the grammar: what to move, and
    # the fact that the prose itself is fine.
    assert "reflow" in out
    assert "fine mid-line" in out
    # And what the silence costs, since that is the part nobody sees.
    assert "still exit 0" in out


@pytest.fixture(scope="module")
def lucky_message():
    return (MESSAGES / "46c25f9.txt").read_text(encoding="utf-8")


def test_this_repositorys_own_message_about_the_same_fix_parses(lucky_message):
    """It does, and that is the point: it very nearly did not."""
    assert not commitcheck.check(lucky_message, engine="python")


def test_the_lucky_message_is_the_commit_that_is_actually_in_this_repository(lucky_message):
    """The near miss must stay the real message, not a retyped likeness.

    Skipped rather than failed where the object is unreachable: a shallow clone
    is a legitimate checkout, and this assertion is about provenance, not about
    whether the guard works.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%B", LUCKY_SHA],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("the commit is not reachable from this checkout")
    assert result.stdout == lucky_message


def test_the_two_messages_differ_only_in_where_the_phrase_sits(lost_message, lucky_message):
    """Both describe the same fix and both contain the same term.

    One begins a line with it and is unparseable; the other reaches it a few
    words in and is fine. Nothing about the prose, the punctuation or the
    vocabulary distinguishes them -- which is exactly why a person cannot be the
    control here.
    """
    term = "`Decimal(repr(value))`"
    assert term in lost_message
    assert term in lucky_message
    assert any(line.startswith(term) for line in lost_message.splitlines())
    assert not any(line.startswith(term) for line in lucky_message.splitlines())
    assert commitcheck.check(lost_message, engine="python")
    assert not commitcheck.check(lucky_message, engine="python")


def test_every_commit_in_this_repositorys_history_is_readable():
    """The claim the release audit makes, asserted rather than assumed.

    Skipped on a shallow clone, where the history is not there to check.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--first-parent", "-z", "--format=%H%n%B"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("the history is not reachable from this checkout")
    unreadable = []
    for record in result.stdout.split("\0"):
        if not record.strip():
            continue
        sha, _, body = record.partition("\n")
        if sha.strip() in commitcheck.KNOWN_UNPARSEABLE:
            continue
        if commitcheck.check(body, engine="python"):
            unreadable.append(sha.strip()[:12])
    assert not unreadable, f"release-please would drop: {unreadable}"


# ---------------------------------------------------------------------------
# The corpus: what must be refused, and what must keep working.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
@pytest.mark.parametrize("label", sorted(commitcheck.DEMO_REJECTED))
def test_every_unparseable_shape_is_refused(label, engine):
    assert commitcheck.check(commitcheck.DEMO_REJECTED[label], engine=engine), label


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
@pytest.mark.parametrize("label", sorted(commitcheck.DEMO_ACCEPTED))
def test_every_legitimate_shape_is_accepted(label, engine):
    problems = commitcheck.check(commitcheck.DEMO_ACCEPTED[label], engine=engine)
    assert not problems, f"{label}: {problems[0].error if problems else ''}"


def test_the_rule_is_the_interaction_and_not_any_one_ingredient():
    """Parentheses, backticks, dashes and position are each fine on their own.

    This is the assertion that stops the guard drifting into a ban on rich
    bodies. The failing shape is a line-initial token run into a parenthesis
    that does not close cleanly; every ingredient of it, alone, is legitimate.
    """
    body = "fix: a subject\n\n{}\n"
    for innocent in (
        "parentheses (like these) mid-line",
        "`backticks` around a term",
        "an em-dash - used as a dash",
        "call(argument) at the line start",
        "call() at the line start",
        "call(a) and call(b) at the line start",
    ):
        assert not commitcheck.check(body.format(innocent)), innocent
    assert commitcheck.check(body.format("call(nested(argument))")), "the interaction must fail"


def test_the_demo_is_a_self_test_that_can_fail(capsys):
    """`--demo` has to be able to report a wrong verdict, or it proves nothing."""
    assert commitcheck.run_demo("python", stream=sys.stdout) == 0
    original = commitcheck.DEMO_REJECTED.copy()
    try:
        commitcheck.DEMO_REJECTED["a message that parses fine"] = "fix: a subject\n"
        assert commitcheck.run_demo("python", stream=sys.stdout) == 1
    finally:
        commitcheck.DEMO_REJECTED.clear()
        commitcheck.DEMO_REJECTED.update(original)
    assert "accepted a message it must reject" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The agreement between the transcription and the parser it stands in for.
# ---------------------------------------------------------------------------


def _corpus():
    cases = {f"reject:{k}": v for k, v in commitcheck.DEMO_REJECTED.items()}
    cases.update({f"accept:{k}": v for k, v in commitcheck.DEMO_ACCEPTED.items()})
    cases["lost-commit"] = (MESSAGES / "sibling-41bcb73.txt").read_text(encoding="utf-8")
    cases["lucky-commit"] = (MESSAGES / "46c25f9.txt").read_text(encoding="utf-8")
    # Shapes with no verdict recorded anywhere else: the point of running them is
    # that the two engines must agree about whatever the answer turns out to be.
    cases["empty"] = ""
    cases["subject only"] = "fix: a subject"
    cases["subject and no blank line"] = "fix: a subject\nbody straight after"
    cases["crlf line endings"] = "fix: a subject\r\n\r\n`a(b(c))` at the line start\r\n"
    cases["a leading blank line"] = "\n\n\nfix: a subject\n\n`a(b(c))` here\n"
    cases["trailing whitespace only"] = "fix: a subject\n\nbody\n\n   \n"
    cases["a non-breaking space before the paren"] = "fix: a subject\n\ncall\u00a0(nested(x))\n"
    cases["nested commit block"] = (
        "chore: outer\n\nbody\n\nBEGIN_NESTED_COMMIT\nfix: inner\n\n`a(b(c))` here\n"
        "END_NESTED_COMMIT\n"
    )
    cases["a bare 'BREAKING CHANGE' body line"] = "fix: a subject\n\nBREAKING CHANGE: it moved\n"
    return cases


CORPUS = _corpus()


@NODE
@pytest.mark.parametrize("label", sorted(CORPUS))
def test_the_transcription_agrees_with_the_vendored_parser(label):
    """Same verdict, same position, same token -- for every case in the corpus.

    Compared field by field rather than on the rendered string, so a difference
    in where the failure was found cannot hide behind identical wording.
    """
    message = CORPUS[label]
    ported = commitcheck.check(message, engine="python")
    upstream = commitcheck.check(message, engine="node")
    assert [(p.line, p.column, p.error.found, p.error.valid, str(p.error)) for p in ported] == [
        (p.line, p.column, p.error.found, p.error.valid, str(p.error)) for p in upstream
    ], label


@NODE
def test_the_corpus_actually_exercises_both_outcomes():
    """A corpus that only passes proves the engines agree about nothing."""
    verdicts = {bool(commitcheck.check(m, engine="node")) for m in CORPUS.values()}
    assert verdicts == {True, False}


# ---------------------------------------------------------------------------
# The vendored parser itself.
# ---------------------------------------------------------------------------


def test_the_vendored_parser_is_unmodified():
    """A file edited to make the transcription look right is not upstream's opinion."""
    recorded = {}
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        recorded[name] = digest
    assert recorded, "checksums.txt is empty"
    for name, digest in recorded.items():
        actual = hashlib.sha256((VENDOR_ROOT / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} differs from the vendored copy"


def test_the_vendored_parser_needs_nothing_outside_its_own_directory():
    """`node` alone has to run it -- no `npm install`, no network, no lockfile."""
    for source in sorted((VENDOR_ROOT / "lib").glob("*.js")):
        for line in source.read_text(encoding="utf-8").splitlines():
            if "require(" in line:
                assert "require('./" in line, f"{source.name}: {line.strip()}"


def test_the_number_of_ways_a_message_can_be_refused_is_pinned():
    """Four `throw`s in the grammar, and the transcription was written against those.

    A refreshed parser with a fifth has grown a rejection this file has never
    seen, and the port has to be re-read rather than assumed still faithful.
    """
    grammar = (VENDOR_ROOT / "lib" / "parser.js").read_text(encoding="utf-8")
    throws = [line for line in grammar.splitlines() if line.strip().startswith("throw ")]
    assert len(throws) == commitcheck.THROW_SITES
    # And the transcription cites every one of them by upstream line number.
    ported = Path(commitcheck.__file__).read_text(encoding="utf-8")
    for number in (17, 30, 48, 177):
        assert f"lib/parser.js:{number}" in ported


# ---------------------------------------------------------------------------
# The release audit, and the allowance that keeps it honest.
# ---------------------------------------------------------------------------


def _repo(tmp_path, commits):
    """A throwaway repository with one commit per message, oldest first."""
    root = tmp_path / "repo"
    root.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "nobody@example.com")
    run("config", "user.name", "Nobody")
    (root / ".release-please-manifest.json").write_text('{\n  ".": "1.0.0"\n}\n')
    run("add", ".release-please-manifest.json")
    run("commit", "-q", "-m", "chore: release 1.0.0")
    run("tag", "v1.0.0")
    for message in commits:
        run("commit", "-q", "--allow-empty", "-m", message)
    return root


def test_the_audit_passes_when_every_commit_can_be_read(tmp_path, capsys):
    root = _repo(tmp_path, ["fix: a real fix\n\nbody text\n"])
    assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 0
    assert "silently dropped:              0" in capsys.readouterr().out


def test_the_audit_fails_when_a_commit_would_be_dropped(tmp_path, capsys):
    root = _repo(tmp_path, ["fix: a real fix\n\n`a(b(c))` at the line start\n"])
    assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 1
    out = capsys.readouterr().out
    assert "silently dropped:              1" in out


def test_zero_considered_with_commits_waiting_is_called_out_by_name(tmp_path, capsys):
    """The exact state that exited 0 and shipped nothing.

    A run that considers zero commits while commits are waiting is not the same
    as a run with nothing to do, and the whole failure was that the two looked
    identical.
    """
    root = _repo(tmp_path, ["fix: a real fix\n\n`a(b(c))` at the line start\n"])
    assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 1
    out = capsys.readouterr().out
    assert "release-please would consider: 0" in out
    assert "ZERO of these commits and still" in out


def test_an_empty_range_is_not_reported_as_a_failure(tmp_path, capsys):
    """The healthy no-op still has to pass, or the guard gets switched off."""
    root = _repo(tmp_path, [])
    assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 0
    assert "ZERO" not in capsys.readouterr().out


def test_since_release_reads_the_range_from_the_manifest(tmp_path):
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    assert commitcheck.released_version(root) == "1.0.0"
    assert len(commitcheck.commits_since("v1.0.0..HEAD", root=root)) == 1


@pytest.mark.parametrize("sha", sorted(commitcheck.KNOWN_UNPARSEABLE))
def test_every_known_unparseable_entry_is_still_earning_its_place(sha):
    """An allowance whose commit now parses is covering something else.

    Same discipline as `PATH_ALLOWANCES` in the leak scanner: the entry has to
    keep being true, or it stops being an exemption and becomes a blind spot.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%B", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("the commit is not reachable from this checkout")
    assert commitcheck.check(result.stdout, engine="python"), (
        f"{sha[:12]} parses now -- delete the allowance rather than leaving it"
    )
    assert commitcheck.KNOWN_UNPARSEABLE[sha].strip(), "an allowance must say why it is there"


def test_the_allowance_is_matched_on_the_full_sha(tmp_path):
    """A prefix is not an identifier: two commits can share one.

    The audit looks the SHA up in a dictionary, so this is a statement about
    that choice rather than a bug that was found -- but the leak scanner's
    trailing-match defect was exactly this shape, one layer down.
    """
    root = _repo(tmp_path, ["fix: a real fix\n\n`a(b(c))` at the line start\n"])
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    original = commitcheck.KNOWN_UNPARSEABLE.copy()
    try:
        commitcheck.KNOWN_UNPARSEABLE[head[:12]] = "a prefix, which must not exempt anything"
        assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 1
        commitcheck.KNOWN_UNPARSEABLE[head] = "the full SHA, which must"
        assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 0
    finally:
        commitcheck.KNOWN_UNPARSEABLE.clear()
        commitcheck.KNOWN_UNPARSEABLE.update(original)


def test_the_rules_output_shows_the_allowances(capsys):
    """An exemption nobody can see is one nobody re-examines."""
    assert commitcheck.list_rules("python", stream=sys.stdout) == 0
    out = capsys.readouterr().out
    assert "known-unparseable commits" in out
    for sha in commitcheck.KNOWN_UNPARSEABLE:
        assert sha in out
    if not commitcheck.KNOWN_UNPARSEABLE:
        assert "(none)" in out


# ---------------------------------------------------------------------------
# The hook, end to end.
# ---------------------------------------------------------------------------


def test_the_commit_msg_hook_runs_this_check():
    """Wired, not merely available. An unwired guard reads exactly like a passing one."""
    hook = (REPO_ROOT / ".githooks" / "commit-msg").read_text(encoding="utf-8")
    assert "commitcheck.py" in hook
    assert "--commit-msg" in hook


def test_the_script_exits_non_zero_on_the_lost_message(tmp_path):
    target = tmp_path / "COMMIT_EDITMSG"
    target.write_bytes((MESSAGES / "sibling-41bcb73.txt").read_bytes())
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "commitcheck.py"),
            "--commit-msg",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert LOST_ERROR in result.stderr


def test_git_comment_lines_are_not_part_of_the_message(tmp_path):
    """git strips them before the commit exists, so refusing one is unsatisfiable.

    The template git writes into `COMMIT_EDITMSG` contains parentheses, and a
    guard that failed on the instructions it was given would be uncloseable.
    """
    message = "fix: a subject\n\nbody\n\n# Please enter the commit message (or leave it empty).\n"
    target = tmp_path / "COMMIT_EDITMSG"
    target.write_text(message, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "commitcheck.py"),
            "--commit-msg",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
