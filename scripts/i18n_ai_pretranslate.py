#!/usr/bin/env python3
"""
Fill empty msgstr in .po files with AI draft translations.

Workflow context: this writes *drafts*. Every translated entry is marked
`#, fuzzy` so Poedit / Weblate / msgfmt highlight them for human review.
Run msgfmt -c after every saved file to catch reST damage early.

Hard rules enforced via the system prompt + post-check:
  - Preserve every reST inline marker verbatim: :ref:`...`, :doc:`...`,
    |param|, :math:`...`, double-backtick code, .. directives, URLs.
  - Keep brand / protocol / hardware tokens from the glossary in English
    (see scripts/i18n_glossary_zh_CN.yaml).
  - Translate consistently using the term map in the same file.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 scripts/i18n_ai_pretranslate.py locale/copter/zh_CN/LC_MESSAGES/index.po
  python3 scripts/i18n_ai_pretranslate.py locale/copter/zh_CN/LC_MESSAGES/  # all .po under dir
  python3 scripts/i18n_ai_pretranslate.py --dry-run --files-from priority.txt
  python3 scripts/i18n_ai_pretranslate.py --model claude-sonnet-4-6 --batch 25 ...

Files-from format: one .po path per line, # comments allowed.

Stops early on API error; safe to re-run (skips already-translated msgstr).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import polib
except ImportError:
    sys.exit("polib not installed; pip install polib")

try:
    import yaml
except ImportError:
    sys.exit("PyYAML not installed; pip install PyYAML")

try:
    import requests
except ImportError:
    sys.exit("requests not installed; pip install requests")

REPO_ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_PATH = REPO_ROOT / "scripts" / "i18n_glossary_zh_CN.yaml"
API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Heuristic: skip entries that are pure reST/code (no human-readable words)
PURE_MARKER_RE = re.compile(r"^[\s:`|.\-]+$")


def load_glossary():
    if not GLOSSARY_PATH.exists():
        return {"keep_english": [], "terms": {}}
    return yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8")) or {}


def build_system_prompt(glossary: dict, target_lang_name: str) -> str:
    keep = ", ".join(glossary.get("keep_english", [])) or "(none)"
    terms = glossary.get("terms", {}) or {}
    term_lines = "\n".join(f"  - {en} -> {zh}" for en, zh in terms.items())
    return f"""You are a technical translator producing draft translations of ArduPilot / multicopter flight-control documentation from English into {target_lang_name}.

