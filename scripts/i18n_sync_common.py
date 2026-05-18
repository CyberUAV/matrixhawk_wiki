#!/usr/bin/env python3
"""
Move .po entries sourced from common/ into the shared locale/common/ catalog.

Background: update.py's [copywiki] step physically copies common/source/docs/X.rst
into each <vehicle>/source/docs/X.rst. Sphinx then sees foo.rst as belonging to
the vehicle project and emits a per-vehicle .po. Without this script translators
would have to translate the same string once per vehicle.

Two ways to identify "common-sourced" .po files:
  1) Preferred: read locale/_common_manifest.json written by update.py during
     [copywiki] copy. Format: {"<vehicle>": ["docs/foo", "docs/bar/baz", ...]}
  2) Fallback: any .po path under locale/<vehicle>/<lang>/LC_MESSAGES/<docpath>
     whose corresponding .rst exists at common/source/<docpath>.rst is treated
     as common-sourced. Used when manifest isn't available yet.

Action: for each (vehicle, lang, common_docpath):
  src = locale/<vehicle>/<lang>/LC_MESSAGES/<common_docpath>.po
  dst = locale/common/<lang>/LC_MESSAGES/<common_docpath>.po
  - if dst does not exist: move src -> dst
  - else: merge src entries into dst (union of msgid; keep existing msgstr if
    present, otherwise take src's msgstr), then remove src.

Run after `bash scripts/i18n_extract.sh`. Idempotent.

Usage:
  python3 scripts/i18n_sync_common.py --langs zh_CN
  python3 scripts/i18n_sync_common.py --langs "zh_CN ja_JP"
  python3 scripts/i18n_sync_common.py --dry-run
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    import polib
except ImportError:
    sys.exit("polib not installed; run `pip install polib` (also in requirements.txt).")

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALE_ROOT = REPO_ROOT / "locale"
COMMON_RST_ROOT = REPO_ROOT / "common" / "source"
MANIFEST_PATH = LOCALE_ROOT / "_common_manifest.json"

VEHICLES = ['copter', 'plane', 'rover', 'sub', 'antennatracker',
            'dev', 'planner', 'planner2', 'ardupilot', 'mavproxy', 'blimp']


def load_manifest():
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return None


def infer_common_docpaths(vehicle: str, lang: str):
    """Fallback: scan vehicle .po files; treat as common-sourced if
    common/source/<docpath>.rst exists."""
    vlocale = LOCALE_ROOT / vehicle / lang / "LC_MESSAGES"
    if not vlocale.exists():
        return []
    out = []
    for po in vlocale.rglob("*.po"):
        rel = po.relative_to(vlocale).with_suffix("")  # e.g. docs/foo
        rst = COMMON_RST_ROOT / rel.with_suffix(".rst")
        if rst.exists():
            out.append(str(rel).replace("\\", "/"))
    return out


def merge_into(src_po: polib.POFile, dst_path: Path):
    """Merge src into dst, preferring dst's existing msgstr if non-empty."""
    if dst_path.exists():
        dst = polib.pofile(str(dst_path))
        existing = {e.msgid: e for e in dst}
        for e in src_po:
            if e.msgid in existing:
                if not existing[e.msgid].msgstr and e.msgstr:
                    existing[e.msgid].msgstr = e.msgstr
                # union occurrences for traceability
                merged = sorted(set(existing[e.msgid].occurrences) | set(e.occurrences))
                existing[e.msgid].occurrences = merged
            else:
                dst.append(e)
        dst.save(str(dst_path))
    else:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_po.save(str(dst_path))


def sync_one(vehicle: str, lang: str, common_docpaths, dry_run: bool):
    moved = 0
    for docpath in common_docpaths:
        src = LOCALE_ROOT / vehicle / lang / "LC_MESSAGES" / f"{docpath}.po"
        dst = LOCALE_ROOT / "common" / lang / "LC_MESSAGES" / f"{docpath}.po"
        if not src.exists():
            continue
        if dry_run:
            print(f"  [dry] {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}")
            moved += 1
            continue
        src_po = polib.pofile(str(src))
        merge_into(src_po, dst)
        src.unlink()
        moved += 1
    return moved


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--langs", default="zh_CN",
                    help="Space-separated language codes (default: zh_CN)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = load_manifest()
    if manifest:
        print(f"Using manifest at {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    else:
        print("No manifest found; falling back to common/source/ existence check.")
        print("(Manifest will be written by update.py once Phase 2 is implemented.)")

    langs = args.langs.split()
    total = 0
    for lang in langs:
        for vehicle in VEHICLES:
            if manifest is not None:
                docpaths = manifest.get(vehicle, [])
            else:
                docpaths = infer_common_docpaths(vehicle, lang)
            if not docpaths:
                continue
            n = sync_one(vehicle, lang, docpaths, args.dry_run)
            if n:
                print(f"  [{lang}] {vehicle}: {n} .po moved to locale/common/")
                total += n
    print(f"Total moved: {total}")


if __name__ == "__main__":
    main()
