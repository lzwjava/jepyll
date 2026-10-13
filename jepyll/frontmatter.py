"""YAML front matter parsing (the `--- ... ---` block at the top of a file)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import yaml

# Mirrors Jekyll's `Document::YAML_FRONT_MATTER_REGEXP`
#   \A(---\s*\n.*?\n?)^((---|\.\.\.)\s*$\n?)  with Ruby's /m semantics.
FRONT_MATTER_RE = re.compile(
    r"\A\ufeff?---[ \t]*\r?\n(?P<meta>.*?)(?:\r?\n)?^(?:---|\.\.\.)[ \t]*\r?$\n?",
    re.DOTALL | re.MULTILINE,
)


class FrontMatterError(ValueError):
    pass


def split(source: str) -> tuple[dict[str, Any] | None, str]:
    """Split `source` into ``(front matter dict, body)``.

    Returns ``(None, source)`` when the file has no front matter, which is how
    Jekyll decides that a file is a static file instead of a page.
    """
    match = FRONT_MATTER_RE.match(source)
    if not match:
        return None, source

    raw = match.group("meta")
    if not raw.strip():
        return {}, source[match.end():]

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"invalid YAML front matter: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontMatterError("front matter must be a YAML mapping")
    return data, source[match.end():]

def normalize_dates(data: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    """Make `date`-ish front matter timezone aware (Jekyll stores Time objects)."""
    from .dates import tzinfo

    zone = tzinfo(timezone_name)
    for key in ("date", "last_modified_at"):
        value = data.get(key)
        if isinstance(value, dt.datetime) and value.tzinfo is None:
            data[key] = value.replace(tzinfo=zone)
        elif isinstance(value, dt.date) and not isinstance(value, dt.datetime):
            data[key] = dt.datetime(value.year, value.month, value.day, tzinfo=zone)
    return data