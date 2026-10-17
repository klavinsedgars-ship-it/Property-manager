"""Bulk import of income/expense rows (CSV file or pasted spreadsheet cells).

Columns are matched by name (English or Latvian): date / datums, category /
kategorija, amount / summa, description / apraksts / nosaukums / darba veids,
vendor / piegādātājs. Missing dates use the date chosen on the form; missing
or unknown categories are guessed from the description.
"""
from __future__ import annotations

import csv
import io
import re

from . import finance as fin
from .vzd import norm, to_date, to_float

COLUMNS = {
    "date": [r"^date", r"datums"],
    "category": [r"categ", r"kategor"],
    "amount": [r"^amount", r"summakop", r"^summa", r"^sum$", r"cena", r"price", r"eur"],
    "description": [r"descr", r"apraksts", r"nosaukums", r"darbaveids", r"^item", r"^name"],
    "vendor": [r"vendor", r"piegad", r"veikals", r"payee"],
}

# keyword -> category, checked in order against the normalised description
GUESSES = [
    (("darbs", "darba", "alga", "labour", "labor", "meistar", "montaz", "uzstadis"), "Renovation – labour"),
    (("ikea", "jysk", "pepco", "ledusskap", "velasmas", "trauku", "gulta", "matrac", "kresl", "galds", "galdin",
      "skapis", "spogul", "dekor", "divan", "tv", "televiz", "mebel", "aizkar", "lampa", "virtuvestehn"),
     "Furniture & appliances"),
    (("remont", "flize", "plaksne", "gipsk", "osb", "putas", "skruv", "grunts", "kras", "santehn", "kran", "pods",
      "dusa", "stikl", "depo", "kurss", "radiator", "trub", "elektr", "laminat", "material"), "Renovation – materials"),
]


def guess_category(text: str, default="Renovation – materials") -> str:
    t = norm(text)
    for words, cat in GUESSES:
        if any(w in t for w in words):
            return cat
    return default


def match_category(value: str | None, description: str) -> str:
    if value:
        v = norm(value)
        for c, _g in fin.CATEGORIES:
            if norm(c) == v or norm(c).startswith(v) and len(v) > 4:
                return c
    return guess_category(f"{value or ''} {description}")


def parse(text: str) -> list[dict]:
    text = text.strip("﻿\n ")
    if not text:
        return []
    sample = text[:5000]
    delim = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header = [norm(h) for h in rows[0]]
    body = rows[1:]
    filled = lambda i: sum(1 for r in body if i < len(r) and r[i].strip())  # noqa: E731
    cols = {}
    for field, pats in COLUMNS.items():
        # all columns matching any pattern; the most filled-in one wins (sheets often have empty extra columns)
        hits = [i for i, h in enumerate(header) if i not in cols.values() and any(re.search(p, h) for p in pats)]
        if hits:
            cols[field] = max(hits, key=lambda i: (filled(i), -hits.index(i)))
    if "amount" not in cols:
        raise ValueError(f"No amount column found in header: {rows[0]}")
    out, last_desc = [], None
    for r in body:
        get = lambda k: r[cols[k]].strip() if k in cols and cols[k] < len(r) else ""  # noqa: E731
        amount = to_float(get("amount"))
        desc = get("description")
        if not amount or re.search(r"(?i)^(kopā|kopa|total|summa|kopsumma)$", desc):
            continue
        if not desc and last_desc:          # continuation row of the line above (e.g. several payments)
            desc = f"{last_desc} (cont.)" if not last_desc.endswith("(cont.)") else last_desc
        last_desc = desc or last_desc
        out.append({"date": to_date(get("date")), "category": match_category(get("category"), desc),
                    "amount": abs(amount), "description": desc or None, "vendor": get("vendor") or None,
                    "_has_desc": bool(get("description"))})
    # a last row without its own description that is as large as everything else is a sheet total
    if len(out) > 2 and not out[-1]["_has_desc"] and out[-1]["amount"] >= 0.5 * sum(x["amount"] for x in out[:-1]):
        out.pop()
    for x in out:
        x.pop("_has_desc")
    return out
