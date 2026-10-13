"""Jekyll-specific custom tags: ``{% seo %}`` and ``{% highlight %}``.

``{% seo %}`` emits the same kind of meta tags as the ``jekyll-seo-tag`` gem
(title / Open Graph / Twitter card / JSON-LD), which is what the blog uses.
``{% highlight lang %}`` runs its body through Pygments (rouge-compatible
HTML, wrapped in ``div.highlight`` so the site's syntax stylesheet applies).
"""

from __future__ import annotations

import json
from typing import Any

import pygments.lexers
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound

from .. import __version__
from ..urls import sanitize_url
from .filters import to_s, xml_escape
from .nodes import CustomNode


# --------------------------------------------------------------------- seo
def seo_tag(parser: Any, tokens: list, index: int, name: str, rest: str):
    token = tokens[index]
    node = CustomNode(_render_seo, None, token.line)
    return node, index + 1


_MISSING = object()


class _View:
    """Uniform ``.get``/``[]`` access for dicts and Liquid drops."""

    __slots__ = ("_obj",)

    def __init__(self, obj: Any):
        self._obj = obj

    def get(self, key: str, default: Any = None) -> Any:
        obj = self._obj
        if isinstance(obj, dict):
            return obj.get(key, default)
        getter = getattr(obj, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value


def _page(ctx: Any) -> _View:
    page = ctx.resolve("page")
    return _View(page if page is not None else {})


def _site(ctx: Any) -> dict[str, Any]:
    site = ctx.registers.get("site")
    return site if isinstance(site, dict) else {}


def _seo_title(page: _View, site: dict) -> str:
    site_title = to_s(site.get("title"))
    page_title = to_s(page.get("title"))
    if page_title and site_title and page_title != site_title:
        return f"{page_title} | {site_title}"
    return page_title or site_title


def _canonical_url(page: _View, site: dict) -> str:
    baseurl = to_s(site.get("baseurl"))
    url = to_s(site.get("url"))
    page_url = page.get("url")
    if page_url is None:
        return url
    return url + sanitize_url(f"{baseurl}{to_s(page_url)}")


def _render_seo(ctx: Any, _payload: Any) -> str:
    page = _page(ctx)
    site = _site(ctx)
    twitter = site.get("twitter") or {}
    author = site.get("author") or {}
    if not isinstance(author, dict):
        author = {"name": to_s(author)}

    seo_title = _seo_title(page, site)
    description = to_s(page.get("description") or site.get("description")).strip()
    canonical = _canonical_url(page, site)
    locale = to_s(page.get("locale") or site.get("locale") or "en")
    site_name = to_s(site.get("title"))
    has_date = page.get("date") is not None
    og_type = "article" if has_date else to_s(page.get("type") or "website")
    if og_type in ("post", "page", "note"):
        og_type = "article" if og_type in ("post", "note") else "website"

    image = page.get("image") or site.get("image")
    image_url = None
    if image and isinstance(image, str) and str(image).strip().lower() not in ("false", "true", "none"):
        image_url = image if image.startswith("http") else (
            to_s(site.get("url")) + sanitize_url(to_s(image))
        )

    card = to_s(twitter.get("card") or "summary")
    handle = to_s(twitter.get("username"))
    if handle and not handle.startswith("@"):
        handle = "@" + handle

    lines: list[str] = ["<!-- Begin jepyll seo tag -->"]
    lines.append(f"<title>{xml_escape(seo_title)}</title>")
    lines.append(f'<meta name="generator" content="Jepyll v{__version__}" />')
    if page.get("title"):
        lines.append(f'<meta property="og:title" content="{xml_escape(to_s(page["title"]))}" />')
    if author.get("name"):
        lines.append(f'<meta name="author" content="{xml_escape(to_s(author["name"]))}" />')
    lines.append(f'<meta property="og:locale" content="{xml_escape(locale)}" />')
    if description:
        lines.append(f'<meta name="description" content="{xml_escape(description)}" />')
        lines.append(f'<meta property="og:description" content="{xml_escape(description)}" />')
    lines.append(f'<link rel="canonical" href="{xml_escape(canonical)}" />')
    lines.append(f'<meta property="og:url" content="{xml_escape(canonical)}" />')
    if site_name:
        lines.append(f'<meta property="og:site_name" content="{xml_escape(site_name)}" />')
    lines.append(f'<meta property="og:type" content="{xml_escape(og_type)}" />')
    if has_date:
        lines.append(
            f'<meta property="article:published_time" content="{xml_escape(_iso(page["date"]))}" />'
        )
    if image_url:
        lines.append(f'<meta property="og:image" content="{xml_escape(image_url)}" />')
    lines.append(f'<meta name="twitter:card" content="{xml_escape(card)}" />')
    if page.get("title"):
        lines.append(f'<meta property="twitter:title" content="{xml_escape(to_s(page["title"]))}" />')
    if handle:
        lines.append(f'<meta name="twitter:site" content="{xml_escape(handle)}" />')
        lines.append(f'<meta property="twitter:creator" content="{xml_escape(handle)}" />')
    if image_url:
        lines.append(f'<meta name="twitter:image" content="{xml_escape(image_url)}" />')
    lines.append(f"<script type=\"application/ld+json\">{_json_ld(page, site, seo_title, description, canonical, og_type, image_url)}</script>")
    lines.append("<!-- End jepyll seo tag -->")
    return "\n".join(lines)


def _iso(value: Any) -> str:
    import datetime as dt

    if isinstance(value, dt.datetime):
        return value.isoformat()
    return to_s(value)


def _json_ld(
    page: _View,
    site: dict,
    title: str,
    description: str,
    canonical: str,
    og_type: str,
    image_url: str | None,
) -> str:
    import datetime as dt

    author = site.get("author") or {}
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "WebPage" if og_type != "article" else "BlogPosting",
    }
    if isinstance(author, dict) and author.get("name"):
        data["author"] = {"@type": "Person", "name": to_s(author["name"])}
    if og_type == "article" and isinstance(page.get("date"), dt.datetime):
        iso = page["date"].isoformat()
        data["datePublished"] = iso
        data["dateModified"] = iso
    if description:
        data["description"] = description
    data["headline"] = title or to_s(page.get("title"))
    data["url"] = canonical
    data["mainEntityOfPage"] = {"@type": "WebPage", "@id": canonical}
    if image_url:
        data["image"] = [image_url]
    return json.dumps(data, ensure_ascii=False)


# ----------------------------------------------------------------- highlight
def highlight_tag(parser: Any, tokens: list, index: int, name: str, rest: str):
    token = tokens[index]
    language = rest.strip().split()[0] if rest.strip() else ""
    body, index = parser._consume_until(tokens, index + 1, "endhighlight")
    return CustomNode(_render_highlight, (language, body), token.line), index


def _render_highlight(ctx: Any, payload: tuple[str, str]) -> str:
    language, body = payload
    body = body.lstrip("\n").rstrip("\n")
    try:
        lexer = pygments.lexers.get_lexer_by_name(language or "text")
    except ClassNotFound:
        lexer = pygments.lexers.TextLexer()
    formatter = HtmlFormatter(cssclass="highlight", nowrap=False)
    from pygments import highlight as pyg_highlight

    rendered = pyg_highlight(body, lexer, formatter)
    # Pygments emits "<div class="highlight">...</div>"; rouge emits the same
    # token classes, so the site stylesheet applies unchanged.
    return rendered


def build_custom_tags() -> dict[str, Any]:
    return {"seo": seo_tag, "highlight": highlight_tag}