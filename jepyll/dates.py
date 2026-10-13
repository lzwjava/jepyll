"""Date helpers: site timezones and Ruby-compatible strftime.

Jekyll stores post dates as ``Time`` objects in the site's timezone
(``_config.yml: timezone`` sets ``ENV['TZ']`` in Ruby). We use ``zoneinfo``
and provide a small shim for the strftime directives Ruby supports but
Python's ``strftime`` does not (``%e``, ``%l``, ``%-d`` ...).
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@lru_cache(maxsize=8)
def tzinfo(name: str | None) -> dt.tzinfo:
    if not name or str(name).upper() == "UTC":
        return dt.timezone.utc
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return dt.timezone.utc


def parse_date(value: Any, zone: dt.tzinfo | None = None) -> dt.datetime:
    """Coerce many YAML/string/date values into an aware ``datetime``."""
    zone = zone or dt.timezone.utc
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=zone)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=zone)
    if isinstance(value, bool):
        raise ValueError(f"cannot parse date: {value!r}")
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, tz=zone)
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered in ("now", "today"):
            return dt.datetime.now(tz=zone)
        if lowered == "yesterday":
            return dt.datetime.now(tz=zone) - dt.timedelta(days=1)
        if lowered == "tomorrow":
            return dt.datetime.now(tz=zone) + dt.timedelta(days=1)
        parsed = _parse_iso_like(text)
        if parsed is None:
            raise ValueError(f"cannot parse date: {value!r}")
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    raise ValueError(f"cannot parse date: {value!r}")


def _parse_iso_like(text: str) -> dt.datetime | None:
    """Parse the date shapes that show up in front matter/filenames."""
    for fmt in (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:  # ISO-8601 with fractional seconds / colon offsets
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


_DIRECTIVE = re.compile(
    r"%(-|_|#|0)?(e|l|P|N|s|V|G|y{1,4}|m{1,2}|d{1,2}|H{1,2}|I{1,2}|M{1,2}|S{1,2})"
)


def strftime(value: dt.datetime, fmt: str) -> str:
    """``strftime`` handling the Ruby/Liquid directives Python lacks.

    Supported extras: ``%-d`` (no padding), ``%e`` (space padded day),
    ``%l`` (space padded 12h hour), ``%P`` (am/pm), ``%_d``/``%_m``,
    ``%N`` (fractional seconds), ``%s`` (epoch), ``%V``/``%G`` (ISO week/year).
    """
    literals: list[str] = []

    def stash(text: str) -> str:
        literals.append(text)
        return f"\x01{len(literals) - 1}\x01"

    def convert(match: re.Match[str]) -> str:
        flag, word = match.group(1), match.group(2)
        hour12 = value.hour % 12 or 12
        no_pad = {"d": value.day, "m": value.month, "y": value.year % 100,
                  "H": value.hour, "M": value.minute, "S": value.second,
                  "I": hour12}
        if flag == "-":
            if word in no_pad:
                return stash(str(no_pad[word]))
            return match.group(0)
        if flag == "_":
            if word in no_pad:
                return stash(f"{no_pad[word]:2d}")
            return match.group(0)
        if flag in ("#", "0"):  # alternate form: unsupported, best effort
            return match.group(0)
        if word == "e":
            return stash(f"{value.day:2d}")
        if word == "l":
            return stash(f"{hour12:2d}")
        if word == "P":
            return stash("am" if value.hour < 12 else "pm")
        if word == "N":
            return stash(f"{value.microsecond:06d}")
        if word == "s":
            return stash(str(int(value.timestamp())))
        if word == "V":
            return stash(f"{value.isocalendar()[1]:02d}")
        if word == "G":
            return stash(str(value.isocalendar()[0]))
        if len(word) > 2:  # %yyy / %yyyy -> %Y
            return "%Y"
        return match.group(0)

    py_fmt = _DIRECTIVE.sub(convert, fmt)
    rendered = value.strftime(py_fmt)
    return re.sub(r"\x01(\d+)\x01", lambda m: literals[int(m.group(1))], rendered)


def date_to_xmlschema(value: dt.datetime) -> str:
    offset = value.strftime("%z") or "+0000"
    offset = "+00:00" if offset in ("+0000", "") else f"{offset[:3]}:{offset[3:]}"
    return value.strftime("%Y-%m-%dT%H:%M:%S") + offset


def date_to_rfc822(value: dt.datetime) -> str:
    return value.strftime("%a, %d %b %Y %H:%M:%S %z")