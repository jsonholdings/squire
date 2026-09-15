#!/bin/sh
# weekly_public_stats_refresh.sh -- regenerate squire's honest public-stats block in README.md
# and docs/SAVINGS.md from the real ledger, commit if it changed, then sync to the public
# mirror (github.com/jsonholdings/squire) the same way scripts/sync_to_mirror.py --apply
# already does by hand: it runs the mirror's own scrub_check + pytest before ever touching
# the real mirror, and this script never pushes if either fails.
#
# Owner directive 2026-09-14/15: keep the README and jsonholdings.com stats honest and
# current on a schedule, not only when someone remembers to run the generator by hand.
# See memory feedback_squire_public_stats_honest.md and TODO-2026-09-12.md SQUIRE-PUBLIC-STATS.
#
# Run by squire-public-stats-refresh.service (systemd --user timer, weekly). Never run as root.
set -eu

SQUIRE_SRC="$(cd "$(dirname "$0")/.." && pwd)"
MIRROR="$HOME/Projects/github-org/squire"

cd "$SQUIRE_SRC"
python3 scripts/squire_report.py --write README.md docs/SAVINGS.md

if ! git diff --quiet -- README.md docs/SAVINGS.md; then
    echo "[weekly-stats-refresh] README.md / docs/SAVINGS.md changed, committing"
    git add README.md docs/SAVINGS.md
    git commit -m "weekly: refresh generated public-stats block (scripts/squire_report.py --write)"
    git push origin main
else
    echo "[weekly-stats-refresh] no change in source"
fi

python3 scripts/scrub_check.py

if python3 scripts/sync_to_mirror.py --check; then
    echo "[weekly-stats-refresh] mirror already current"
    exit 0
fi

echo "[weekly-stats-refresh] mirror drifted, applying"
python3 scripts/sync_to_mirror.py --apply

cd "$MIRROR"
if [ -z "$(git status --porcelain)" ]; then
    echo "[weekly-stats-refresh] apply produced no changes, nothing to publish"
    exit 0
fi

# sync_to_mirror.py --apply only ever touches its own allow-listed paths (per squire/CLAUDE.md),
# so a plain -A here is bounded to that safe set, not an arbitrary "stage everything".
git add -A
git commit -m "weekly: sync generated public-stats block from source"
git fetch origin main
git merge --ff-only origin/main
git push origin main
echo "[weekly-stats-refresh] mirror published"
