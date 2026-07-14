#!/bin/zsh
# Build the wiki (both languages by default) and push the result straight
# to the MT6000 router's USB drive for LAN-wide access testing.
#
#   scripts/build_deploy_mt6000.sh              # full bilingual --fast build + deploy
#   scripts/build_deploy_mt6000.sh --site plane --languages zh_CN
#                                               # any update.py args pass through
#
# Deploy target / URL: http://192.168.8.1:8642/zh/  (see deploy_mt6000.sh)
set -e
cd "$(dirname "$0")/.."

PY=/Users/bzuav/.pyenv/versions/3.11.7/bin/python3
SITE="$HOME/SynologyDrive/GitHub/matrixhawk_site"

if [ $# -gt 0 ]; then
    "$PY" update.py "$@" --destdir "$SITE"
else
    "$PY" update.py --languages en,zh_CN --fast --destdir "$SITE"
fi

echo "== build done, deploying to MT6000 =="
exec scripts/deploy_mt6000.sh "$SITE"
