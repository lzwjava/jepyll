"""Liquid + Jekyll filters.

Every filter is a plain function ``fn(value, *args)``. ``nil`` handling follows
Liquid: filters receive ``None`` for undefined variables and are expected to
behave like their Ruby counterparts (usually producing an empty string).
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import json
import math
import re
from typing import Any, Callable
from urllib.parse import quote, unquote

from ..dates import date_to_rfc822, date_to_xmlschema, parse_date, strftime as ruby_strftime, tzinfo
from ..urls import sanitize_url


# ----------------------------------------------------------------- helpers
def to_s(value: Any) -> str:
    """Liquid's ``to_s``."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _number(value: Any) -> float | int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        try:
            return float(str(value).strip())
        except ValueError:
            return 0


def _is_empty(value: Any) -> bool:
    from .expr import is_empty

    return is_empty(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# ----------------------------------------------------------------- strings
def append(value: Any, other: Any) -> str:
    return to_s(value) + to_s(other)


def prepend(value: Any, other: Any) -> str:
    return to_s(other) + to_s(value)


def capitalize(value: Any) -> str:
    return to_s(value).capitalize()


def upcase(value: Any) -> str:
    return to_s(value).upper()


def downcase(value: Any) -> str:
    return to_s(value).lower()


def replace(value: Any, pattern: Any, replacement: Any = "") -> str:
    return to_s(value).replace(to_s(pattern), to_s(replacement))


def replace_first(value: Any, pattern: Any, replacement: Any = "") -> str:
    return to_s(value).replace(to_s(pattern), to_s(replacement), 1)


def remove(value: Any, pattern: Any) -> str:
    return to_s(value).replace(to_s(pattern), "")


def remove_first(value: Any, pattern: Any) -> str:
    return to_s(value).replace(to_s(pattern), "", 1)


def lstrip(value: Any) -> str:
    return to_s(value).lstrip()


def rstrip(value: Any) -> str:
    return to_s(value).rstrip()


def strip(value: Any) -> str:
    return to_s(value).strip()


def strip_newlines(value: Any) -> str:
    return to_s(value).replace("\n", "").replace("\r", "")


def newline_to_br(value: Any) -> str:
    return re.sub(r"\r?\n", "<br />\n", to_s(value))


def split(value: Any, pattern: Any = None) -> list[str]:
    text = to_s(value)
    if pattern is None or pattern == "":
        return text.split()
    return text.split(to_s(pattern))


def join(value: Any, separator: str = " ") -> str:
    return to_s(separator).join(to_s(item) for item in _as_list(value))


def slice(value: Any, start: Any, length: Any = None) -> Any:
    seq: Any = value
    if isinstance(seq, (str, list, tuple)):
        start = int(start)
        length = int(length) if length is not None else 1
        if start < 0:
            start = max(len(seq) + start, 0)
        return seq[start:start + length]
    return value


def truncate(value: Any, length: int = 50, ellipsis: str = "...") -> str:
    text = to_s(value)
    length = int(length)
    if len(text) <= length:
        return text
    return text[: max(length - len(ellipsis), 0)] + to_s(ellipsis)


def truncatewords(value: Any, count: int = 15, ellipsis: str = "...") -> str:
    words = to_s(value).split()
    count = int(count)
    if len(words) <= count:
        return to_s(value)
    return " ".join(words[:count]) + to_s(ellipsis)


def strip_html(value: Any) -> str:
    return re.sub(r"<[^>]*>", "", to_s(value))


def number_of_words(value: Any) -> int:
    text = strip_html(value).strip()
    return len(text.split()) if text else 0


def cgi_escape(value: Any) -> str:
    return quote(to_s(value), safe="")


def uri_escape(value: Any) -> str:
    return quote(to_s(value), safe="/:?&=#")


def url_encode(value: Any) -> str:
    return quote(to_s(value), safe="")


def url_decode(value: Any) -> str:
    return unquote(to_s(value))


_XML_ESCAPES = [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("'", "&#39;")]


def xml_escape(value: Any) -> str:
    text = to_s(value)
    for char, escaped in _XML_ESCAPES:
        text = text.replace(char, escaped)
    return text


def escape(value: Any) -> str:
    return xml_escape(value)


def escape_once(value: Any) -> str:
    text = to_s(value)
    text = html_mod.unescape(text)
    return xml_escape(text)


def normalize_whitespace(value: Any) -> str:
    return re.sub(r"\s{2,}", " ", to_s(value)).strip()


def smartify(value: Any) -> str:
    text = to_s(value)
    text = re.sub(r"---(?=\s)", "&#8212;", text)
    text = re.sub(r"(^|\W)'(?=\S)", r"\1&#8217;", text)
    text = re.sub(r'(^|\W)"(?=\S)', r"\1&#8221;", text)
    return text


def jsonify(value: Any, *_args: Any) -> str:
    def default(obj: Any) -> Any:
        if isinstance(obj, (dt.date, dt.datetime)):
            return obj.isoformat()
        return to_s(obj)

    return json.dumps(value, default=default)


def inspect(value: Any) -> str:
    return f'"{to_s(value)}"' if isinstance(value, str) else repr(value)


def to_integer(value: Any) -> int:
    return int(_number(value))


# ----------------------------------------------------------------- numbers
def _numeric_filter(func: Callable[[float], float]) -> Callable[..., Any]:
    def wrapper(value: Any, *args: Any) -> Any:
        if value is None:
            return 0
        operands = [_number(arg) for arg in args]
        result = func(_number(value), *operands) if operands else func(_number(value))
        if all(isinstance(x, int) for x in [_number(value), *operands]) and isinstance(result, float):
            if result.is_integer():
                return int(result)
        return result

    return wrapper


plus = _numeric_filter(lambda a, b=0: a + b)
minus = _numeric_filter(lambda a, b=0: a - b)
times = _numeric_filter(lambda a, b=1: a * b)
modulo = _numeric_filter(lambda a, b=1: a % b if b else 0)
abs_ = _numeric_filter(abs)
ceil = _numeric_filter(math.ceil)
floor = _numeric_filter(math.floor)


def divided_by(value: Any, other: Any) -> Any:
    left, right = _number(value), _number(other)
    if right == 0:
        return 0
    if isinstance(left, int) and isinstance(right, int):
        return int(left / right)  # Ruby integer division truncates toward zero
    return left / right


def round(value: Any, places: Any = 0) -> Any:
    places = int(places)
    result = round_half_up(_number(value), places)
    if places == 0:
        return int(result)
    return result


def round_half_up(value: float, places: int) -> float:
    factor = 10 ** places
    return math.floor(value * factor + 0.5) / factor


# ----------------------------------------------------------------- arrays
def first(value: Any) -> Any:
    items = _as_list(value)
    return items[0] if items else None


def last(value: Any) -> Any:
    items = _as_list(value)
    return items[-1] if items else None


def reverse(value: Any) -> Any:
    if isinstance(value, str):
        return value[::-1]
    return list(reversed(_as_list(value)))


def size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value)
    return 0


