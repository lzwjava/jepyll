"""Pages, collection documents and static files.

The URL/destination logic mirrors Jekyll's ``Page``, ``Document`` and
``Jekyll::URL`` so that a migrated site keeps exactly the same URLs.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any, Callable

from .config import Config
from .dates import parse_date, tzinfo
from .urls import (
    add_permalink_suffix,
    build_url,
    converter_output_ext,
    destination_for,
    permalink_extension,
    slugify,
    titleize_slug,
)

# Jekyll::Document::DATE_FILENAME_MATCHER / DATELESS_FILENAME_MATCHER
DATE_FILENAME_RE = re.compile(r"^(?:.+/)*?(\d{2,4}-\d{1,2}-\d{1,2})-([^/]*)(\.[^.]+)$")
DATELESS_FILENAME_RE = re.compile(r"^(?:.+/)*(.+)(\.[^.]+)$")

HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}


class StaticFile:
    """A file copied verbatim into the destination."""

    __slots__ = ("site", "source_path", "relative_path", "collection", "_mtime")

    def __init__(self, site_source: str, source_path: str, relative_path: str, collection: str = ""):
        self.source_path = source_path
        self.relative_path = relative_path
        self.collection = collection

    # -- accessors used by Liquid -----------------------------------------
    @property
    def path(self) -> str:
        return self.relative_path

    @property
    def name(self) -> str:
        return os.path.basename(self.relative_path)

    @property
    def basename(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def extname(self) -> str:
        return os.path.splitext(self.name)[1]

    @property
    def type(self) -> str:
        return self.collection

    @property
    def modified_time(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(os.path.getmtime(self.source_path), tz=dt.timezone.utc)

    def drop(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "basename": self.basename,
            "extname": self.extname,
            "modified_time": self.modified_time,
            "path": self.relative_path,
            "collection": self.collection,
            "type": self.collection,
        }

    # -- writing -----------------------------------------------------------
    @property
    def destination_relative(self) -> str:
        return self.relative_path


class BaseDocument:
    """Shared behaviour for pages and collection documents."""

    kind = "document"

    def __init__(
        self,
        config: Config,
        source_path: str,
        relative_path: str,
        data: dict[str, Any],
        content: str,
        collection: str = "",
    ):
        self.config = config
        self.source_path = source_path
        self.relative_path = relative_path
        self.data = data
        self.content = content
        self.collection = collection

        self.url: str = ""
        self.destination: str = ""  # relative to site destination
        self.output_ext: str = ".html"
        self.rendered: str | None = None  # cached liquid+markdown output
        self._content_fn: Callable[["BaseDocument"], str] | None = None

    # -- identity ----------------------------------------------------------
    @property
    def path(self) -> str:
        """`page.path` - path relative to the site source (Jekyll DocumentDrop)."""
        return self.relative_path

    @property
    def basename_without_ext(self) -> str:
        return os.path.splitext(os.path.basename(self.relative_path))[0]

    @property
    def extname(self) -> str:
        return os.path.splitext(self.relative_path)[1]

    @property
    def dir(self) -> str:
        return os.path.dirname(self.relative_path)

    @property
    def is_markdown(self) -> bool:
        return self.extname.lower().lstrip(".") in self.config.markdown_extensions

    @property
    def is_sass(self) -> bool:
        return self.extname.lower() in (".scss", ".sass")

    @property
    def has_yaml_header(self) -> bool:
        return True

    @property
    def date(self) -> dt.datetime | None:
        return self.data.get("date")

    @property
    def published(self) -> bool:
        if self.data.get("published") is False:
            return False
        date = self.data.get("date")
        if date is not None and not self.config.future:
            if date > dt.datetime.now(tz=date.tzinfo or dt.timezone.utc):
                return False
        return True

    @property
    def output(self) -> bool:
        return True

    # -- Liquid ------------------------------------------------------------
    def liquid_content(self) -> str:
        """Content after Liquid + Markdown, without layouts (Jekyll's `post.content`)."""
        if self.rendered is None:
            if self._content_fn is not None:
                self.rendered = self._content_fn(self)
            else:  # pragma: no cover - set by Site
                self.rendered = self.content
        return self.rendered

    def set_content_fn(self, fn: Callable[["BaseDocument"], str]) -> None:
        self._content_fn = fn

    def drop(self) -> "DocDrop":
        return DocDrop(self)


class Page(BaseDocument):
    kind = "page"

    @property
    def html(self) -> bool:
        return converter_output_ext(self.extname, self.config.markdown_extensions) == ".html"

    @property
    def is_index(self) -> bool:
        return self.basename_without_ext == "index"

    def compute_url(self) -> None:
        self.output_ext = permalink_extension(self.data.get("permalink")) or converter_output_ext(
            self.extname, self.config.markdown_extensions
        )
        if self.data.get("permalink") is None:
            if not self.html:
                template = "/:path/:basename:output_ext"
            elif self.is_index:
                template = "/:path/"
            else:
                template = add_permalink_suffix("/:path/:basename", self.config.permalink_style)
        else:
            template = None
        placeholders = {
            "path": os.path.dirname(self.relative_path),
            "basename": self.basename_without_ext,
            "output_ext": self.output_ext,
            "dir": os.path.dirname(self.relative_path) or "",
            "name": os.path.basename(self.relative_path),
        }
        self.url = build_url(template=template, permalink=self.data.get("permalink"),
                             placeholders=placeholders)
        self.destination = destination_for(self.url, self.output_ext)


class Document(BaseDocument):
    """A post or a member of another collection (``_notes``, ...)."""

    kind = "document"

    def __init__(self, *args: Any, label: str = "posts", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.label = label
        self.data.setdefault("ext", self.extname)
        self._populate_title_and_date()

    # -- front matter defaults ---------------------------------------------
    def _populate_title_and_date(self) -> None:
        match = DATE_FILENAME_RE.match(self.relative_path)
        slug = None
        if match:
            date_text, slug, _ext = match.groups()
            self.modify_date(date_text)
        else:
            match = DATELESS_FILENAME_RE.match(self.relative_path)
            if match:
                slug = match.group(1)
        if slug is None:
            slug = self.basename_without_ext
        slug = re.sub(r"\.*$", "", slug)

        self.data.setdefault("title", titleize_slug(slug))
        self.data.setdefault("slug", slug)
        if "date" not in self.data:
            mtime = os.path.getmtime(self.source_path)
            self.data["date"] = dt.datetime.fromtimestamp(
                mtime, tz=tzinfo(self.config.timezone_name)
            )
        elif not isinstance(self.data["date"], dt.datetime):
            self.data["date"] = parse_date(self.data["date"], tzinfo(self.config.timezone_name))
        elif self.data["date"].tzinfo is None:
            self.data["date"] = self.data["date"].replace(tzinfo=tzinfo(self.config.timezone_name))

        # categories derived from the path (Jekyll `categories_from_path`)
        parts = self.relative_path.split("/")
        derived = parts[1:-1] if len(parts) > 2 else []
        existing = self.data.get("categories")
        if isinstance(existing, str):
            existing = [existing]
        merged = list(dict.fromkeys(list(existing or []) + derived))
        if merged:
            self.data["categories"] = merged

    def modify_date(self, date_text: str) -> None:
        if self.data.get("date") is None:
            self.data["date"] = parse_date(date_text, tzinfo(self.config.timezone_name))

    # -- url ----------------------------------------------------------------
    @property
    def categories(self) -> list[str]:
        values = self.data.get("categories") or []
        if isinstance(values, str):
            values = [values]
        return [str(value).lower() for value in values]

    @property
    def slug(self) -> str:
        return str(self.data.get("slug") or self.basename_without_ext)

    @property
    def cleaned_relative_path(self) -> str:
        """Path without collection dir / extension (Jekyll `cleaned_relative_path`)."""
        prefix = "_" + self.label
        without_ext = self.relative_path.rsplit(".", 1)[0]
        cleaned = without_ext
        if without_ext.startswith(prefix + "/"):
            cleaned = without_ext[len(prefix) + 1:]
        elif without_ext == prefix:
            cleaned = ""
        return cleaned

    @property
    def id(self) -> str:
        return os.path.join(os.path.dirname(self.url), self.slug)

    def url_placeholders(self) -> dict[str, Any]:
        date = self.data.get("date")
        zone = tzinfo(self.config.timezone_name)
        if date is None:
            date = dt.datetime.now(tz=zone)

        def fmt(pattern: str) -> str:
            return date.strftime(pattern)

        return {
            "collection": self.label,
            "path": self.cleaned_relative_path,
            "basename": self.basename_without_ext,
            "name": slugify(self.basename_without_ext) or "",
            "title": slugify(self.slug, mode="pretty", cased=True) or "",
            "slug": slugify(self.slug) or "",
            "output_ext": self.output_ext,
            "year": fmt("%Y"),
            "month": fmt("%m"),
            "day": fmt("%d"),
            "hour": fmt("%H"),
            "minute": fmt("%M"),
            "second": fmt("%S"),
            "i_day": str(date.day),
            "i_month": str(date.month),
            "short_month": fmt("%b"),
            "long_month": fmt("%B"),
            "short_year": fmt("%y"),
            "y_day": fmt("%j"),
            "categories": "/".join(self.categories),
        }

    @property
    def permalink_template(self) -> str | None:
        metadata = self.config.collections.get(self.label) or {}
        if metadata.get("permalink"):
            return str(metadata["permalink"])
        return None

    def compute_url(self) -> None:
        self.output_ext = permalink_extension(self.data.get("permalink")) or converter_output_ext(
            self.extname, self.config.markdown_extensions
        )
        placeholders = self.url_placeholders()
        template = self.permalink_template
        if template is None:
            template = add_permalink_suffix("/:collection/:path", self.config.permalink_style)
        self.url = build_url(template=template, permalink=self.data.get("permalink"),
                             placeholders=placeholders)
        self.destination = destination_for(self.url, self.output_ext)

    @property
    def output(self) -> bool:
        metadata = self.config.collections.get(self.label) or {}
        return bool(metadata.get("output", True))


class DocDrop:
    """The Liquid view of a document (Jekyll's DocumentDrop / Page)."""

    __slots__ = ("_doc",)

    def __init__(self, doc: BaseDocument):
        self._doc = doc

    # -- explicit members ---------------------------------------------------
    @property
    def url(self) -> str:
        return self._doc.url

    @property
    def path(self) -> str:
        return self._doc.relative_path

    @property
    def content(self) -> str:
        return self._doc.liquid_content()

    @property
    def date(self) -> dt.datetime | None:
        return self._doc.data.get("date")

    @property
    def name(self) -> str:
        return os.path.basename(self._doc.relative_path)

    @property
    def dir(self) -> str:
        return os.path.dirname(self._doc.relative_path)

    @property
    def id(self) -> str:
        return getattr(self._doc, "id", self._doc.url)

    @property
    def output(self) -> bool:
        return self._doc.output

    @property
    def collection(self) -> str:
        return self._doc.collection

    @property
    def type(self) -> str:
        return self._doc.kind

    @property
    def next(self) -> "DocDrop | None":
        return getattr(self._doc, "next_doc", None)

    @property
    def previous(self) -> "DocDrop | None":
        return getattr(self._doc, "prev_doc", None)

    @property
    def excerpt(self) -> str:
        return str(self._doc.data.get("excerpt") or "")

    # -- front matter fallback ---------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        data = object.__getattribute__(self, "_doc").data
        return data.get(name)

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(type(self), key) and key in (
            "url", "path", "content", "date", "name", "dir", "id", "output",
            "collection", "type", "next", "previous", "excerpt",
        ):
            return getattr(self, key)
        return self._doc.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._doc.data

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocDrop {self._doc.relative_path}>"