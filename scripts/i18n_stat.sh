#!/usr/bin/env bash
# Show translation-coverage stats per locale catalog.
#
# Usage:
#   bash scripts/i18n_stat.sh                  # all vehicles + common, default lang zh_CN
#   bash scripts/i18n_stat.sh copter
#   LANGS="zh_CN" bash scripts/i18n_stat.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEFAULT_TARGETS="common copter plane rover sub antennatracker dev planner planner2 ardupilot mavproxy blimp"
TARGETS="${*:-$DEFAULT_TARGETS}"
LANGS="${LANGS:-zh_CN}"

for t in $TARGETS; do
  for lang in $LANGS; do
    if [ -d "locale/$t/$lang/LC_MESSAGES" ]; then
      echo "=== $t / $lang ==="
      sphinx-intl stat -d "locale/$t" -l "$lang" | tail -20
    fi
  done
done
