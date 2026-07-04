#!/usr/bin/env python3
"""
i18n_autofix.py — mechanically repair marker defects found by i18n_lint.py.

Fix strategies (all deterministic, order-pairing based):
  A   bare link roles: pair translated bodies with the originals, in order,
      and restore the original body verbatim.
  B   explicit <target>: pair wrong targets with missing ones per role, in
      order of appearance, and restore the original target (translated
      labels are kept — they are legitimate).
  D   bare URLs: pair a damaged/truncated URL in the translation with the
      original by longest-common-prefix and restore it verbatim.
  E   substitution refs: entries that are *pure* substitutions get
      msgstr = msgid; inline translated |refs| are restored pairwise.
  F1  dropped ``literal``: if the literal text survives as plain text or
      inside CJK quotes 「」『』“”, re-wrap/replace it with ``literal``.
  F2  translated ``literal`` / verbatim role: restore original content
      pairwise (double-backtick content is code/UI by wiki convention).

Anything not mechanically provable (class C, misalignments, leftovers) is
written to --queue JSON for AI retranslation, deduped by msgid so shared
common-* entries are translated once.

After patching each entry the linter re-checks it; a fix is kept only if it
strictly reduces that entry's defect count. Files are msgfmt-validated.

Usage:
  python3 scripts/i18n_autofix.py locale/ --queue /tmp/retranslate.json
  python3 scripts/i18n_autofix.py locale/ --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n_lint import (ROLE_RE, TARGETED_RE, DCODE_RE, SUB_RE, URI_RE,
                       LINK_ROLES, VERBATIM_ROLES, INLINE_SPAN_RES,
                       OK_BEFORE_RE, OK_AFTER_RE, NAMEDLINK_NOSPACE_RE,
                       parse_markers, strip_url_punct, check_entry)

try:
    import polib
except ImportError:
    sys.exit("polib not installed; pip install polib")

CJK_QUOTE_RE = "「{0}」|『{0}』|“{0}”"


# --- per-class fixers: take (msgid, msgstr) -> new msgstr or None ----------

def fix_bare_roles(msgid, msgstr):
    """Class A: restore translated bare link-role bodies pairwise."""
    mid, mst = parse_markers(msgid), parse_markers(msgstr)
    missing = list((mid["bare"] - mst["bare"]).elements())
    added = list((mst["bare"] - mid["bare"]).elements())
    out = msgstr
    for (name, target) in missing:
        cands = [(n, b) for (n, b) in added if n == name]
        if not cands:
            return None
        n, b = cands[0]
        added.remove((n, b))
        out = out.replace(f":{n}:`{b}`", f":{n}:`{target}`", 1)
    return out


def fix_explicit_targets(msgid, msgstr):
    """Class B: restore translated <target> parts pairwise per role."""
    mid, mst = parse_markers(msgid), parse_markers(msgstr)
    missing = list((mid["targets"] - mst["targets"]).elements())
    added = list((mst["targets"] - mid["targets"]).elements())
    out = msgstr
    for (name, target) in missing:
        cands = [(n, t) for (n, t) in added if n == name]
        if not cands:
            # maybe the whole role got a bare body instead — give up here
            return None
        n, wrong = cands[0]
        added.remove((n, wrong))
        # replace only inside that role's <...>
        pat = re.compile(r"(:%s:`[^`]*?<)%s(>`)" % (re.escape(n), re.escape(wrong)))
        new = pat.sub(lambda m: m.group(1) + target + m.group(2), out, count=1)
        if new == out:
            return None
        out = new
    return out


def fix_bare_urls(msgid, msgstr):
    """Class D: restore damaged/truncated URLs by longest-common-prefix."""
    mid_urls = [strip_url_punct(u) for u in URI_RE.findall(msgid)]
    out = msgstr
    for u in mid_urls:
        if u in out:
            continue
        # candidate: URL in msgstr sharing the same scheme+host prefix
        cands = sorted(
            (c for c in URI_RE.findall(out)
             if os.path.commonprefix([c, u]).count("/") >= 3
             or u.startswith(strip_url_punct(c))),
            key=len, reverse=True)
        if not cands:
            return None
        out = out.replace(strip_url_punct(cands[0]), u, 1) \
            if strip_url_punct(cands[0]) in out else out.replace(cands[0], u, 1)
        if u not in out:
            return None
    return out


def fix_substitutions(msgid, msgstr):
    """Class E: pure-substitution sources copy verbatim; inline pairwise."""
    if SUB_RE.fullmatch(msgid.strip()):
        return msgid
    mid, mst = parse_markers(msgid), parse_markers(msgstr)
    missing = list((mid["subs"] - mst["subs"]).elements())
    added = list((mst["subs"] - mid["subs"]).elements())
    out = msgstr
    for sub in missing:
        if not added:
            return None
        out = out.replace(added.pop(0), sub, 1)
    return out


def fix_literals(msgid, msgstr):
    """Classes F1/F2: restore double-backtick literal content."""
    mid, mst = parse_markers(msgid), parse_markers(msgstr)
    missing = list((mid["code"] - mst["code"]).elements())
    added = list((mst["code"] - mid["code"]).elements())
    out = msgstr
    for lit in missing:
        if added:                                   # F2: translated in place
            out = out.replace(f"``{added.pop(0)}``", f"``{lit}``", 1)
            continue
        # F1: backticks dropped — try CJK quotes around the verbatim literal
        quoted = re.search(CJK_QUOTE_RE.format(re.escape(lit)), out)
        if quoted:
            out = out.replace(quoted.group(0), f"``{lit}``", 1)
            continue
        # plain verbatim text (word-ish boundary to avoid mid-word wraps)
        idx = out.find(lit)
        if idx != -1 and "`" not in out[max(0, idx - 2):idx]:
            out = out[:idx] + f"``{lit}``" + out[idx + len(lit):]
            continue
        # sole CJK-quoted segment left and one literal missing -> swap
        quotes = re.findall(r"「[^」]+」", out)
        if len(quotes) == 1 and len(missing) == 1:
            out = out.replace(quotes[0], f"``{lit}``", 1)
            continue
        return None
    # verbatim roles (e.g. :code:`x` translated)
    mid_v = list((mid["verbatim"] - mst["verbatim"]).elements())
    added_v = list((mst["verbatim"] - mid["verbatim"]).elements())
    for (name, body) in mid_v:
        cands = [(n, b) for (n, b) in added_v if n == name]
        if not cands:
            return None
        n, b = cands[0]
        added_v.remove((n, b))
        out = out.replace(f":{n}:`{b}`", f":{n}:`{body}`", 1)
    return out


def fix_glued_markup(msgid, msgstr):
    """Class I: insert a half-width space wherever the character adjacent to
    inline markup would stop docutils recognising the start/end string
    (CJK letters, opener after end-string, closer before start-string)."""
    out = msgstr
    for _ in range(20):                      # until fixpoint (spans shift)
        spans = []
        for rx in INLINE_SPAN_RES:
            spans.extend(m.span() for m in rx.finditer(out))
        patched = False
        for start, end in sorted(spans, reverse=True):
            if end < len(out) and not OK_AFTER_RE.fullmatch(out[end]):
                out = out[:end] + " " + out[end:]
                patched = True
            if start > 0 and not OK_BEFORE_RE.fullmatch(out[start - 1]):
                out = out[:start] + " " + out[start:]
                patched = True
        if not patched:
            break
    return out


def fix_namedlink_space(msgid, msgstr):
    """Class J: restore the whitespace between a named link's label and its
    <url> so docutils recognises the embedded URI."""
    out = msgstr
    for _ in range(10):
        m = NAMEDLINK_NOSPACE_RE.search(out)
        if not m:
            break
        idx = out.index("<", m.start())
        out = out[:idx] + " " + out[idx:]
    return out


FIXERS = [fix_bare_roles, fix_explicit_targets, fix_bare_urls,
          fix_substitutions, fix_literals, fix_glued_markup,
          fix_namedlink_space]


def try_fix(msgid, msgstr):
    """Apply fixers greedily while they strictly reduce the defect count."""
    best = msgstr
    best_n = len(check_entry(msgid, best))
    changed = True
    while changed and best_n:
        changed = False
        for fixer in FIXERS:
            cand = fixer(msgid, best)
            if cand is None or cand == best:
                continue
            n = len(check_entry(msgid, cand))
            if n < best_n:
                best, best_n, changed = cand, n, True
    return best, best_n


def validate_msgfmt(po_path: Path) -> bool:
    try:
        subprocess.check_output(
            ["msgfmt", "-c", "-o", os.devnull, str(po_path)],
            stderr=subprocess.STDOUT)
        return True
    except FileNotFoundError:
        return True          # msgfmt not installed locally; CI still checks
    except subprocess.CalledProcessError as e:
        print(f"  msgfmt FAILED {po_path}: {e.output.decode()[:300]}",
              file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--queue", help="write unfixable entries here (JSON)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    po_files = []
    for t in args.targets:
        p = Path(t)
        po_files.extend(sorted(p.rglob("*.po")) if p.is_dir() else [p])

    n_fixed = n_partial = n_queued = 0
    queue = defaultdict(lambda: {"msgstr": None, "sites": [], "classes": set()})
    for po_path in po_files:
        po = polib.pofile(str(po_path))
        dirty = False
        for e in po:
            # xgettext heuristically flags prose containing "90% of" etc. as
            # python-format; Sphinx catalogs are never printf templates and
            # the bogus flag makes msgfmt -c reject valid translations.
            if "python-format" in e.flags and not e.obsolete:
                e.flags.remove("python-format")
                if "no-python-format" not in e.flags:
                    e.flags.append("no-python-format")
                dirty = True
            if not e.msgid or not e.msgstr or e.obsolete:
                continue
            defects = check_entry(e.msgid, e.msgstr)
            if not defects:
                continue
            fixed, left = try_fix(e.msgid, e.msgstr)
            if fixed != e.msgstr:
                e.msgstr = fixed
                dirty = True
                if left == 0:
                    n_fixed += 1
                else:
                    n_partial += 1
            if left:
                n_queued += 1
                q = queue[e.msgid]
                q["msgstr"] = fixed
                q["sites"].append(str(po_path))
                q["classes"] |= {d["cls"] for d in defects}
        if dirty and not args.dry_run:
            po.save(str(po_path))
            if not validate_msgfmt(po_path):
                sys.exit(f"aborting: {po_path} failed msgfmt after fix")

    print(f"fully fixed: {n_fixed}   partially: {n_partial}   "
          f"queued for AI: {n_queued} entry-sites "
          f"({len(queue)} unique msgids)")
    if args.queue:
        out = [{"msgid": k, "msgstr": v["msgstr"], "sites": v["sites"],
                "classes": sorted(v["classes"])} for k, v in queue.items()]
        Path(args.queue).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"queue -> {args.queue}")


if __name__ == "__main__":
    main()
