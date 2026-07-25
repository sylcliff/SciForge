"""sf-me show — print the profile.

Two output modes:
  - Human (default):  section-grouped plain-text table.
  - --json:           ADR-0006 minimum-contract envelope with the
                      profile under `data`.

Only ``self`` is a valid entity id (``sf-me`` owns one entity). Any
other id exits 3.
"""

from __future__ import annotations

import json
import sys

from config import me_file
from profile import SECTIONS, ProfileError, load_profile

ENTITY_ID = "self"
ENTITY_TYPE = "me"
ENTITY_URI = f"sciforge://me/{ENTITY_ID}"


def _emit_json(sections: dict[str, list[dict]]) -> None:
    envelope = {
        "id": ENTITY_ID,
        "type": ENTITY_TYPE,
        "uri": ENTITY_URI,
        "data": {name: sections.get(name, []) for name in SECTIONS},
    }
    json.dump(envelope, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _emit_human(sections: dict[str, list[dict]], source: str) -> None:
    # Header line points the user at the file for editing / cat.
    print(f"# Me  ({source})")
    print()

    # Nice-cased section titles, keep the plural everyone uses.
    titles = {
        "skill": "Skills",
        "equipment": "Equipment",
        "compute": "Compute",
        "preference": "Preferences",
        "history": "History",
    }

    for section in SECTIONS:
        entries = sections.get(section, [])
        title = titles[section]
        header = f"## {title} ({len(entries)})"
        if not entries:
            print(f"{header}  (empty)")
            print()
            continue
        print(header)
        # Column-align: pad `name` to the widest in the section.
        width = max(len(str(e["name"])) for e in entries)
        for e in entries:
            print(f"  {str(e['name']).ljust(width)}    {e['short']}")
        print()


def cmd_show(entity_id: str, as_json: bool) -> int:
    if entity_id != ENTITY_ID:
        print(
            f"unknown id: {entity_id!r} (sf-me owns only 'self')",
            file=sys.stderr,
        )
        return 3

    path = me_file()
    try:
        sections = load_profile(path)
    except ProfileError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code

    if as_json:
        _emit_json(sections)
    else:
        _emit_human(sections, source=str(path))
    return 0
