#!/usr/bin/env python3
"""
i18n_harmonize.py — cross-catalog translation consistency.

The common-*.po catalogs are duplicated per vehicle (11 copies), and the
AI pretranslation pass ran per vehicle, so the same English source string
accumulated up to 17 stylistic variants (若/如果, spacing around Latin,
etc.). This tool votes: for every msgid that appears with more than one
distinct msgstr anywhere under locale/, the most frequent variant wins
(ties: the longest), and every catalog is rewritten to use it.

Only prose entries (len(msgid) >= --min-len, default 40) are harmonized;
short strings like "Overview" could legitimately need context-specific
renderings, though in practice they rarely differ.

Term normalization: --terms applies the project glossary's canonical
Chinese terms (e.g. 故障保护→失控保护) to entries whose *English* source
contains the matching term, so unrelated uses of the minority word are
never touched.

Usage:
  python3 scripts/i18n_harmonize.py --dry-run locale/
  python3 scripts/i18n_harmonize.py locale/            # harmonize votes
  python3 scripts/i18n_harmonize.py --terms locale/    # + term normalization
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import polib
except ImportError:
    sys.exit("polib not installed; pip install polib")

# canonical term -> regex of minority variants, gated on English source match
TERM_RULES = [
    # (english_gate_regex, minority_zh_regex, canonical_zh)
    (r"\bfail[- ]?safe\b", r"故障保护|失效保护|故障安全", "失控保护"),
    (r"\bservo", r"伺服电机", "舵机"),
    (r"\bservo", r"伺服(?!电机)", "舵机"),
    (r"\breceiver", r"接收器", "接收机"),
    (r"\bvibration", r"振动", "震动"),
    (r"\bautopilot", r"自动驾驶仪", "飞控"),
    (r"\btelemetry", r"遥测", "数传"),
]


def collect(po_files):
    votes = defaultdict(Counter)
    for po_path in po_files:
        po = polib.pofile(str(po_path))
        for e in po:
            if e.msgid and e.msgstr and not e.obsolete:
                votes[e.msgid][e.msgstr] += 1
    return votes


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--min-len", type=int, default=40)
    ap.add_argument("--terms", action="store_true",
                    help="also apply glossary term normalization")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    po_files = []
    for t in args.targets:
        p = Path(t)
        po_files.extend(sorted(p.rglob("*.po")) if p.is_dir() else [p])

    votes = collect(po_files)
    canonical = {}
    for msgid, counter in votes.items():
        if len(counter) > 1 and len(msgid) >= args.min_len:
            best = max(counter.items(), key=lambda kv: (kv[1], len(kv[0])))
            canonical[msgid] = best[0]
    print(f"{len(canonical)} msgids have competing variants (min-len {args.min_len})")

    term_rules = [(re.compile(g, re.I), re.compile(v), c)
                  for g, v, c in TERM_RULES] if args.terms else []

    n_harm = n_term = 0
    for po_path in po_files:
        po = polib.pofile(str(po_path))
        changed = False
        for e in po:
            if not e.msgid or not e.msgstr or e.obsolete:
                continue
            if e.msgid in canonical and e.msgstr != canonical[e.msgid]:
                e.msgstr = canonical[e.msgid]
                n_harm += 1
                changed = True
            for gate, minority, canon in term_rules:
                if gate.search(e.msgid) and minority.search(e.msgstr):
                    e.msgstr = minority.sub(canon, e.msgstr)
                    n_term += 1
                    changed = True
        if changed and not args.dry_run:
            po.save(str(po_path))
            subprocess.check_output(
                ["msgfmt", "-c", "-o", os.devnull, str(po_path)],
                stderr=subprocess.STDOUT)
    tag = " (dry-run)" if args.dry_run else ""
    print(f"harmonized {n_harm} entries, term-normalized {n_term} entries{tag}")


if __name__ == "__main__":
    main()
