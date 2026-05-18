#!/usr/bin/env bash
# Compile .po -> .mo for every locale catalog so sphinx-build can consume them.
#
# Each vehicle's conf.py sets:
#   locale_dirs = ['../../locale/<vehicle>/', '../../locale/common/']
# Sphinx looks up <locale_dir>/<lang>/LC_MESSAGES/<docname>.mo at build time.
# We compile both per-vehicle and shared 'common' .mo so the fallback works.
#
# Run this after editing .po and before sphinx-build / update.py.
# Usage:
#   bash scripts/i18n_build.sh                # all locales found under locale/
#   LANGS="zh_CN" bash scripts/i18n_build.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LANGS="${LANGS:-zh_CN}"

if [ ! -d locale ]; then
  echo "locale/ not found; run scripts/i18n_extract.sh first."
  exit 1
fi

for d in locale/*/; do
  name="$(basename "$d")"
  [ "$name" = "_common_manifest.json" ] && continue
  for lang in $LANGS; do
    if [ -d "$d$lang/LC_MESSAGES" ]; then
      echo "[$name] compile .mo for $lang"
      sphinx-intl build -d "$d" -l "$lang"
    fi
  done
done

echo "Done. Sphinx will pick up .mo automatically via locale_dirs fallback."
