"""Output serializers: ndjson / ids / table / bib / ris."""

from __future__ import annotations

import json
import re
import sys
from typing import Any, TextIO


def _open_out(out: str | None) -> TextIO:
    if out is None or out == "-":
        return sys.stdout
    return open(out, "w", encoding="utf-8", newline="\n")  # noqa: SIM115


def _close_if_file(fp: TextIO) -> None:
    if fp is not sys.stdout:
        fp.close()


# --------------------------------------------------------------------------- #
# NDJSON
# --------------------------------------------------------------------------- #


def write_ndjson(records: list[dict[str, Any]], out: str | None,
                 summary: dict[str, Any] | None = None) -> None:
    fp = _open_out(out)
    try:
        for rec in records:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if summary is not None:
            fp.write(json.dumps({"summary": summary}, ensure_ascii=False) + "\n")
    finally:
        _close_if_file(fp)


# --------------------------------------------------------------------------- #
# IDs (one per line)
# --------------------------------------------------------------------------- #


def write_ids(records: list[dict[str, Any]], out: str | None) -> None:
    fp = _open_out(out)
    try:
        for rec in records:
            fp.write(rec["identifier"] + "\n")
    finally:
        _close_if_file(fp)


# --------------------------------------------------------------------------- #
# Human table
# --------------------------------------------------------------------------- #


def _truncate(s: str | None, width: int) -> str:
    s = s or ""
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def write_table(records: list[dict[str, Any]], out: str | None) -> None:
    fp = _open_out(out)
    try:
        header = f"{'#':>3}  {'YEAR':>4}  {'CITED':>6}  {'VENUE':<20}  {'TITLE':<50}  DOI"
        fp.write(header + "\n")
        fp.write("-" * len(header) + "\n")
        for i, rec in enumerate(records, start=1):
            m = rec["meta"]
            authors = m.get("authors") or []
            first_author = authors[0].split()[-1] if authors else ""
            year = m.get("year") or ""
            cited = m.get("citation_count")
            cited_s = str(cited) if isinstance(cited, int) else "-"
            venue = _truncate(m.get("journal") or "", 20)
            title = _truncate(m.get("title") or "", 50)
            doi = m.get("doi") or (m.get("arxiv_id") and f"arXiv:{m['arxiv_id']}") or ""
            fp.write(f"{i:>3}  {str(year):>4}  {cited_s:>6}  {venue:<20}  {title:<50}  {doi}\n")
            if first_author and authors:
                more = f" +{len(authors)-1}" if len(authors) > 1 else ""
                fp.write(f"     {'':<4}  {'':<6}  {'':<20}  {first_author}{more}\n")
    finally:
        _close_if_file(fp)


# --------------------------------------------------------------------------- #
# BibTeX
# --------------------------------------------------------------------------- #


_CITEKEY_STOP = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with", "by"}


def _citekey(m: dict[str, Any]) -> str:
    authors = m.get("authors") or []
    surname = "anon"
    if authors:
        first = authors[0].strip()
        surname = first.split()[-1].lower()
    year = m.get("year") or "nd"
    title_words = re.findall(r"[A-Za-z]+", (m.get("title") or "").lower())
    title_words = [w for w in title_words if w not in _CITEKEY_STOP]
    tw = title_words[0] if title_words else "paper"
    return re.sub(r"[^a-z0-9]", "", f"{surname}{year}{tw}")


def _bibtex_escape(s: str) -> str:
    return (s.replace("\\", "\\\\")
             .replace("{", "\\{")
             .replace("}", "\\}")
             .replace("&", "\\&")
             .replace("%", "\\%")
             .replace("$", "\\$")
             .replace("#", "\\#"))


def _bibtex_type(rec_type: str | None) -> str:
    t = (rec_type or "").lower()
    if "preprint" in t or "posted" in t:
        return "misc"
    if "book" in t:
        return "book"
    if "conf" in t or "proceedings" in t:
        return "inproceedings"
    if "chapter" in t:
        return "incollection"
    return "article"


def write_bib(records: list[dict[str, Any]], out: str | None) -> None:
    fp = _open_out(out)
    try:
        for rec in records:
            m = rec["meta"]
            btype = _bibtex_type(m.get("type"))
            key = _citekey(m)
            fp.write(f"@{btype}{{{key},\n")
            if m.get("title"):
                fp.write(f"  title    = {{{_bibtex_escape(m['title'])}}},\n")
            if m.get("authors"):
                names = " and ".join(_bibtex_escape(a) for a in m["authors"])
                fp.write(f"  author   = {{{names}}},\n")
            if m.get("journal"):
                fp.write(f"  journal  = {{{_bibtex_escape(m['journal'])}}},\n")
            if m.get("year"):
                fp.write(f"  year     = {{{m['year']}}},\n")
            if m.get("volume"):
                fp.write(f"  volume   = {{{m['volume']}}},\n")
            if m.get("issue"):
                fp.write(f"  number   = {{{m['issue']}}},\n")
            if m.get("pages"):
                fp.write(f"  pages    = {{{m['pages']}}},\n")
            if m.get("doi"):
                fp.write(f"  doi      = {{{m['doi']}}},\n")
            if m.get("url"):
                fp.write(f"  url      = {{{m['url']}}},\n")
            if m.get("abstract"):
                fp.write(f"  abstract = {{{_bibtex_escape(m['abstract'])}}},\n")
            fp.write("}\n\n")
    finally:
        _close_if_file(fp)


# --------------------------------------------------------------------------- #
# RIS
# --------------------------------------------------------------------------- #


def _ris_type(rec_type: str | None) -> str:
    t = (rec_type or "").lower()
    if "preprint" in t or "posted" in t:
        return "UNPD"    # unpublished document
    if "book" in t:
        return "BOOK"
    if "conf" in t or "proceedings" in t:
        return "CONF"
    if "chapter" in t:
        return "CHAP"
    return "JOUR"


def write_ris(records: list[dict[str, Any]], out: str | None) -> None:
    fp = _open_out(out)
    try:
        for rec in records:
            m = rec["meta"]
            fp.write(f"TY  - {_ris_type(m.get('type'))}\n")
            if m.get("title"):
                fp.write(f"TI  - {m['title']}\n")
            for a in m.get("authors") or []:
                fp.write(f"AU  - {a}\n")
            if m.get("year"):
                fp.write(f"PY  - {m['year']}\n")
            if m.get("journal"):
                fp.write(f"JO  - {m['journal']}\n")
            if m.get("volume"):
                fp.write(f"VL  - {m['volume']}\n")
            if m.get("issue"):
                fp.write(f"IS  - {m['issue']}\n")
            if m.get("pages"):
                fp.write(f"SP  - {m['pages']}\n")
            if m.get("doi"):
                fp.write(f"DO  - {m['doi']}\n")
            if m.get("url"):
                fp.write(f"UR  - {m['url']}\n")
            if m.get("abstract"):
                fp.write(f"AB  - {m['abstract']}\n")
            fp.write("ER  - \n\n")
    finally:
        _close_if_file(fp)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def emit(records: list[dict[str, Any]], *, fmt: str, out: str | None,
         summary: dict[str, Any] | None = None) -> None:
    if fmt == "ndjson":
        write_ndjson(records, out, summary)
    elif fmt == "ids":
        write_ids(records, out)
    elif fmt == "table":
        write_table(records, out)
    elif fmt == "bib":
        write_bib(records, out)
    elif fmt == "ris":
        write_ris(records, out)
    else:
        raise ValueError(f"unknown format: {fmt!r}")


__all__ = ["emit"]
