#!/bin/sh
# public_stats_refresh.sh -- regenerate squire's honest public-stats blocks in README.md,
# docs/SAVINGS.md and the jsonholdings.com open-source card from the real ledger + session data,
# commit/push whatever changed, verify, then sync to the public mirror
# (github.com/jsonholdings/squire) the same way scripts/sync_to_mirror.py --apply already does by
# hand: it runs the mirror's own scrub_check + pytest before ever touching the real mirror, and
# this script never pushes if either fails.
#
# Owner directive 2026-09-14/15: keep the README, SAVINGS.md and jsonholdings.com stats honest
# and current on a schedule, not only when someone remembers to run the generator by hand.
# 2026-09-15: extended to also regenerate the README numbered headline, SAVINGS.md's two MEASURED
# tables + ESTIMATED row, and the site card's three <dl> stats (all from
# scripts/squire_report.py's full_history_stats(), never hand-typed), and moved from weekly to
# daily. See memory feedback_squire_public_stats_honest.md, feedback_keep_squire_stats_current.md.
#
# Run by squire-public-stats-refresh.service (systemd --user timer, daily). Never run as root.
# Any check below failing stops the script before anything further is committed or published.
set -eu

SQUIRE_SRC="$(cd "$(dirname "$0")/.." && pwd)"
MIRROR="$HOME/Projects/github-org/squire"
SITE_DIR="$HOME/Projects/business/jsonholdings/site"
SITE_FILE="$SITE_DIR/index.html"
RENDER_CHECK="$HOME/Projects/business/jsonholdings/infrastructure/render-check/render_check.py"

cd "$SQUIRE_SRC"
python3 scripts/squire_report.py --write README.md docs/SAVINGS.md

if ! git diff --quiet -- README.md docs/SAVINGS.md; then
    echo "[public-stats-refresh] README.md / docs/SAVINGS.md changed, committing"
    git add README.md docs/SAVINGS.md
    git commit -m "stats: refresh generated public-stats blocks (scripts/squire_report.py --write)"
    git push origin main
else
    echo "[public-stats-refresh] no change in squire source"
fi

# ---- site card (a separate repo, its own verification per jsonholdings/CLAUDE.md) ----
if python3 scripts/squire_report.py --check "$SITE_FILE" >/dev/null 2>&1; then
    echo "[public-stats-refresh] site card already current"
else
    echo "[public-stats-refresh] site card stale, regenerating"
    python3 scripts/squire_report.py --write "$SITE_FILE"

    echo "[public-stats-refresh] structural check: tag balance + JSON-LD parse"
    python3 - "$SITE_FILE" <<'PYEOF'
import sys, re, json
from html.parser import HTMLParser

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    html = f.read()

VOID = {"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}

class Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)
    def handle_startendtag(self, tag, attrs):
        pass
    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"mismatched </{tag}> (stack: {self.stack[-3:]})")
        else:
            self.stack.pop()

p = Balance()
p.feed(html)
if p.errors:
    print("[structural-check] TAG BALANCE FAILED:", p.errors[:5])
    sys.exit(1)
if p.stack:
    print("[structural-check] TAG BALANCE FAILED: unclosed tags remain:", p.stack)
    sys.exit(1)

m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
if not m:
    print("[structural-check] no JSON-LD block found")
    sys.exit(1)
try:
    json.loads(m.group(1))
except json.JSONDecodeError as e:
    print(f"[structural-check] JSON-LD FAILED TO PARSE: {e}")
    sys.exit(1)

print("[structural-check] OK: tags balanced, JSON-LD parses")
PYEOF

    echo "[public-stats-refresh] render_check.py --dir (1280/430, both themes)"
    python3 "$RENDER_CHECK" --dir "$SITE_DIR" --widths 1280 430

    cd "$SITE_DIR"
    if [ -n "$(git status --porcelain -- index.html)" ]; then
        git add index.html
        git commit -m "stats: refresh squire open-source card figures (generated)"
        git push origin main
    fi

    echo "[public-stats-refresh] live check via a Cloudflare edge"
    CF_IP="$(dig +short jsonholdings.com A | tail -1)"
    if [ -n "$CF_IP" ]; then
        curl -s --resolve "jsonholdings.com:443:$CF_IP" -A "Mozilla/5.0 (public-stats-refresh check)" \
            https://jsonholdings.com/ | grep -o '<dt>[^<]*</dt>' || echo "[public-stats-refresh] WARNING: could not read live card figures"
    else
        echo "[public-stats-refresh] WARNING: could not resolve a Cloudflare edge IP, skipping live check"
    fi
    cd "$SQUIRE_SRC"
fi

python3 scripts/scrub_check.py

if python3 scripts/sync_to_mirror.py --check; then
    echo "[public-stats-refresh] mirror already current"
    exit 0
fi

echo "[public-stats-refresh] mirror drifted, applying"
python3 scripts/sync_to_mirror.py --apply

cd "$MIRROR"
if [ -z "$(git status --porcelain)" ]; then
    echo "[public-stats-refresh] apply produced no changes, nothing to publish"
    exit 0
fi

# sync_to_mirror.py --apply only ever touches its own allow-listed paths (per squire/CLAUDE.md),
# so a plain -A here is bounded to that safe set, not an arbitrary "stage everything".
git add -A
git commit -m "stats: sync generated public-stats blocks from source"
git fetch origin main
git merge --ff-only origin/main
git push origin main
echo "[public-stats-refresh] mirror published"
