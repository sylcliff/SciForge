"""sf-me init — create ~/.sciforge/me/me.md with commented examples.

Exit codes:
  0  created
  4  file already exists and --force was not passed
  1  I/O error while writing
"""

from __future__ import annotations

import sys

from config import me_file
from profile import write_skeleton


def cmd_init(force: bool) -> int:
    target = me_file()
    if target.exists() and not force:
        print(
            f"refusing to overwrite existing profile at {target}\n"
            f"pass --force to overwrite, or run 'sf-me edit' to modify it in place.",
            file=sys.stderr,
        )
        return 4
    try:
        write_skeleton(target)
    except OSError as e:
        print(f"failed to write {target}: {e}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
    return 0