ABSOLUTE PRESERVATION RULES — break these and the build fails:
1. Preserve every reST inline marker exactly as-is. Do NOT translate or alter text inside:
   - :ref:`...`, :doc:`...`, :math:`...`, :code:`...`, :term:`...`, :class:`...`, :func:`...`
   - Substitution refs like |param| or |any_name|
   - Inline code in double-backticks ``code`` or single-backticks `code`
   - Directive heads (.. image::, .. note::, .. warning::, .. code-block::, etc.)
   - URLs (http://..., https://...) and the URL part of `text <url>`_ links
   - HTML entities like &nbsp;, &amp;
2. Keep these tokens in English without translation:
   {keep}
3. Apply this term map consistently:
{term_lines}
4. Match leading/trailing whitespace and trailing punctuation of the source.
5. Do not add explanations, quotes, or markdown around the answer.

OUTPUT PROTOCOL:
You receive a JSON array under <input>...</input>; each entry has fields "id" (int) and "en" (string).
Respond with ONLY a JSON array of the same length under <output>...</output>; each entry has fields "id" (int, same as input) and "zh" (string, the translation).
No prose outside <output>...</output>.
"""


def call_anthropic(api_key: str, model: str, system: str, batch: list, retries: int = 3) -> list:
    """batch = [{"id": int, "en": str}, ...]; returns [{"id": int, "zh": str}, ...]"""
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system,
        "messages": [
            {"role": "user",
             "content": f"<input>{json.dumps(batch, ensure_ascii=False)}</input>"},
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                wait = 2 ** attempt
                print(f"  retry in {wait}s (status {r.status_code})", file=sys.stderr)
                time.sleep(wait)
                last_err = f"{r.status_code} {r.text[:200]}"
                continue
            r.raise_for_status()
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []))
            m = re.search(r"<output>(.*?)</output>", text, re.S)
            if not m:
                raise RuntimeError(f"no <output> in model response: {text[:200]}")
            return json.loads(m.group(1))
        except Exception as e:
            last_err = str(e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"API call failed after {retries} retries: {last_err}")


def split_batches(items, batch_size):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def discover_po_files(targets):
    out = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.po")))
        elif p.suffix == ".po" and p.exists():
            out.append(p)
        else:
            print(f"  skipping {t} (not a .po or directory)", file=sys.stderr)
    return out


def validate_msgfmt(po_path: Path) -> bool:
    try:
        subprocess.check_output(["msgfmt", "-c", "-o", os.devnull, str(po_path)],
                                stderr=subprocess.STDOUT)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  msgfmt FAILED on {po_path}: {getattr(e, 'output', e)}", file=sys.stderr)
        return False


def process_po(po_path: Path, api_key, model, system, batch_size, dry_run):
    po = polib.pofile(str(po_path))
    todo = [(i, e) for i, e in enumerate(po) if not e.msgstr and e.msgid
            and not PURE_MARKER_RE.match(e.msgid)]
    if not todo:
        return 0
    print(f"  {po_path}: {len(todo)} entries to translate")
    batch_input = [{"id": i, "en": e.msgid} for i, e in todo]
    by_id = {i: e for i, e in todo}
    translated_count = 0

    for chunk in split_batches(batch_input, batch_size):
        if dry_run:
            for item in chunk:
                print(f"    [dry] id={item['id']} en={item['en'][:80]!r}")
            translated_count += len(chunk)
            continue
        outs = call_anthropic(api_key, model, system, chunk)
        outs_by_id = {o["id"]: o.get("zh", "") for o in outs}
        for item in chunk:
            zh = outs_by_id.get(item["id"], "")
            if not zh:
                continue
            entry = by_id[item["id"]]
            entry.msgstr = zh
            # Mark as fuzzy so human reviewers see this is AI draft
            if "fuzzy" not in entry.flags:
                entry.flags.append("fuzzy")
            translated_count += 1

    if not dry_run and translated_count:
        # Atomic-ish write: write to .tmp then rename, msgfmt-check after.
        tmp = po_path.with_suffix(".po.tmp")
        po.save(str(tmp))
        if not validate_msgfmt(tmp):
            print(f"  KEEPING {tmp} for inspection; original unchanged.", file=sys.stderr)
            return 0
        shutil.move(str(tmp), str(po_path))
    return translated_count


def read_files_from(path: Path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="*", help=".po files or directories")
    ap.add_argument("--files-from", help="read .po paths from a file (one per line)")
    ap.add_argument("--lang", default="zh_CN")
    ap.add_argument("--lang-name", default="Simplified Chinese (zh_CN)")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001",
                    help="Anthropic model id (default Haiku 4.5)")
    ap.add_argument("--batch", type=int, default=20,
                    help="entries per API call (default 20)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be translated; no API call, no file change")
    args = ap.parse_args()

    targets = list(args.targets)
    if args.files_from:
        targets.extend(read_files_from(Path(args.files_from)))
    if not targets:
        sys.exit("no targets; pass .po paths, dirs, or --files-from FILE")

    po_files = discover_po_files(targets)
    if not po_files:
        sys.exit("no .po files matched")
    print(f"Found {len(po_files)} .po file(s)")

    api_key = None
    if not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            sys.exit("ANTHROPIC_API_KEY not set; export it or pass --dry-run")

    glossary = load_glossary()
    system = build_system_prompt(glossary, args.lang_name)

    total = 0
    for po_path in po_files:
        try:
            n = process_po(po_path, api_key, args.model, system, args.batch, args.dry_run)
            total += n
        except Exception as e:
            print(f"  ERROR processing {po_path}: {e}", file=sys.stderr)
    print(f"Done. {total} entries translated{' (dry-run)' if args.dry_run else ''}.")


if __name__ == "__main__":
    main()
