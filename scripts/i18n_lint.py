#!/usr/bin/env python3
"""
i18n_lint.py — reST-marker integrity linter for translated .po catalogs.

msgfmt -c only validates gettext syntax; it cannot see reStructuredText.
This linter enforces the invariants the AI-pretranslation pipeline promises
(see i18n_ai_pretranslate.py) but cannot fully guarantee:

  A  bare-target link roles must survive verbatim      :ref:`target`
  B  explicit targets must survive inside <...>        :ref:`label <target>`
     (translating the *label* is legitimate and encouraged)
  C  named-link URLs must survive                      `label <https://...>`_
  D  bare URLs must survive character-for-character    https://...
  E  substitution references must survive              |name|
  F  double-backtick literals must survive verbatim    ``literal``
     (F1 = literal dropped entirely, F2 = literal content translated)
  G  fuzzy entries (draft translations pending review)
  H  suspected batch misalignment: long English source whose translation
     contains no CJK at all, or is implausibly short — symptom of the
     id-mapping bug in early AI batches (translation belongs to another entry)
  I  inline markup in a position docutils will not recognise. Verified
     against docutils' inline recognition rules (states.py):
       - before the start-string only whitespace, openers (Ps/Pi/Pf, e.g.
         （「『“) and delimiters (Pd/Po, e.g. ，、：；。) are allowed —
         a CJK letter (Lo) or a closer (）」』) breaks recognition;
       - after the end-string only whitespace, \\.,;!? , delimiters and
         closers (Pe/Pf/Pi, e.g. ）」』”) are allowed — a CJK letter or an
         opener (（「『) breaks it.
     Broken markup renders as raw backticks/asterisks in the page.
  J  named link `label <url>`_ whose translation lost the whitespace
     between label and <url> — docutils then fails to recognise the
     embedded URI ("Anonymous hyperlink mismatch" build error)

Usage:
  python3 scripts/i18n_lint.py locale/                      # human summary
  python3 scripts/i18n_lint.py --json report.json locale/   # machine output
  python3 scripts/i18n_lint.py --fail-on A,B,C,D,E,F1,F2,H locale/   # CI gate

Exit code: 0 = clean (w.r.t. --fail-on classes), 1 = defects found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import polib
except ImportError:
    sys.exit("polib not installed; pip install polib")

# --- reST parsing ---------------------------------------------------------

ROLE_RE = re.compile(r":(?P<name>[a-zA-Z][a-zA-Z0-9:._+-]*):`(?P<body>[^`]+)`")
TARGETED_RE = re.compile(r"^(?P<label>.*?)\s*<(?P<target>[^<>]+)>$", re.S)
DCODE_RE = re.compile(r"``([^`]+)``")
SUB_RE = re.compile(r"\|[A-Za-z0-9_.\- ]+\|")
# strict URI charset: CJK / full-width punctuation terminates the match
URI_RE = re.compile(r"(?:https?|ftp)://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
NAMEDLINK_RE = re.compile(r"`[^`<]*<(?P<url>[^`<>]+)>`__?")
CJK_RE = re.compile(r"[一-鿿]")
# inline markup spans for the class-I recognition check (see docstring).
# The before/after character classes mirror docutils' start_string_prefix /
# end_string_suffix (docutils.parsers.rst.states.Inliner + punctuation_chars).
INLINE_SPAN_RES = [
    re.compile(r"``[^`]+``"),                                   # literal
    re.compile(r":[a-zA-Z][a-zA-Z0-9:._+-]*:`[^`]+`"),          # role
    re.compile(r"`[^`]+`__?"),                                  # named link
    re.compile(r"\*\*[^*\n]+\*\*"),                             # strong
]
CJK_LETTER_RE = re.compile(r"[一-鿿]")

try:
    from docutils.utils import punctuation_chars as _pc
    OK_BEFORE_RE = re.compile("[%s%s\\s'\"([{<\\-:/]" % (_pc.openers, _pc.delimiters))
    OK_AFTER_RE = re.compile("[%s%s%s\\s'\")\\]}>\\-:/\\\\.,;!?]"
                             % (_pc.closing_delimiters, _pc.delimiters, _pc.closers))
except ImportError:                       # docutils absent: CJK approximation
    OK_BEFORE_RE = re.compile(r"[\s（「『“‘,，、：；。？！<(\[{'\"\-:/]")
    OK_AFTER_RE = re.compile(r"[\s）」』”’,，、：；。？！>)\]}'\"\-:/\\.,;!?]")

# named link whose label is glued to <url> (no whitespace): docutils then
# does not recognise the embedded URI at all
NAMEDLINK_NOSPACE_RE = re.compile(r"`[^`<\s][^`<]*?[^`\s<]<(?:https?|ftp)[^`>]*>`__?")


def glued_spans(text: str) -> list[str]:
    """Return markup spans docutils will not recognise in this context.

    Spans nested inside a role or literal body (e.g. the ``**bold**`` in
    upstream's ``:ref:`**SERIAL0_BAUD** <SERIAL0_BAUD>```) are skipped:
    docutils takes role/literal bodies verbatim, so top-level inline
    recognition rules do not apply there — flagging them is a false
    positive that rejects byte-faithful translations of upstream markup.
    """
    containers = []
    for rx in (re.compile(r":[a-zA-Z][a-zA-Z0-9:._+-]*:`[^`]+`"),
               re.compile(r"``[^`]+``")):
        for m in rx.finditer(text):
            containers.append((m.start(), m.end()))

    def inside_container(s, e):
        return any(cs < s and e <= ce for cs, ce in containers)

    out = []
    for rx in INLINE_SPAN_RES:
        for m in rx.finditer(text):
            if inside_container(m.start(), m.end()):
                continue
            before = text[m.start() - 1: m.start()]
            after = text[m.end(): m.end() + 1]
            if (before and not OK_BEFORE_RE.fullmatch(before)) or \
               (after and not OK_AFTER_RE.fullmatch(after)):
                out.append(m.group(0))
    return out

# roles whose body is a link (label <target> or bare target)
LINK_ROLES = {"ref", "doc", "term", "numref", "download", "any", "keyword"}
# roles whose body is code-like and must survive verbatim
VERBATIM_ROLES = {"code", "math", "class", "func", "meth", "attr", "mod",
                  "data", "option", "envvar", "command", "makevar", "samp",
                  "file", "regexp"}
# roles whose body is display text — translation allowed: guilabel,
# menuselection, kbd, abbr, sub, sup, index, ...


def strip_url_punct(u: str) -> str:
    return u.rstrip(".,;:!?\"')")


def parse_markers(text: str):
    """Extract the multisets of reST markers this linter guards."""
    bare, targets, verbatim = Counter(), Counter(), Counter()
    for m in ROLE_RE.finditer(text):
        name, body = m.group("name"), m.group("body")
        if name in LINK_ROLES:
            tm = TARGETED_RE.match(body)
            if tm:
                targets[(name, tm.group("target").strip())] += 1
            else:
                bare[(name, body.strip())] += 1
        elif name in VERBATIM_ROLES:
            verbatim[(name, body)] += 1
    return {
        "bare": bare,
        "targets": targets,
        "verbatim": verbatim,
        "code": Counter(DCODE_RE.findall(text)),
        "subs": Counter(SUB_RE.findall(text)),
        "urls": Counter(strip_url_punct(u) for u in URI_RE.findall(text)),
        "namedlink_urls": Counter(
            strip_url_punct(m.group("url"))
            for m in NAMEDLINK_RE.finditer(text)
            if m.group("url").startswith(("http", "ftp"))),
    }


def looks_misaligned(msgid: str, msgstr: str) -> bool:
    """Heuristic for the batch id-mapping bug: a long, prose-like English
    source whose 'translation' contains no CJK at all, or is absurdly short.
    """
    if len(msgid) < 60 or CJK_RE.search(msgid):
        return False
    words = re.findall(r"[A-Za-z]{2,}", msgid)
    if len(words) < 8:
        return False
    if not CJK_RE.search(msgstr) and msgstr.strip() != msgid.strip():
        # nearly-identical output (only punctuation localized, quotes
        # normalized, etc.) is a legitimate copy-through, not misalignment
        import difflib
        if difflib.SequenceMatcher(None, msgid, msgstr).ratio() > 0.75:
            return False
        return True
    # CJK translation implausibly short for a long source paragraph
    if len(msgstr) < len(msgid) * 0.12:
        return True
    return False


def check_entry(msgid: str, msgstr: str) -> list[dict]:
    """Return a list of defect dicts for one translated entry."""
    defects = []
    mid, mst = parse_markers(msgid), parse_markers(msgstr)

    missing = mid["bare"] - mst["bare"]
    if missing:
        defects.append({"cls": "A", "detail": [f":{n}:`{b}`" for n, b in missing]})

    missing = mid["targets"] - mst["targets"]
    if missing:
        defects.append({"cls": "B", "detail": [f":{n}: <{t}>" for n, t in missing]})

    missing = mid["namedlink_urls"] - mst["urls"] - mst["namedlink_urls"]
    lost = [u for u in missing if u not in msgstr]
    if lost:
        defects.append({"cls": "C", "detail": lost})

    lost = [u for u in (mid["urls"] - mst["urls"]) if u not in msgstr]
    if lost:
        defects.append({"cls": "D", "detail": lost})

    if mid["subs"] != mst["subs"]:
        missing = mid["subs"] - mst["subs"]
        if missing:
            defects.append({"cls": "E", "detail": list(missing)})

    missing = mid["code"] - mst["code"]
    if missing:
        n_id, n_st = sum(mid["code"].values()), sum(mst["code"].values())
        cls = "F1" if n_st < n_id else "F2"
        defects.append({"cls": cls, "detail": list(missing),
                        "added": list(mst["code"] - mid["code"])})

    missing = mid["verbatim"] - mst["verbatim"]
    if missing:
        defects.append({"cls": "F2", "detail": [f":{n}:`{b}`" for n, b in missing],
                        "added": [f":{n}:`{b}`" for n, b in
                                  (mst["verbatim"] - mid["verbatim"])]})

    if looks_misaligned(msgid, msgstr):
        defects.append({"cls": "H", "detail": ["suspect batch misalignment"]})

    # Class I fires only for glued spans the TRANSLATION introduced: a span
    # that is equally glued in the English msgid is upstream-broken markup,
    # and a byte-faithful translation of it must not be rejected.
    source_glued = set(glued_spans(msgid))
    glued = [s for s in glued_spans(msgstr) if s not in source_glued]
    if glued:
        defects.append({"cls": "I", "detail": glued[:5]})

    nospace = NAMEDLINK_NOSPACE_RE.findall(msgstr)
    if nospace:
        defects.append({"cls": "J", "detail": nospace[:5]})
    return defects


# --- driver ---------------------------------------------------------------

def lint_po(po_path: Path):
    """Yield defect records for one .po file."""
    po = polib.pofile(str(po_path))
    for e in po:
        if not e.msgid or e.obsolete:
            continue
        if "fuzzy" in e.flags and e.msgstr:
            yield {"po": str(po_path), "msgid": e.msgid, "msgstr": e.msgstr,
                   "cls": "G", "detail": ["fuzzy"]}
        if not e.msgstr:
            continue
        for d in check_entry(e.msgid, e.msgstr):
            yield {"po": str(po_path), "msgid": e.msgid, "msgstr": e.msgstr,
                   "cls": d["cls"], "detail": d["detail"],
                   "added": d.get("added", [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("targets", nargs="+", help=".po files or directories")
    ap.add_argument("--json", help="write full machine-readable report here")
    ap.add_argument("--fail-on", default="A,B,C,D,E,F1,F2,H,I,J",
                    help="comma-separated defect classes that fail the build "
                         "(default: all except G/fuzzy)")
    ap.add_argument("--max-examples", type=int, default=5)
    args = ap.parse_args()

    po_files = []
    for t in args.targets:
        p = Path(t)
        po_files.extend(sorted(p.rglob("*.po")) if p.is_dir() else [p])

    records = []
    for po_path in po_files:
        records.extend(lint_po(po_path))

    by_cls = defaultdict(list)
    for r in records:
        by_cls[r["cls"]].append(r)

    names = {"A": "bare link target changed", "B": "explicit <target> changed",
             "C": "named-link URL lost", "D": "bare URL lost/damaged",
             "E": "substitution |ref| mismatch", "F1": "``literal`` dropped",
             "F2": "``literal``/verbatim-role translated", "G": "fuzzy (draft)",
             "H": "suspect batch misalignment",
             "I": "inline markup in unrecognisable position (won't render)",
             "J": "named link missing space before <url>"}
    print(f"linted {len(po_files)} .po files")
    for cls in sorted(by_cls):
        rs = by_cls[cls]
        print(f"\n[{cls}] {names.get(cls, cls)}: {len(rs)}")
        for r in rs[:args.max_examples]:
            print(f"    {r['po']}")
            print(f"      id : {r['msgid'][:100]!r}")
            print(f"      del: {r['detail'][:3]}"
                  + (f"  add: {r['added'][:3]}" if r.get("added") else ""))
    if not by_cls:
        print("clean — no defects found")

    if args.json:
        Path(args.json).write_text(
            json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nfull report -> {args.json} ({len(records)} records)")

    fail_classes = set(args.fail_on.split(","))
    n_fail = sum(len(v) for k, v in by_cls.items() if k in fail_classes)
    if n_fail:
        print(f"\nFAIL: {n_fail} defects in classes {sorted(fail_classes & set(by_cls))}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
