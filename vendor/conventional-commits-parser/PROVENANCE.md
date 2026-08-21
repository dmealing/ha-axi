# Vendored conventional-commits parser

`lib/` is a byte-for-byte copy of the four dependency-free modules of
`@conventional-commits/parser` — the reference implementation of the Conventional Commits
grammar, and **the parser release-please actually runs**.

| | |
| --- | --- |
| Upstream | <https://github.com/conventional-commits/parser> |
| Package | `@conventional-commits/parser` |
| Version | 0.4.1 (the only version ever published as latest; release-please 17.3.0 depends on `^0.4.1`) |
| Tarball | <https://registry.npmjs.org/@conventional-commits/parser/-/parser-0.4.1.tgz> |
| Tarball integrity | `sha512-H2ZmUVt6q+KBccXfMBhbBF14NlANeqHTXL4qCL6QGbMzrc4HDXyzWuxPxPNbz71f/5UkR5DrycP5VO9u7crahg==` |
| Upstream path | `lib/` inside the published tarball's `package/` directory |
| Licence | ISC — the copy here is `LICENSE`, from the same tarball |

## Why a copy, and why only four files

`scripts/commitcheck.py` answers one question: **would release-please see this commit, or drop
it?** A second opinion that is a re-derivation of the first is not one, so the answer has to come
from upstream's own code rather than from a description of it. The four files here are the whole
parser and they `require` nothing outside this directory, so `node` alone runs them — no
`npm install`, no network, no lockfile.

`lib/utils.js` and `index.js` are deliberately **not** vendored. `utils.js` is
`toConventionalChangelogFormat`, which needs two `unist` packages, and release-please does not use
it: `release-please/build/src/commit.js` carries its own copy. It also contains no `throw`, and
neither does `conventional-commits-filter`, so **every way a commit message can be rejected is one
of the four `throw` statements in `lib/parser.js`** — lines 17, 30, 48 and 177 of the vendored
copy. That is what makes this small set sufficient rather than merely convenient.

## What runs it

- `scripts/commitcheck.py --engine node` runs this copy directly. `--engine auto`, the default and
  what the hooks use, picks it whenever `node` is on `PATH`.
- `tests/test_commit_message.py` runs the whole message corpus through **both** engines and asserts
  they agree on the verdict *and* on the reported line, column and text. That is what lets the
  Python transcription stand in on a machine with no `node`, and it is what would catch a refresh
  that changed the grammar.
- `checksums.txt` records the SHA-256 of each vendored file and the same suite asserts they still
  match. A file edited to make the transcription look correct is no longer upstream's opinion, and
  the edit has to be visible rather than silent.

## Refreshing

```sh
npm pack @conventional-commits/parser@<version>            # into a scratch directory
tar -xzf conventional-commits-parser-<version>.tgz         # extracts to package/
cp package/lib/{parser,scanner,type-checks,codes}.js vendor/conventional-commits-parser/lib/
cp package/LICENSE.txt vendor/conventional-commits-parser/LICENSE
(cd vendor/conventional-commits-parser && sha256sum lib/*.js LICENSE) > vendor/conventional-commits-parser/checksums.txt
pytest tests/test_commit_message.py
```

Then update the table above, and re-read `THROW_SITES` in `scripts/commitcheck.py`: the count is
asserted, so a version that added or removed a `throw` fails the suite instead of silently leaving
the transcription behind. A grammar change belongs in its own commit, separate from any change to
`commitcheck.py` made to satisfy it.

Confirm the version release-please pins before refreshing — the parser this repository checks
against is worth nothing if it is not the one the release workflow runs:

```sh
npm view release-please@<version> dependencies.@conventional-commits/parser
```
