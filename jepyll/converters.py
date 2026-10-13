"""Markdown -> HTML conversion.

Jekyll uses kramdown with ``input: GFM`` and rouge for highlighting. jepyll
uses ``python-markdown`` with an equivalent extension set plus Pygments
(classes are rouge-compatible, so the site's ``_sass/syntax.scss`` applies).

Extras implemented here for kramdown compatibility:

* ``$$math$$`` / ``$math$`` / ``\\(..\\)`` spans are protected from Markdown
  (kramdown's ``math_engine: mathjax`` leaves them for the browser).
* ``~~strikethrough~~`` (GFM)
* standalone ``{: .class}`` attribute lists (kramdown IAL)
* bare URL autolinking (GFM)
"""

from __future__ import annotations

import re

import markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.treeprocessors import Treeprocessor
from xml.etree import ElementTree as etree

from .config import Config

# ---------------------------------------------------------------- math guard
_MATH_BLOCK = re.compile(r"(\$\$.+?\$\$|\\\(.+?\\\)|\\\[.+?\\\])", re.DOTALL)
_MATH_INLINE = re.compile(r"\$(?!\$)([^\s$][^$\n]*?[^\s$])\$(?!\$)")
_OPEN, _CLOSE = "\uE003", "\uE004"


def _segment_fenced(text: str) -> list[tuple[bool, str]]:
    """Split into ``(is_code, chunk)`` so math is only masked outside code."""
    segments: list[tuple[bool, str]] = []
    in_code = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if not in_code and stripped.startswith(("```", "~~~")):
            in_code = True
            segments.append((True, line))
            continue
        if in_code:
            segments.append((True, line))
            if stripped.startswith(("```", "~~~")) and len(stripped.strip()) >= 3:
                # closing fence: the opening fence line was already flushed
                in_code = False
            continue
        segments.append((False, line))
    return segments if segments else [(False, text)]


def mask_math(text: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def keep(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"{_OPEN}{len(spans) - 1}{_CLOSE}"

    parts: list[str] = []
    for is_code, chunk in _segment_fenced(text):
        if is_code:
            parts.append(chunk)
            continue
        chunk = _MATH_BLOCK.sub(keep, chunk)
        chunk = _MATH_INLINE.sub(keep, chunk)
        parts.append(chunk)
    return "".join(parts), spans


def unmask_math(html: str, spans: list[str]) -> str:
    if not spans:
        return html
    return re.sub(
        re.escape(_OPEN) + r"(\d+)" + re.escape(_CLOSE),
        lambda m: spans[int(m.group(1))],
        html,
    )


# ------------------------------------------------------------ custom pieces
class StrikeThroughProcessor(InlineProcessor):
    """GFM ``~~deleted~~``."""

    def handleMatch(self, match: re.Match[str], data: str):  # noqa: N802
        el = etree.Element("del")
        el.text = match.group(1)
        return el, match.start(0), match.end(0)


class StrikeThroughExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802
        md.inlinePatterns.register(StrikeThroughProcessor(r"~~(.+?)~~"), "jepyll_strikethrough", 145)


class UrlAutolinkProcessor(InlineProcessor):
    """GFM autolinking of bare URLs (runs after ``link``/``image``)."""

    _PATTERN = r"(?<![\w@/])(https?://[^\s<>\"'\)\]]+)"

    def handleMatch(self, match: re.Match[str], data: str):  # noqa: N802
        url = match.group(1).rstrip(".,;:!?)")
        if not url:
            return None
        el = etree.Element("a")
        el.set("href", url)
        el.text = url
        return el, match.start(1), match.start(1) + len(url)


class UrlAutolinkExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802
        md.inlinePatterns.register(UrlAutolinkProcessor(UrlAutolinkProcessor._PATTERN), "jepyll_autolink", 140)


_IAL_STANDALONE = re.compile(r"^\{\:?(.*)\}\s*$")


class KramdownIALProcessor(Treeprocessor):
    """``{: .class}`` on a line of its own attaches to the previous element."""

    def run(self, root: etree.Element):  # noqa: N802
        from markdown.extensions.attr_list import get_attrs_and_remainder

        for parent in list(root.iter()):
            children = list(parent)
            for position, child in enumerate(children):
                if child.tag != "p" or list(child) or not child.text:
                    continue
                stripped = child.text.strip()
                match = _IAL_STANDALONE.match(stripped)
                if not match:
                    continue
                attrs, _remainder = get_attrs_and_remainder(match.group(1))
                target = children[position - 1] if position > 0 else child
                for key, value in attrs:
                    if key == ".":
                        existing = target.get("class")
                        target.set("class", f"{existing} {value}" if existing else value)
                    else:
                        target.set(key, value)
                if target is child:
                    child.text = ""
                    continue
                target.tail = (target.tail or "") + (child.tail or "")
                parent.remove(child)
        return root


class KramdownIALExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802
        md.treeprocessors.register(KramdownIALProcessor(), "jepyll_ial", 15)


# ------------------------------------------------------------------ converter
class MarkdownConverter:
    """Reusable (and cheap to pickle-free) Markdown -> HTML converter."""

    def __init__(self, config: Config | None = None):
        extensions = [
            "fenced_code",
            "codehilite",
            "tables",
            "attr_list",
            "footnotes",
            "sane_lists",
            "md_in_html",
            StrikeThroughExtension(),
            UrlAutolinkExtension(),
            KramdownIALExtension(),
        ]
        extension_configs = {
            "codehilite": {
                "cssclass": "highlight",
                "guess_lang": False,
                "noclasses": False,
                "pygments_formatter": "html",
                "linenums": False,
            },
        }
        self._md = markdown.Markdown(extensions=extensions, extension_configs=extension_configs)

    def convert(self, text: str) -> str:
        if not text:
            return ""
        masked, spans = mask_math(text)
        self._md.reset()
        html = self._md.convert(masked)
        return unmask_math(html, spans)


_default: MarkdownConverter | None = None


def convert(text: str, config: Config | None = None) -> str:
    """Convert one Markdown document (module level cache for speed)."""
    global _default
    if _default is None:
        _default = MarkdownConverter(config)
    return _default.convert(text)