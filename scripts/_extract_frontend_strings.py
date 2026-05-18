"""One-off helper: extract translatable strings from frontend/index.html into JSON."""
import json
import re
import sys
from bs4 import BeautifulSoup, NavigableString, Comment

SKIP_TAGS = {"script", "style", "noscript"}

soup = BeautifulSoup(open(sys.argv[1]).read(), "html.parser")
out = []
seen = {}


def add(text, kind, hint=""):
    s = text.strip()
    if not s or len(s) < 2:
        return
    if not re.search(r"[A-Za-z]", s):
        return
    if s in seen:
        return
    seen[s] = len(out)
    out.append({"id": len(out), "kind": kind, "hint": hint, "en": s})


for el in soup.find_all(string=True):
    if isinstance(el, Comment):
        continue
    if not isinstance(el, NavigableString):
        continue
    if el.parent and el.parent.name in SKIP_TAGS:
        continue
    add(str(el), "text", el.parent.name if el.parent else "")

for tag in soup.find_all():
    for attr in ("alt", "title", "placeholder", "aria-label"):
        v = tag.get(attr)
        if v and not v.startswith(("#", "javascript:")):
            add(v, attr, tag.name)
    if tag.name == "meta":
        nm = (tag.get("name") or tag.get("property") or "").lower()
        if nm in ("description", "og:title", "og:description", "og:site_name"):
            add(tag.get("content") or "", "meta:" + nm, "meta")

print(json.dumps(out, ensure_ascii=False, indent=2))
