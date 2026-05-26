"""Extract untranslated entries from given .po files into a single JSON map.

JSON shape: [{"po": "<path>", "idx": <int>, "id": <global-id>, "en": "..."}]

idx is the index within the .po (used by the inject script). Skip entries
whose msgid is pure markup (no letters).
"""
import json
import re
import sys
from pathlib import Path

import polib

PURE_MARKER_RE = re.compile(r"^[\s:`|.\-]+$")
out = []
gid = 0
for po_path in sys.argv[1:]:
    po = polib.pofile(po_path)
    for idx, entry in enumerate(po):
        if entry.obsolete:
            continue
        if not entry.msgid or entry.msgstr:
            continue
        if PURE_MARKER_RE.match(entry.msgid):
            continue
        out.append({"po": po_path, "idx": idx, "id": gid, "en": entry.msgid})
        gid += 1
print(json.dumps(out, ensure_ascii=False, indent=2))
