"""Permalink / URL generation, mirroring ``Jekyll::URL`` + ``Jekyll::Utils.slugify``."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

# Regexes used by Jekyll's `Utils.slugify` (SLUGIFY_MODES).
_SLUGIFY = {
    "default": re.compile(r"[^0-9a-zA-Z]+"),
    "pretty": re.compile(r"[^0-9a-zA-Z._~!$&'()+,;=@]+"),
    "ascii": re.compile(r"[^0-9a-zA-Z]+"),
    "latin": re.compile(r"[^0-9a-zA-Z]+"),
    "raw": None,
}

_SAFE_PATH = re.compile(r"\A[a-zA-Z0-9./-]+\Z")


def slugify(string: str | None, mode: str = "default", cased: bool = False) -> str | None:
    """Jekyll-compatible slugification."""
    if string is None:
        return None
    pattern = _SLUGIFY.get(mode)
    if mode not in _SLUGIFY:
        pattern = _SLUGIFY["default"]
    if pattern is None:  # mode == "raw"
        return string if cased else string.lower()

    text = string
    if mode in ("ascii", "latin"):
        text = unicodedata.normalize("NFKD", text)
        if mode == "ascii":
            text = text.encode("ascii", "ignore").decode("ascii")
        else:  # latin: drop combining marks only
            text = "".join(ch for ch in text if not unicodedata.combining(ch))

    slug = pattern.sub("-", text).strip("-")
    if not cased:
        slug = slug.lower()
    if not slug:
        import warnings

        warnings.warn(f"Empty `slug` generated for {string!r}", stacklevel=2)
    return slug


def titleize_slug(slug: str) -> str:
    """`Utils.titleize_slug` - turns `my-post-title` into `My Post Title`."""
    return " ".join(part.capitalize() for part in slug.split("-"))


def escape_path(path: str) -> str:
    """Percent-encode a path the way ``Jekyll::URL.escape_path`` does."""
    if not path or _SAFE_PATH.match(path):
        return path
    encoded = quote(path, safe="!$&'()*+,;=:@/-._~")
    return encoded.replace("#", "%23")


def sanitize_url(url: str) -> str:
    """Strip `..`, `./` and duplicate slashes; enforce a leading slash."""
    result = "/" + url
    result = result.replace("..", "/")
    result = result.replace("./", "")
    result = re.sub(r"/+", "/", result)
    return result


def generate_url(template: str, placeholders: dict[str, object]) -> str:
    """Replace `:tokens` in `template` (Jekyll `URL#generate_url_from_hash`)."""
    result = template
    if ":" not in result:
        return result
    for key, value in placeholders.items():
        if ":" not in result:
            break
        if value is None:
            result = result.replace(f"/:{key}", "").replace(f":{key}", "")
        else:
            result = result.replace(f":{key}", escape_path(str(value)))
    return result


def build_url(
    template: str | None = None,
    permalink: str | None = None,
    placeholders: dict[str, object] | None = None,
) -> str:
    """Compute a document's URL (Jekyll ``URL.new(...).to_s``)."""
    placeholders = placeholders or {}
    if template is None and permalink is None:
        raise ValueError("one of template or permalink must be supplied")
    raw = generate_url(permalink, placeholders) if permalink else generate_url(template, placeholders)
    return sanitize_url(raw)


def permalink_extension(permalink: str | None) -> str | None:
    """`Renderer#permalink_ext` - the extension implied by an explicit permalink."""
    if permalink and not permalink.endswith("/"):
        ext = "." + permalink.rsplit(".", 1)[-1] if "." in permalink.rsplit("/", 1)[-1] else ""
        if ext and ext != ".":
            return ext
    return None


def converter_output_ext(source_ext: str, markdown_exts: set[str]) -> str:
    """Extension a file's content is written with (`.md` -> `.html`, ...)."""
    ext = source_ext if source_ext.startswith(".") else f".{source_ext}"
    known = {item.lower().lstrip(".") for item in markdown_exts}
    if ext.lower().lstrip(".") in known:
        return ".html"
    if ext.lower() in (".html", ".htm", ".xhtml"):
        return ".html"
    if ext in (".sass", ".scss"):
        return ".css"
    return ext


def destination_for(url: str, output_ext: str) -> str:
    """``Document#destination`` / ``Page#destination`` (relative to site dest)."""
    if url.endswith("/"):
        return url + "index.html"
    if url.endswith(output_ext):
        return url
    return url + output_ext


def add_permalink_suffix(template: str, permalink_style: str) -> str:
    """``Jekyll::Utils.add_permalink_suffix``."""
    if permalink_style == "pretty":
        return template + "/"
    if permalink_style in ("date", "ordinal", "none"):
        return template + ":output_ext"
    result = template
    if permalink_style.endswith("/"):
        result += "/"
    if permalink_style.endswith(":output_ext"):
        result += ":output_ext"
    return result