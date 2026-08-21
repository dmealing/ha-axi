# Vendored TOON conformance fixtures

`encode/` is a byte-for-byte copy of the official, language-agnostic TOON test fixtures.

| | |
| --- | --- |
| Upstream | <https://github.com/toon-format/spec> |
| Path | `tests/fixtures/encode/` |
| Spec version | 4.1.1 (SPEC.md v4.1, released 2026-08-05) |
| Commit | `62f16b369408180f1faf1cba7da1b46d1f336f12` |
| Licence | MIT — see `LICENSE` in this directory, copied from the same commit |

`tests/test_toon_conformance.py` runs every case in `encode/` against `ha_axi.toon.encode` and
fails the suite if any of them regresses. `checksums.txt` records the SHA-256 of each vendored
file, and the same test asserts they still match: a fixture edited to make a failing encoder pass
is no longer the specification's opinion, and the edit must be visible rather than silent.

## What is not vendored, and why

- **`decode/`** — `ha_axi.toon` is an encoder only. Vendoring decode fixtures would add 14 files
  that nothing can run.
- **§3 host-type normalisation** (NaN, ±Infinity, host `Date`/`Set`/`Map`/`BigInt`) — upstream
  states this is deliberately outside the JSON fixtures, because the fixture format cannot express
  a non-JSON encode input. `tests/test_toon.py` covers it in Python instead.

## One naming difference, in the option, not the output

The fixtures spell the indentation option `indentSize`; this encoder's keyword argument is
`indent`. `test_toon_conformance.py` maps one to the other in a single documented place. That is a
difference in the encoder's API surface (spec §13), not in a single byte it emits — and every
fixture that exercises a non-default indent passes through the mapping.

## Refreshing

```sh
git clone --depth 1 https://github.com/toon-format/spec.git       # into a scratch directory
cp <clone>/tests/fixtures/encode/*.json tests/fixtures/toon-spec/encode/
cp <clone>/LICENSE tests/fixtures/toon-spec/LICENSE
(cd tests/fixtures/toon-spec/encode && sha256sum *.json) > tests/fixtures/toon-spec/checksums.txt
pytest tests/test_toon_conformance.py
```

Then update the table above with the new commit and version, and update `CASE_COUNT` in
`tests/test_toon_conformance.py` if upstream added cases — it is the only place the case count is
written, and the prose in the README and AGENTS.md deliberately stays number-free. A refresh that
changes an expected output is a specification change and belongs in its own commit, separate from
any encoder change made to satisfy it.

Re-vendoring rewrites `checksums.txt` in the same commit, so no automated gate compares the new
content against the old: re-read what the `PATH_ALLOWANCES` entry in `scripts/leakcheck.py` now
covers before committing. The suite does pin the shapes an entry exempts — a refresh that changes
them fails `test_every_path_allowance_is_still_earning_its_place` — but only a person can confirm
a new shape still names nobody and reaches nothing.