def compact(value: Any) -> list[Any]:
    return [item for item in _as_list(value) if item is not None]


def uniq(value: Any) -> list[Any]:
    seen: list[Any] = []
    for item in _as_list(value):
        if item not in seen:
            seen.append(item)
    return seen


def push(value: Any, item: Any) -> list[Any]:
    return _as_list(value) + [item]


def concat(value: Any, other: Any) -> list[Any]:
    return _as_list(value) + _as_list(other)


def sum(value: Any, property_name: Any = None) -> Any:
    items = _as_list(value)
    if property_name is not None:
        total = 0
        for item in items:
            if isinstance(item, dict):
                total += _number(item.get(to_s(property_name)))
            else:
                total += _number(item)
        return total
    if not items:
        return 0
    total: Any = items[0]
    for item in items[1:]:
        total = _number(total) + _number(item)
    return total


def sample(value: Any, count: Any = None) -> Any:
    items = _as_list(value)
    if count is None:
        return items[0] if items else None
    return items[: int(count)]


def sort(value: Any, property_name: Any = None, direction: Any = None) -> list[Any]:
    items = _as_list(value)

    def key(item: Any) -> Any:
        if property_name is None:
            return item
        if isinstance(item, dict):
            return item.get(to_s(property_name))
        return getattr(item, to_s(property_name), None)

    def sort_key(item: Any) -> tuple[int, Any]:
        value_ = key(item)
        if value_ is None:
            return (1, "")
        if isinstance(value_, (int, float)):
            return (0, value_)
        return (0, to_s(value_).lower())

    reverse_it = to_s(direction).lower() == "desc" if direction is not None else False
    return sorted(items, key=sort_key, reverse=reverse_it)


def sort_natural(value: Any, property_name: Any = None) -> list[Any]:
    return sort(value, property_name)


def where(value: Any, key: Any, expected: Any = "__missing__") -> list[Any]:
    items = _as_list(value)
    key = to_s(key)
    if expected == "__missing__":
        return [item for item in items if _get(item, key)]
    result = []
    for item in items:
        candidate = _get(item, key)
        if candidate == expected or to_s(candidate) == to_s(expected):
            result.append(item)
    return result


def find(value: Any, key: Any, expected: Any = None) -> Any:
    found = where(value, key, expected)
    return found[0] if found else None


