"""Inject translations into .po files by merging two JSON tables.

Inputs:
  arg1: path to extract JSON ([{po, idx, id, en}, ...])
  arg2: path to translation JSON ([{id, zh}, ...])
  --no-fuzzy : do NOT mark entries as fuzzy (use when translations are already audited)

For each (po, idx) in the extract, find matching id in translations, write msgstr,
mark entry fuzzy (so reviewers can see it's a draft). Then save each po and
run `msgfmt -c` on it; refuse to overwrite if validation fails.

Usage:
  python3 scripts/_po_inject_translations.py /tmp/po_untranslated.json /tmp/po_translations_zh_CN.json
"""
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import polib


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    no_fuzzy = "--no-fuzzy" in sys.argv
    extract_path, trans_path = args[0], args[1]
    extract = json.load(open(extract_path))
    trans = {row["id"]: row["zh"] for row in json.load(open(trans_path))}

    by_po = defaultdict(list)
    for row in extract:
        by_po[row["po"]].append(row)

    total_written = 0
    failed_files = []
    for po_path, rows in by_po.items():
        po = polib.pofile(po_path)
        wrote = 0
        for row in rows:
            zh = trans.get(row["id"])
            if not zh:
                continue
            entry = po[row["idx"]]
            if entry.msgid != row["en"]:
                print(f"  WARN: msgid drift in {po_path}#{row['idx']}; skipping", file=sys.stderr)
                continue
            entry.msgstr = zh
            if not no_fuzzy and "fuzzy" not in entry.flags:
                entry.flags.append("fuzzy")
            wrote += 1
        if not wrote:
            continue
        tmp = po_path + ".tmp"
        po.save(tmp)
        # Validate with msgfmt if available; otherwise rely on polib's parse
        # round-trip as a weaker check.
        try:
            subprocess.check_output(["msgfmt", "-c", "-o", os.devnull, tmp],
                                    stderr=subprocess.STDOUT)
        except FileNotFoundError:
            try:
                polib.pofile(tmp)
            except Exception as e:
                print(f"  polib re-parse FAILED on {po_path}: {e}", file=sys.stderr)
                failed_files.append(po_path)
                continue
        except subprocess.CalledProcessError as e:
            print(f"  msgfmt FAILED on {po_path}: {e.output.decode()}", file=sys.stderr)
            failed_files.append(po_path)
            continue
        shutil.move(tmp, po_path)
        print(f"  {po_path}: wrote {wrote} entries")
        total_written += wrote

    print(f"Total: {total_written} entries injected"
          f"{' (' + str(len(failed_files)) + ' files failed)' if failed_files else ''}")
    if failed_files:
        sys.exit(1)


if __name__ == "__main__":
    main()
