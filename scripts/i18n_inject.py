#!/usr/bin/env python3
"""
i18n_inject.py — apply reviewed translations to every matching .po entry.

Input JSON: [{"msgid": "...", "zh": "..."}, ...]
For each item, every entry under the target locale tree whose msgid matches
gets msgstr = zh and its fuzzy flag cleared.

Safety gates per item:
  - check_entry(msgid, zh) from i18n_lint must report zero defects,
    otherwise the item is rejected (the bad draft stays in the queue);
  - every touched file must pass msgfmt -c afterwards.

Usage:
  python3 scripts/i18n_inject.py translations.json [more.json ...] [--root locale/]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n_lint import check_entry

try:
    import polib
except ImportError:
    sys.exit("polib not installed; pip install polib")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("json_files", nargs="+")
    ap.add_argument("--root", default="locale/")
    args = ap.parse_args()

    items = []
    for jf in args.json_files:
        items.extend(json.loads(Path(jf).read_text(encoding="utf-8")))

    by_msgid, rejected = {}, []
    for it in items:
        defects = check_entry(it["msgid"], it["zh"])
        if defects:
            rejected.append((it["msgid"][:70], [d["cls"] for d in defects]))
            continue
        by_msgid[it["msgid"]] = it["zh"]

    if rejected:
        print(f"REJECTED {len(rejected)} items (marker defects remain):")
        for mid, cls in rejected:
            print(f"  {cls} {mid!r}")

    n_applied, files_touched = 0, 0
    for po_path in sorted(Path(args.root).rglob("*.po")):
        po = polib.pofile(str(po_path))
        dirty = False
        for e in po:
            if e.obsolete or e.msgid not in by_msgid:
                continue
            zh = by_msgid[e.msgid]
            if e.msgstr == zh and "fuzzy" not in e.flags:
                continue
            e.msgstr = zh
            if "fuzzy" in e.flags:
                e.flags.remove("fuzzy")
            dirty = True
            n_applied += 1
        if dirty:
            po.save(str(po_path))
            files_touched += 1
            try:
                subprocess.check_output(
                    ["msgfmt", "-c", "-o", os.devnull, str(po_path)],
                    stderr=subprocess.STDOUT)
            except FileNotFoundError:
                pass
            except subprocess.CalledProcessError as err:
                sys.exit(f"msgfmt failed on {po_path}:\n{err.output.decode()[:500]}")

    print(f"applied {n_applied} entry updates across {files_touched} files "
          f"({len(by_msgid)} unique msgids accepted)")
    sys.exit(1 if rejected else 0)


if __name__ == "__main__":
    main()
