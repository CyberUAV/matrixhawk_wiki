#!/usr/bin/env python3
"""
Generate the per-language landing pages from ONE template.

    frontend/template/index.template.html   (structure, {{key}} placeholders)
    frontend/template/strings.<lang>.json   (per-language copy)
        -> frontend/index.html              (en)
        -> frontend/index.zh_CN.html        (zh_CN; update.py deploys it as
                                             /zh/index.html per its
                                             index.<lang>.html convention)

Why: the two landing pages used to be maintained as separate hand-edited
HTML files. They drifted, and the zh variant's serializer once emitted a
bare `html` first line instead of `<!doctype html>` — no doctype means
quirks mode, which visibly broke the whole layout. One template makes that
class of bug impossible and keeps EN/zh structurally identical by
construction.

Usage:  python3 frontend/template/build_frontend.py
Exits non-zero if any {{placeholder}} is left unresolved.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent
TEMPLATE = HERE / "index.template.html"

# language -> output filename (update.py convention: index.<sphinx_code>.html)
OUTPUTS = {
    "en": FRONTEND / "index.html",
    "zh_CN": FRONTEND / "index.zh_CN.html",
}

PLACEHOLDER_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")


def main() -> int:
    template = TEMPLATE.read_text(encoding="utf-8")
    if not template.lstrip().lower().startswith("<!doctype html>"):
        print("FATAL: template lost its doctype", file=sys.stderr)
        return 1

    failed = False
    for lang, out_path in OUTPUTS.items():
        strings = json.loads(
            (HERE / f"strings.{lang}.json").read_text(encoding="utf-8"))

        html = PLACEHOLDER_RE.sub(
            lambda m: strings.get(m.group(1), m.group(0)), template)

        leftover = sorted(set(PLACEHOLDER_RE.findall(html)))
        if leftover:
            print(f"FATAL: {lang}: unresolved placeholders: {leftover}",
                  file=sys.stderr)
            failed = True
            continue

        out_path.write_text(html, encoding="utf-8")
        print(f"wrote {out_path.relative_to(FRONTEND.parent)}"
              f"  ({len(html)} bytes, {lang})")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
