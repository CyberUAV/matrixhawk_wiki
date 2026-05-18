"""Inject translations from a JSON map back into a frontend HTML file.

JSON shape: [{"en": "...", "zh": "..."}, ...]   (id ignored, en is key)
Walks the same nodes as _extract_frontend_strings.py and replaces each text /
attribute when it matches an "en" key. Same string used in multiple places gets
the same translation everywhere.

Usage:
  python3 scripts/_inject_frontend_strings.py SRC.html TRANSLATIONS.json LANG > OUT.html

LANG is the column to read from the JSON entry (e.g. "zh", "ja").
"""
import json
import re
import sys
from bs4 import BeautifulSoup, NavigableString, Comment

SKIP_TAGS = {"script", "style", "noscript"}


def main():
    src_html_path, json_path, lang = sys.argv[1], sys.argv[2], sys.argv[3]
    table = {row["en"]: row.get(lang, "") for row in json.load(open(json_path))}
    soup = BeautifulSoup(open(src_html_path).read(), "html.parser")

    def tr(s):
        return table.get(s.strip(), None)

    # Replace text nodes
    for el in list(soup.find_all(string=True)):
        if isinstance(el, Comment):
            continue
        if not isinstance(el, NavigableString):
            continue
        if el.parent and el.parent.name in SKIP_TAGS:
            continue
        s = str(el)
        if not re.search(r"[A-Za-z]", s):
            continue
        target = tr(s)
        if target:
            # Preserve leading/trailing whitespace of original
            leading = s[: len(s) - len(s.lstrip())]
            trailing = s[len(s.rstrip()):]
            el.replace_with(NavigableString(leading + target + trailing))

    # Replace attrs
    for tag in soup.find_all():
        for attr in ("alt", "title", "placeholder", "aria-label"):
            v = tag.get(attr)
            if v and not v.startswith(("#", "javascript:")):
                t = tr(v)
                if t:
                    tag[attr] = t
        if tag.name == "meta":
            nm = (tag.get("name") or tag.get("property") or "").lower()
            if nm in ("description", "og:title", "og:description", "og:site_name"):
                v = tag.get("content") or ""
                t = tr(v)
                if t:
                    tag["content"] = t

    # Update <html lang="...">
    if lang == "zh":
        html_tag = soup.find("html")
        if html_tag:
            html_tag["lang"] = "zh-CN"
    elif soup.find("html") and lang:
        soup.find("html")["lang"] = lang.replace("_", "-")

    sys.stdout.write(str(soup))


if __name__ == "__main__":
    main()
