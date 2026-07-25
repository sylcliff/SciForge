"""sf-me edit — open me.md in $EDITOR.

Exit codes:
  0  editor exited 0
  3  me.md does not exist yet (user should run 'sf-me init' first),
     or $EDITOR is unset with no reasonable fallback
  1  editor exited non-zero, or subprocess failed to launch
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from config import me_file


def _resolve_editor() -> str | None:
    # Explicit env vars first — the standard Unix convention.
    for var in ("VISUAL", "EDITOR"):
        val = os.environ.get(var)
        if val:
            return val
    # Fallbacks. Only choose one that actually exists on PATH so we
    # do not leave the user staring at a mysterious 'command not found'.
    if os.name == "nt":
        for candidate in ("notepad.exe", "notepad"):
            if shutil.which(candidate):
                return candidate
    else:
        for candidate in ("nano", "vim", "vi"):
            if shutil.which(candidate):
                return candidate
    return None


def cmd_edit() -> int:
    target = me_file()
    if not target.is_file():
        print(
            f"me.md not found at {target}. Run 'sf-me init' first.",
            file=sys.stderr,
        )
        return 3

    editor = _resolve_editor()
    if editor is None:
        print(
            "no editor found — set $EDITOR (or $VISUAL) and try again.",
            file=sys.stderr,
        )
        return 3

    # Split the EDITOR string like a shell would (e.g. "code --wait").
    # shlex is stdlib and handles quoted args correctly. On Windows we
    # split with posix=False so backslashes in path literals survive,
    # then strip a single leading/trailing quote pair from each token
    # so the argv is directly usable by subprocess.run (same trick as
    # sf-lit's config.py get_converter_command).
    import shlex

    argv = shlex.split(editor, posix=(os.name != "nt"))
    if os.name == "nt":
        argv = [
            a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a
            for a in argv
        ]
    argv.append(str(target))

    try:
        proc = subprocess.run(argv, check=False)
    except OSError as e:
        print(f"failed to launch editor {editor!r}: {e}", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        print(
            f"editor exited with status {proc.returncode}", file=sys.stderr
        )
        return 1
    return 0
