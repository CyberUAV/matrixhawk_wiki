#!/usr/bin/env python3
"""
Fix class-I lint defects: inline markup whose boundary character prevents
docutils from recognising it (e.g. a CJK letter glued to ``**strong**`` or a
role). Inserts a plain ASCII space at each invalid boundary — docutils then
recognises the span, and a space between CJK and Latin/markup matches common
Chinese typesetting practice anyway.

Reads defect records from an i18n_lint.py --json report, patches msgstr in
place, validates each file with msgfmt -c afterwards.

Usage:
  python3 scripts/i18n_lint.py --json /tmp/report.json locale/
  python3 scripts/i18n_fix_inline_boundaries.py /tmp/report.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n_lint import INLINE_SPAN_RES, OK_AFTER_RE, OK_BEFORE_RE  # noqa: E402

import polib  # noqa: E402


def fix_text(text: str) -> str:
    """Insert spaces at invalid inline-markup boundaries until stable."""
    for _ in range(8):  # spans can overlap after edits; iterate to fixpoint
        edits = []
        for rx in INLINE_SPAN_RES:
            for m in rx.finditer(text):
                before = text[m.start() - 1: m.start()]
                after = text[m.end(): m.end() + 1]
                if before and not OK_BEFORE_RE.fullmatch(before):
                    edits.append((m.start(), "before"))
                if after and not OK_AFTER_RE.fullmatch(after):
                    edits.append((m.end(), "after"))
        if not edits:
            return text
        for pos, _kind in sorted(set(edits), reverse=True):
            text = text[:pos] + " " + text[pos:]
    return text


def main():
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    by_po = defaultdict(list)
    for r in report:
        if r["cls"] == "I":
            by_po[r["po"]].append(r)

    n_fixed = 0
    for po_path, records in sorted(by_po.items()):
        po = polib.pofile(po_path)
        msgids = {r["msgid"] for r in records}
        changed = False
        for e in po:
            if e.msgid in msgids and e.msgstr:
                new = fix_text(e.msgstr)
                if new != e.msgstr:
                    e.msgstr = new
                    changed = True
                    n_fixed += 1
        if changed:
            po.save(po_path)
            subprocess.check_output(
                ["msgfmt", "-c", "-o", os.devnull, po_path],
                stderr=subprocess.STDOUT)
            print(f"fixed: {po_path}")
    print(f"{n_fixed} entries fixed across {len(by_po)} files")


if __name__ == "__main__":
    main()