def group_by(value: Any, key: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = {}
    for item in _as_list(value):
        groups.setdefault(to_s(_get(item, key)), []).append(item)
    return [{"name": name, "items": items} for name, items in groups.items()]


def map(value: Any, key: Any) -> list[Any]:
    return [_get(item, key) for item in _as_list(value)]


def _get(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    if hasattr(item, key):
        attr = getattr(item, key)
        return attr() if callable(attr) else attr
    return None


def array_to_sentence_string(value: Any, connector: str = "and") -> str:
    items = [to_s(item) for item in _as_list(value)]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {connector} {items[-1]}"


# ------------------------------------------------------------------- dates
def date(value: Any, fmt: Any = None, timezone_name: Any = None) -> str:
    if value is None:
        return ""
    zone = tzinfo(to_s(timezone_name)) if timezone_name else None
    if isinstance(value, (dt.date, dt.datetime)):
        parsed: dt.datetime = value if isinstance(value, dt.datetime) else dt.datetime(value.year, value.month, value.day)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone or dt.timezone.utc)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = dt.datetime.fromtimestamp(value, tz=zone or dt.timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = parse_date(value, zone or dt.timezone.utc)
        except ValueError:
            return to_s(value)
    else:
        return to_s(value)

    if fmt is None:
        fmt = "%a, %d %b %Y %H:%M:%S %z"
    fmt = to_s(fmt)
    # Liquid supports passing an epoch instead of a format string
    if fmt.lstrip("-").isdigit():
        return str(int(parsed.timestamp()))
    return ruby_strftime(parsed, fmt)


def _as_datetime(value: Any, zone: dt.tzinfo | None = None) -> dt.datetime | None:
    if value is None:
        return None
    try:
        return parse_date(value, zone or dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def date_to_string(value: Any) -> str:
    parsed = _as_datetime(value)
    return ruby_strftime(parsed, "%d %b %Y") if parsed else ""

def date_to_long_string(value: Any, style: Any = None) -> str:
    parsed = _as_datetime(value)
    if not parsed:
        return ""
    fmt = "%d %B, %Y" if to_s(style) == "ordinal" else "%B %-d, %Y"
    return ruby_strftime(parsed, fmt)


# ------------------------------------------------------------------- urls
def make_url_filters(site: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """Filters that need ``site.url`` / ``site.baseurl`` from registers."""
    baseurl = to_s(site.get("baseurl"))
    url = to_s(site.get("url"))

    def relative_url(value: Any, *_args: Any) -> str:
        return sanitize_url(f"{baseurl}{to_s(value)}")

    def absolute_url(value: Any, *_args: Any) -> str:
        return url + relative_url(value)

    def date_to_xmlschema_safe(value: Any, *_args: Any) -> str:
        parsed = _as_datetime(value)
        return date_to_xmlschema(parsed) if parsed else ""

    def date_to_rfc822_safe(value: Any, *_args: Any) -> str:
        parsed = _as_datetime(value)
        return date_to_rfc822(parsed) if parsed else ""

    return {
        "relative_url": relative_url,
        "absolute_url": absolute_url,
        "date_to_xmlschema": date_to_xmlschema_safe,
        "date_to_rfc822": date_to_rfc822_safe,
    }


STANDARD_FILTERS: dict[str, Callable[..., Any]] = {
    # strings
    "append": append,
    "prepend": prepend,
    "capitalize": capitalize,
    "upcase": upcase,
    "downcase": downcase,
    "replace": replace,
    "replace_first": replace_first,
    "remove": remove,
    "remove_first": remove_first,
    "lstrip": lstrip,
    "rstrip": rstrip,
    "strip": strip,
    "strip_newlines": strip_newlines,
    "newline_to_br": newline_to_br,
    "split": split,
    "join": join,
    "slice": slice,
    "truncate": truncate,
    "truncatewords": truncatewords,
    "strip_html": strip_html,
    "number_of_words": number_of_words,
    "cgi_escape": cgi_escape,
    "uri_escape": uri_escape,
    "url_encode": url_encode,
    "url_decode": url_decode,
    "xml_escape": xml_escape,
    "escape": escape,
    "escape_once": escape_once,
    "normalize_whitespace": normalize_whitespace,
    "smartify": smartify,
    "jsonify": jsonify,
    "inspect": inspect,
    "to_integer": to_integer,
    # numbers
    "plus": plus,
    "minus": minus,
    "times": times,
    "divided_by": divided_by,
    "modulo": modulo,
    "abs": abs_,
    "ceil": ceil,
    "floor": floor,
    "round": round,
    # arrays / objects
    "first": first,
    "last": last,
    "reverse": reverse,
    "size": size,
    "compact": compact,
    "uniq": uniq,
    "push": push,
    "concat": concat,
    "sum": sum,
    "sample": sample,
    "sort": sort,
    "sort_natural": sort_natural,
    "where": where,
    "find": find,
    "group_by": group_by,
    "map": map,
    "array_to_sentence_string": array_to_sentence_string,
    # dates
    "date": date,
    "date_to_string": date_to_string,
    "date_to_long_string": date_to_long_string,
    "date_to_xmlschema": date_to_xmlschema,
    "date_to_rfc822": date_to_rfc822,
    # misc
    "default": lambda value, default_value="", **kw: default_value if _is_empty(value) else value,
}