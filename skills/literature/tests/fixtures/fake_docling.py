#!/usr/bin/env python3
"""Fake Docling launcher for the literature test suite.

Mimics ``docling <pdf> --to md --output <out>``. Writes a single
``<stem>.md`` at ``<out>/<stem>.md`` — no JSON sidecars, no page info,
no block-kind metadata. Just the markdown.
"""

from __future__ import annotations

import sys
from pathlib import Path


MD = """# Docling Rendered Paper

## Introduction

Some prose.

## Methods

A sentence about methods.

## Results

Result content, no page numbers.
"""


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--version":
        print("fake-docling 0.0.1")
        return 0
    pdf = None
    out = None
    i = 0
    positional = []
    while i < len(args):
        a = args[i]
        if a == "--output":
            out = args[i + 1]
            i += 2
        elif a == "--to":
            i += 2  # discard value
        elif a.startswith("--"):
            i += 1
        else:
            positional.append(a)
            i += 1
    if positional:
        pdf = positional[0]
    if not pdf or not out:
        print("fake_docling: positional PDF and --output required", file=sys.stderr)
        return 2
    stem = Path(pdf).stem
    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{stem}.md").write_text(MD, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
