#!/usr/bin/env python3
"""Fake MinerU launcher for the literature test suite.

Mimics ``mineru -p <pdf> -o <out>`` (plus ``--version``). Writes a
minimal but structurally realistic bundle to
``<out>/<stem>/auto/<stem>.md`` and ``<out>/<stem>/auto/<stem>_content_list.json``.

The MD body and the content_list JSON stay in sync so ``read
--section`` returns the same content for both providers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


MD = """# Attention Is All You Need

## 1 Introduction

The dominant sequence transduction models are based on complex recurrent
or convolutional neural networks.

## 2 Background

Attention mechanisms have become an integral part of models.

## 3 Model Architecture

### 3.1 Encoder and Decoder Stacks

The Transformer follows this overall architecture using stacked
self-attention.

### 3.2 Baselines

Comparing against RNN and CNN baselines.
"""


CONTENT_LIST = [
    {"type": "text", "text": "Attention Is All You Need", "text_level": 1, "page_idx": 0},
    {"type": "text", "text": "1 Introduction", "text_level": 2, "page_idx": 0},
    {"type": "text", "text": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.", "page_idx": 0},
    {"type": "text", "text": "2 Background", "text_level": 2, "page_idx": 1},
    {"type": "text", "text": "Attention mechanisms have become an integral part of models.", "page_idx": 1},
    {"type": "text", "text": "3 Model Architecture", "text_level": 2, "page_idx": 2},
    {"type": "text", "text": "3.1 Encoder and Decoder Stacks", "text_level": 3, "page_idx": 2},
    {"type": "text", "text": "The Transformer follows this overall architecture using stacked self-attention.", "page_idx": 2},
    {"type": "text", "text": "3.2 Baselines", "text_level": 3, "page_idx": 3},
    {"type": "text", "text": "Comparing against RNN and CNN baselines.", "page_idx": 3},
    {"type": "table", "table_body": "<table><tr><td>BLEU</td><td>28.4</td></tr></table>", "page_idx": 3},
]


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--version":
        print("fake-mineru 0.0.1")
        return 0
    pdf = None
    out = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-p":
            pdf = args[i + 1]
            i += 2
        elif a == "-o":
            out = args[i + 1]
            i += 2
        else:
            i += 1
    if not pdf or not out:
        print("fake_mineru: -p and -o required", file=sys.stderr)
        return 2
    stem = Path(pdf).stem
    dest = Path(out) / stem / "auto"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{stem}.md").write_text(MD, encoding="utf-8")
    (dest / f"{stem}_content_list.json").write_text(
        json.dumps(CONTENT_LIST), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
