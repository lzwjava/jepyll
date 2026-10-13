"""The Site: builds payloads, renders documents, writes output.

Rendering order follows Jekyll's ``Renderer#render_document``:

1. Liquid pass over the document body (skipped when there are no tags)
2. Markdown conversion for ``.md``-family files
3. ``_layouts`` chain (each layout rendered with Liquid, ``{{ content }}``
   replaced by the result of the previous step)
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

from . import JEKYLL_COMPAT_VERSION, sass
from .config import Config
from .converters import MarkdownConverter
from .document import BaseDocument, Document, Page, StaticFile
from .liquid import Environment
from .liquid.filters import xml_escape
from .liquid.tags import build_custom_tags
from .reader import Reader, ReadResult, has_liquid_construct
from .urls import sanitize_url


class RenderError(Exception):
    """Raised when a single document fails to render."""


class Site:
    def __init__(self, config: Config):
        self.config = config
        self.warnings: list[str] = []
        self.time = dt.datetime.now(tz=_tz(config.timezone_name))
        self.layouts: dict[str, Any] = {}
        self.includes: dict[str, str] = {}
        self.data: dict[str, Any] = {}
        self.pages: list[Page] = []
        self.collections: dict[str, list[Document]] = {}
        self.static_files: list[StaticFile] = []
        self.docs: list[BaseDocument] = []  # everything that gets rendered
        self.payload: dict[str, Any] = {}
        self.env = Environment(loader=self._load_include, custom_tags=build_custom_tags())
        self.markdown = MarkdownConverter(config)
        self.processed = False
        self.renderable: list[BaseDocument] = []
        self.stats: dict[str, Any] = {}

    # ------------------------------------------------------------------ read
    def process(self) -> None:
        if self.processed:
            return
        started = time.time()
        self._check_plugins()
        result: Reader = Reader(self.config, self.warnings)
        read: ReadResult = result.read()
        self.layouts = read.layouts
        self.includes = read.includes
        self.data = read.data
        self.pages = read.pages
        self.collections = read.collections
        self.static_files = read.static_files
        # Reader was handed self.warnings, so `read.warnings` is the same list.

        for docs in self.collections.values():
            for doc in docs:
                doc.compute_url()

        self.docs = [*self.pages, *(doc for docs in self.collections.values() for doc in docs)]
        for doc in self.docs:
            doc.set_content_fn(self.content_of)

        self.payload = self._build_payload()
        self.renderable = [
            doc for doc in self.docs
            if doc.output and doc.published and not doc.is_sass
        ]
        self.processed = True
        self.stats["read_seconds"] = time.time() - started

    # --------------------------------------------------------------- payload
    SUPPORTED_PLUGINS = {"jekyll-sitemap", "jekyll-seo-tag", "jekyll-include-cache"}

    def _check_plugins(self) -> None:
        for plugin in self.config.get("plugins") or []:
            if plugin not in self.SUPPORTED_PLUGINS:
                self.warnings.append(
                    f"plugin {plugin!r} is not supported by jepyll "
                    f"(only {', '.join(sorted(self.SUPPORTED_PLUGINS))} are emulated)"
                )

    def _build_payload(self) -> dict[str, Any]:
        site: dict[str, Any] = dict(self.config)
        site["time"] = self.time
        site["data"] = self.data
        site["jekyll_version"] = JEKYLL_COMPAT_VERSION

        ordered = self.ordered_posts()
        post_drops = [doc.drop() for doc in ordered]
        site["posts"] = post_drops

        # chronological neighbours for `post.next` / `post.previous`
        for index, doc in enumerate(ordered):
            doc.prev_doc = ordered[index - 1].drop() if index > 0 else None  # type: ignore[attr-defined]
            doc.next_doc = ordered[index + 1].drop() if index + 1 < len(ordered) else None  # type: ignore[attr-defined]

        site["pages"] = [page.drop() for page in self.pages]
        site["static_files"] = [static.drop() for static in self.static_files]
        site["html_pages"] = [
            page.drop() for page in self.pages
            if page.html or page.url.endswith("/")
        ]
        site["documents"] = [
            doc.drop()
            for docs in self.collections.values()
            for doc in docs
            if doc.output and doc.published
        ]
        site["collections"] = [
            {
                "label": label,
                "docs": [doc.drop() for doc in docs],
                "files": [],
                "output": bool((self.config.collections.get(label) or {}).get("output", True)),
                "relative_directory": f"_{label}",
            }
            for label, docs in sorted(self.collections.items())
        ]
        for label, docs in self.collections.items():
            if label == "posts":
                continue
            site[label] = [doc.drop() for doc in docs]

        site["tags"] = self._post_attr_hash("tags", ordered)
        site["categories"] = self._post_attr_hash("categories", ordered)
        return site

    def ordered_posts(self) -> list[Document]:
        """Posts newest first (Jekyll's ``SiteDrop#posts``)."""
        fallback = dt.datetime.min.replace(tzinfo=_tz(self.config.timezone_name))
        posts = self.collections.get("posts", [])
        return sorted(
            posts,
            key=lambda d: (d.data.get("date") or fallback, d.relative_path),
            reverse=True,
        )

    def _post_attr_hash(self, attribute: str, ordered: list[Document]) -> dict[str, list[Any]]:
        buckets: dict[str, list[Any]] = {}
        for doc in ordered:
            values = doc.data.get(attribute) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                buckets.setdefault(str(value), []).append(doc.drop())
        return buckets

    def _load_include(self, name: str) -> str:
        if name in self.includes:
            return self.includes[name]
        raise FileNotFoundError(name)

    # --------------------------------------------------------------- render
    def registers(self, doc: BaseDocument | None = None) -> dict[str, Any]:
        registers: dict[str, Any] = {
            "site": self.payload,
            "environment": self.env,
            "_warnings": self.warnings,
        }
        if doc is not None:
            registers["page"] = doc.drop()
        return registers

    def context(self, doc: BaseDocument, content: str | None = None,
                layout_data: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "site": self.payload,
            "page": doc.drop(),
            "jekyll": {
                "version": JEKYLL_COMPAT_VERSION,
                "environment": "production",
                "mode": "build",
            },
        }
        if content is not None:
            ctx["content"] = content
        if layout_data is not None:
            ctx["layout"] = layout_data
        return ctx

    def content_of(self, doc: BaseDocument) -> str:
        """Liquid + Markdown for a document body (no layouts)."""
        if doc.rendered is not None:
            return doc.rendered
        content = doc.content
        if has_liquid_construct(content):
            try:
                content = self.env.render(
                    content,
                    self.context(doc),
                    self.registers(doc),
                    name=doc.relative_path,
                    cache=False,
                )
            except Exception as exc:
                raise RenderError(f"{doc.relative_path}: {exc}") from exc
        if doc.is_markdown:
            content = self.markdown.convert(content)
        doc.rendered = content
        return content

    def render(self, doc: BaseDocument) -> str:
        output = self.content_of(doc)
        layout_name = doc.data.get("layout")
        seen: set[str] = set()
        while layout_name and layout_name != "none":
            if layout_name in seen:
                self.warnings.append(f"{doc.relative_path}: circular layout chain at {layout_name!r}")
                break
            seen.add(layout_name)
            layout = self.layouts.get(layout_name)
            if layout is None:
                self.warnings.append(
                    f"Build Warning: Layout {layout_name!r} requested by {doc.relative_path} "
                    f"does not exist"
                )
                break
            payload = self.context(doc, content=output, layout_data=layout.data)
            output = self.env.render(
                layout.content, payload, self.registers(doc), name=f"_layouts/{layout.name}"
            )
            layout_name = layout.data.get("layout")
        return output

    def _generate_sitemap(self) -> bool:
        """Emulate the ``jekyll-sitemap`` plugin when the site has no
        ``sitemap.xml`` of its own."""
        if "jekyll-sitemap" not in (self.config.get("plugins") or []):
            return False
        if any(page.relative_path == "sitemap.xml" for page in self.pages):
            return False
        baseurl = str(self.config.get("baseurl") or "")
        url = str(self.config.get("url") or "")

        def loc(path: str) -> str:
            return url + sanitize_url(f"{baseurl}{path}".replace("index.html", ""))

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/sitemap/0.9">',
        ]
        for page in self.pages:
            if page.data.get("sitemap") is False:
                continue
            lines.append(f"  <url><loc>{xml_escape(loc(page.url))}</loc></url>")
        for doc in self.collections.get("posts", []):
            if not (doc.output and doc.published):
                continue
            lines.append(f"  <url><loc>{xml_escape(loc(doc.url))}</loc></url>")
        for docs in self.collections.values():
            if docs is self.collections.get("posts"):
                continue
            for doc in docs:
                if doc.output and doc.published:
                    lines.append(f"  <url><loc>{xml_escape(loc(doc.url))}</loc></url>")
        lines.append("</urlset>")
        destination = Path(self.config.destination) / "sitemap.xml"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    # ------------------------------------------------------------------ sass
    def render_sass(self) -> list[tuple[BaseDocument, str]]:
        """Compile SCSS documents (files with front matter)."""
        results: list[tuple[BaseDocument, str]] = []
        load_paths = self.config.sass_load_paths()
        style = str((self.config.get("sass") or {}).get("style") or "expanded")
        source_map = bool((self.config.get("sass") or {}).get("source_map", True))
        for doc in self.docs:
            if not doc.is_sass:
                continue
            try:
                result = sass.compile_file(
                    doc.source_path, load_paths, style=style, source_map=source_map
                )
            except sass.SassError as exc:
                raise RenderError(f"SCSS error in {doc.relative_path}: {exc}") from exc
            results.append((doc, result.css))
            if result.source_map is not None:
                doc.data["_source_map"] = result.source_map
        return results

    # ------------------------------------------------------------------ write
    def expected_paths(self, sass_outputs: Iterable[tuple[BaseDocument, str]] = ()) -> set[str]:
        expected: set[str] = set()
        for doc in self.renderable:
            expected.add(doc.destination.lstrip("/"))
        for doc, _css in sass_outputs:
            expected.add(doc.destination.lstrip("/"))
            expected.add(doc.destination.lstrip("/") + ".map")
        for static in self.static_files:
            expected.add(static.destination_relative.lstrip("/"))
        return expected

    def write_document(self, doc: BaseDocument, output: str | None = None) -> str:
        if output is None:
            output = self.render(doc)
        destination = Path(self.config.destination) / doc.destination.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
        return str(destination)

    def write_static_files(self) -> int:
        destination_root = Path(self.config.destination)
        written = 0
        for static in self.static_files:
            source = Path(static.source_path)
            target = destination_root / static.destination_relative
            try:
                source_stat = source.stat()
            except OSError:
                continue
            if target.exists():
                try:
                    target_stat = target.stat()
                except OSError:
                    target_stat = None
                if (
                    target_stat is not None
                    and target_stat.st_size == source_stat.st_size
                    and int(target_stat.st_mtime) == int(source_stat.st_mtime)
                ):
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            written += 1
        return written

    def prune_stale(self, expected: set[str]) -> int:
        """Remove files in the destination that this build did not produce."""
        destination = Path(self.config.destination)
        if not destination.is_dir():
            return 0
        keep = [k.strip("/") for k in (self.config.get("keep_files") or [])]
        removed = 0
        for dirpath, dirnames, filenames in os.walk(destination, topdown=True):
            current = Path(dirpath)
            relative_dir = "" if current == destination else str(current.relative_to(destination))
            preserved = []
            for name in list(dirnames):
                relative = f"{relative_dir}/{name}" if relative_dir else name
                if relative in keep:
                    continue
                preserved.append(name)
            dirnames[:] = preserved
            for name in filenames:
                relative = f"{relative_dir}/{name}" if relative_dir else name
                if relative in expected or relative in keep:
                    continue
                try:
                    (current / name).unlink()
                    removed += 1
                except OSError:  # pragma: no cover
                    pass
        # drop now-empty directories (except kept ones)
        for dirpath, dirnames, filenames in os.walk(destination, topdown=False):
            current = Path(dirpath)
            if current == destination:
                continue
            relative = str(current.relative_to(destination))
            if relative in keep:
                continue
            if not filenames and not dirnames:
                try:
                    current.rmdir()
                except OSError:  # pragma: no cover
                    pass
        return removed

    # ----------------------------------------------------------------- build
    def build(self, jobs: int = 1, verbose: bool = True) -> dict[str, Any]:
        """Read, render and write the whole site."""
        self.process()
        started = time.time()

        sass_outputs = self.render_sass()
        for doc, css in sass_outputs:
            destination = Path(self.config.destination) / doc.destination.lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(css, encoding="utf-8")
            source_map = doc.data.pop("_source_map", None)
            if source_map:
                Path(str(destination) + ".map").write_text(source_map, encoding="utf-8")

        errors = self._render_all(jobs=jobs, verbose=verbose)
        sitemap_generated = self._generate_sitemap()
        static_written = self.write_static_files()
        expected = self.expected_paths(sass_outputs)
        if sitemap_generated:
            expected.add("sitemap.xml")
        removed = self.prune_stale(expected)

        stats = {
            "pages": len(self.pages),
            "posts": len(self.collections.get("posts", [])),
            "collections": {label: len(docs) for label, docs in self.collections.items()},
            "rendered": len(self.renderable),
            "static_files": len(self.static_files),
            "static_copied": static_written,
            "stale_removed": removed,
            "warnings": list(self.warnings),
            "errors": errors,
            "seconds": time.time() - started,
        }
        self.stats.update(stats)
        return stats

    def _render_all(self, jobs: int = 1, verbose: bool = True) -> list[str]:
        total = len(self.renderable)
        errors: list[str] = []
        if total == 0:
            return errors

        if jobs > 1 and _fork_available():
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor, as_completed

            global _WORKER_SITE
            _WORKER_SITE = self
            context = mp.get_context("fork")
            done = 0
            try:
                with ProcessPoolExecutor(max_workers=jobs, mp_context=context) as pool:
                    futures = [pool.submit(_render_index, index) for index in range(total)]
                    for future in as_completed(futures):
                        index, error, warnings = future.result()
                        if error:
                            errors.append(error)
                        self.warnings.extend(warnings)
                        done += 1
                        if verbose and (done % 2000 == 0 or done == total):
                            print(f"  rendered {done}/{total}", flush=True)
            finally:
                _WORKER_SITE = None
            return errors

        for index, doc in enumerate(self.renderable):
            error, _warnings = _render_document(doc, self)
            if error:
                errors.append(error)
            if verbose and ((index + 1) % 2000 == 0 or index + 1 == total):
                print(f"  rendered {index + 1}/{total}", flush=True)
        return errors


def _render_document(doc: BaseDocument, site: Site) -> tuple[str | None, list[str]]:
    """Render one document and write it (shared by serial and worker paths)."""
    before = len(site.warnings)
    try:
        site.write_document(doc)
    except Exception as exc:  # noqa: BLE001 - one bad post must not kill a build
        return f"{doc.relative_path}: {type(exc).__name__}: {exc}", site.warnings[before:]
    return None, site.warnings[before:]


_WORKER_SITE: Site | None = None


def _render_index(index: int) -> tuple[int, str | None, list[str]]:
    site = _WORKER_SITE
    if site is None:  # pragma: no cover - defensive
        return index, "worker site not initialised", []
    doc = site.renderable[index]
    error, warnings = _render_document(doc, site)
    return index, error, warnings


def _fork_available() -> bool:
    import sys

    return sys.platform != "win32" and "fork" in __import__("multiprocessing").get_all_start_methods()


def _tz(name: str | None) -> dt.tzinfo:
    from .dates import tzinfo

    return tzinfo(name)