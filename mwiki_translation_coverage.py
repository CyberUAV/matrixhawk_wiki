"""
Sphinx extension: inject per-page translation coverage into html_context.

For each page, finds the matching .po under any of the configured locale_dirs,
counts translated (non-fuzzy) entries vs total, and exposes:

    page_translation_ratio       float in [0, 1]  or None if no .po
    page_translation_translated  int
    page_translation_total       int

The theme's layout.html reads these to decide whether to render the
"this page is not fully translated" banner. The extension is a no-op
for the source language (language == 'en') — banners only make sense
for translated builds.

Registered in common_conf.py (extensions list). Lives at the repo root so
both common_conf.py and vehicle conf.py can find it on sys.path.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import polib

SOURCE_LANGUAGE = 'en'


def _find_po(app, pagename: str) -> Optional[Path]:
    """Search every locale_dir (in order) for <lang>/LC_MESSAGES/<pagename>.po."""
    lang = app.config.language or SOURCE_LANGUAGE
    if lang == SOURCE_LANGUAGE:
        return None
    for locale_dir in app.config.locale_dirs or []:
        base = (Path(app.confdir) / locale_dir).resolve()
        candidate = base / lang / 'LC_MESSAGES' / f'{pagename}.po'
        if candidate.exists():
            return candidate
    return None


def _coverage(po_path: Path) -> tuple[int, int]:
    """Return (translated, total). 'Translated' excludes empty msgstr and fuzzy entries."""
    pofile = polib.pofile(str(po_path))
    total = 0
    translated = 0
    for entry in pofile:
        if entry.obsolete or not entry.msgid:
            continue
        total += 1
        if entry.msgstr and 'fuzzy' not in entry.flags:
            translated += 1
    return translated, total


def on_html_page_context(app, pagename, templatename, context, doctree):
    po_path = _find_po(app, pagename)
    if po_path is None:
        context['page_translation_ratio'] = None
        context['page_translation_total'] = 0
        context['page_translation_translated'] = 0
        return
    try:
        translated, total = _coverage(po_path)
    except Exception:
        # Never block a build on a malformed .po — banner just won't show.
        context['page_translation_ratio'] = None
        context['page_translation_total'] = 0
        context['page_translation_translated'] = 0
        return
    context['page_translation_total'] = total
    context['page_translation_translated'] = translated
    context['page_translation_ratio'] = (translated / total) if total else None


def setup(app):
    app.connect('html-page-context', on_html_page_context)
    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
        'version': '0.1.0',
    }
