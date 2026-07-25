"""Parse CLI flags → QueryObject.

The QueryObject dataclass is defined in query_obj.py; this module
holds the argparse-facing helpers and the strategy JSON loader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from query_obj import QueryObject


class QueryError(ValueError):
    """User input is malformed / modes are mixed."""


def _parse_year(spec: str | None) -> tuple[int | None, int | None]:
    """Parse '2020', '2020..2024', '2020..', '..2024'.  Returns (from, to)."""
    if not spec:
        return None, None
    if ".." in spec:
        lo, hi = spec.split("..", 1)
        lo_i = int(lo) if lo.strip() else None
        hi_i = int(hi) if hi.strip() else None
        return lo_i, hi_i
    y = int(spec)
    return y, y


def build_from_args(args: Any) -> QueryObject:
    """Build a QueryObject from argparse Namespace of the top-level search command.

    Fields on args:
        positional: str | None
        query: str | None
        title: str | None
        author: list[str] | None
        journal: str | None
        year: str | None            (e.g. '2020..2024')
        from_strategy: str | None   (path to strategy.json)
    """
    year_from, year_to = _parse_year(getattr(args, "year", None))

    # Count how many mode-selectors are set (mutually exclusive)
    modes_given = []
    if args.positional:
        modes_given.append("keyword")
    if args.query:
        modes_given.append("raw")
    field_flags = any([args.title, args.author, args.journal])
    if field_flags:
        modes_given.append("fields")
    if args.from_strategy:
        modes_given.append("strategy")

    if len(modes_given) == 0:
        raise QueryError(
            "no query given: pass a positional query, --query, --fields "
            "(--title / --author / --journal), or --from-strategy"
        )
    if len(modes_given) > 1:
        raise QueryError(
            f"conflicting input modes: {', '.join(modes_given)}. "
            "Pick exactly one of: positional / --query / --fields / --from-strategy"
        )

    mode = modes_given[0]

    if mode == "keyword":
        return QueryObject(mode="keyword", text=args.positional,
                           year_from=year_from, year_to=year_to)

    if mode == "raw":
        return QueryObject(mode="raw", text=args.query,
                           year_from=year_from, year_to=year_to)

    if mode == "fields":
        fields: dict[str, Any] = {}
        if args.title:
            fields["title"] = args.title
        if args.author:
            fields["authors"] = list(args.author)
        if args.journal:
            fields["journal"] = args.journal
        return QueryObject(mode="fields", fields=fields,
                           year_from=year_from, year_to=year_to)

    # strategy
    strategy_path = Path(args.from_strategy).expanduser()
    if not strategy_path.is_file():
        raise QueryError(f"strategy file not found: {strategy_path}")
    try:
        with open(strategy_path, "r", encoding="utf-8") as f:
            strategy = json.load(f)
    except json.JSONDecodeError as e:
        raise QueryError(f"strategy file is not valid JSON: {e}") from e

    compiled = strategy.get("compiled")
    if not isinstance(compiled, dict) or not compiled:
        raise QueryError(
            f"strategy file lacks a non-empty 'compiled' block: {strategy_path}"
        )
    return QueryObject(mode="strategy", per_source=dict(compiled),
                       year_from=year_from, year_to=year_to)


def build_from_batch_line(line: str) -> QueryObject:
    """Turn one line of `--from-file` into a keyword QueryObject."""
    line = line.strip()
    if not line:
        raise QueryError("empty line")
    return QueryObject(mode="keyword", text=line)


__all__ = ["QueryError", "build_from_args", "build_from_batch_line"]
