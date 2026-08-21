#!/usr/bin/env sh
# Point this repository's hooks at the tracked .githooks directory.
#
# Tracked hooks are the point: a hook that lives only in one clone's .git
# directory protects only that clone.
set -e

root=$(git rev-parse --show-toplevel)
git -C "$root" config core.hooksPath .githooks
echo "hooks: core.hooksPath set to .githooks"
echo "check: run scripts/leakcheck.py --demo to confirm the scanner works"
echo "check: run scripts/commitcheck.py --demo to confirm the message check works"
