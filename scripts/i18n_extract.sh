#!/usr/bin/env bash
# Extract gettext .pot from each Sphinx vehicle project and update .po catalogs.
#
# Usage:
#   bash scripts/i18n_extract.sh                   # all vehicles
#   bash scripts/i18n_extract.sh copter            # one vehicle
#   LANGS="zh_CN ja_JP" bash scripts/i18n_extract.sh copter
#
# Output layout (per plan):
#   <vehicle>/build/gettext/**/*.pot
#   locale/<vehicle>/<lang>/LC_MESSAGES/**/*.po
#
# After extraction, call scripts/i18n_sync_common.py to move entries sourced
# from common/ into the shared locale/common/ catalog (avoids translating the
# same string once per vehicle).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEFAULT_VEHICLES="copter plane rover sub antennatracker dev planner planner2 ardupilot mavproxy blimp"
VEHICLES="${*:-$DEFAULT_VEHICLES}"
LANGS="${LANGS:-zh_CN}"

for v in $VEHICLES; do
  if [ ! -d "$v/source" ]; then
    echo "[skip] $v: no source/ dir"
    continue
  fi
  echo "[$v] extracting gettext .pot..."
  sphinx-build -q -b gettext "$v/source" "$v/build/gettext"

  mkdir -p "locale/$v"
  for lang in $LANGS; do
    echo "[$v] sphinx-intl update -> locale/$v/$lang"
    sphinx-intl update \
      -p "$v/build/gettext" \
      -l "$lang" \
      -d "locale/$v"
  done
done

if [ -f scripts/i18n_sync_common.py ] && [ -f locale/_common_manifest.json ]; then
  echo "Merging common-sourced entries via i18n_sync_common.py..."
  python3 scripts/i18n_sync_common.py --langs "$LANGS"
else
  echo "Note: skipped common-sync (run update.py once to produce locale/_common_manifest.json,"
  echo "      then re-run this script)."
fi

echo "Done. Check locale/<vehicle>/<lang>/LC_MESSAGES/ for .po files."
