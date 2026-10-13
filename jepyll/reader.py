"""Reads a site source directory: layouts, includes, collections, pages,
static files and data files (mirrors Jekyll's ``Reader``/``EntryFilter``)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .document import DATE_FILENAME_RE, DATELESS_FILENAME_RE, Document, Page, StaticFile
from .frontmatter import FrontMatterError, normalize_dates, split

_SPECIAL_LEADING = ("_", ".", "#", "~")


@dataclass
class Layout:
    name: str
    source_path: str
    data: dict[str, Any]
    content: str


@dataclass
class ReadResult:
    pages: list[Page] = field(default_factory=list)
    collections: dict[str, list[Document]] = field(default_factory=dict)
    static_files: list[StaticFile] = field(default_factory=list)
    layouts: dict[str, Layout] = field(default_factory=dict)
    includes: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class EntryFilter:
    """Jekyll's ``EntryFilter``: special/backup/excluded entries are skipped."""

    def __init__(self, config: Config):
        self.excludes = config.exclude
        self.includes = config.include

    @staticmethod
    def is_special(name: str) -> bool:
        return bool(name) and name[0] in _SPECIAL_LEADING

    def is_excluded(self, relative_path: str) -> bool:
        base = os.path.basename(relative_path)
        for pattern in self.excludes:
            if not pattern:
                continue
            clean = pattern.rstrip("/")
            if pattern.endswith("/"):
                if relative_path == clean or relative_path.startswith(clean + "/"):
                    return True
                continue
            if relative_path == clean or base == clean:
                return True
            if relative_path.startswith(clean + "/"):
                return True
            if any(ch in pattern for ch in "*?["):
                import fnmatch

                if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(base, pattern):
                    return True
        return False

    def reject(self, name: str, relative_path: str) -> bool:
        if name.endswith(".") or name.endswith("~"):
            return True
        if self.is_special(name):
            # explicitly included entries win (Jekyll `include:`)
            if name in self.includes or relative_path in self.includes:
                return False
            return True
        if self.is_excluded(relative_path):
            if name in self.includes or relative_path in self.includes:
                return False
            return True
        return False


class Reader:
    def __init__(self, config: Config, warnings: list[str] | None = None):
        self.config = config
        self.filter = EntryFilter(config)
        self.warnings = warnings if warnings is not None else []

    # ------------------------------------------------------------------ api
    def read(self) -> ReadResult:
        result = ReadResult(warnings=self.warnings)
        result.layouts = self._read_layouts()
        result.includes = self._read_includes()
        result.data = self._read_data()
        result.collections = self._read_collections()
        if self.config.show_drafts:
            drafts = self._read_drafts()
            result.collections.setdefault("posts", [])
            result.collections["posts"].extend(drafts)
        self._read_pages_and_static(result)
        return result

    # ------------------------------------------------------------ components
    def _read_layouts(self) -> dict[str, Layout]:
        directory = Path(self.config.source) / "_layouts"
        layouts: dict[str, Layout] = {}
        if not directory.is_dir():
            return layouts
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            data, content = self._frontmatter(text, str(path))
            name = path.stem
            layouts[name] = Layout(name, str(path), data or {}, content)
        return layouts

    def _read_includes(self) -> dict[str, str]:
        directory = Path(self.config.source) / "_includes"
        includes: dict[str, str] = {}
        if not directory.is_dir():
            return includes
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                includes[str(path.relative_to(directory))] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return includes

    def _read_data(self) -> dict[str, Any]:
        directory = Path(self.config.source) / "_data"
        data: dict[str, Any] = {}
        if not directory.is_dir():
            return data
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            relative = path.relative_to(directory)
            if ext in (".yaml", ".yml"):
                import yaml

                try:
                    value = yaml.safe_load(path.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(f"could not read data file {relative}: {exc}")
                    continue
            elif ext == ".json":
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(f"could not read data file {relative}: {exc}")
                    continue
            elif ext in (".csv", ".tsv"):
                import csv

                delimiter = "\t" if ext == ".tsv" else ","
                with path.open(encoding="utf-8", newline="") as fh:
                    value = list(csv.DictReader(fh, delimiter=delimiter))
            else:
                continue
            key_parts = list(relative.with_suffix("").parts)
            node = data
            for part in key_parts[:-1]:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    node = {}
            node[key_parts[-1]] = value
        return data

    # ----------------------------------------------------------- collections
    def _collection_dir(self, label: str) -> Path:
        return self.config.collections_dir / f"_{label}"

    def _read_collections(self) -> dict[str, list[Document]]:
        collections: dict[str, list[Document]] = {}
        for label in self.config.collections:
            collections[label] = self._read_collection(label)
        return collections

    def _read_collection(self, label: str) -> list[Document]:
        directory = self._collection_dir(label)
        docs: list[Document] = []
        if not directory.is_dir():
            return docs
        matcher = DATE_FILENAME_RE if label == "posts" else None
        for path in self._walk(directory):
            relative = str(path.relative_to(self.config.collections_dir))
            if matcher is not None and not matcher.match(relative):
                continue
            text = self._read_text(path)
            if text is None:
                continue
            data, content = self._frontmatter(text, str(path))
            if data is None:
                # files without front matter inside a collection are static
                continue
            data = normalize_dates(data, self.config.timezone_name)
            doc = Document(
                self.config,
                str(path),
                relative,
                data,
                content,
                collection=label,
                label=label,
            )
            if not self._publishable(doc):
                continue
            docs.append(doc)
        return docs

    def _publishable(self, doc: Document) -> bool:
        """Jekyll's ``Publisher#publish?`` (future posts & `published: false`)."""
        if doc.data.get("published") is False and not self.config.get("unpublished"):
            return False
        date = doc.data.get("date")
        if date is not None and date > _now(self.config.timezone_name) and not self.config.future:
            self.warnings.append(f"Skipping: {doc.relative_path} has a future date")
            return False
        return True

    def _read_drafts(self) -> list[Document]:
        directory = self.config.collections_dir / "_drafts"
        docs: list[Document] = []
        if not directory.is_dir():
            return docs
        for path in self._walk(directory):
            relative = str(path.relative_to(self.config.collections_dir))
            if not DATELESS_FILENAME_RE.match(relative):
                continue
            text = self._read_text(path)
            if text is None:
                continue
            data, content = self._frontmatter(text, str(path))
            if data is None:
                continue
            data = normalize_dates(data, self.config.timezone_name)
            doc = Document(self.config, str(path), relative, data, content,
                           collection="posts", label="posts")
            if self._publishable(doc):
                docs.append(doc)
        return docs

    # ----------------------------------------------------- pages & statics
    def _read_pages_and_static(self, result: ReadResult) -> None:
        source = Path(self.config.source)
        for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
            current = Path(dirpath)
            relative_dir = "" if current == source else str(current.relative_to(source))

            keep_dirs = []
            for name in sorted(dirnames):
                relative = f"{relative_dir}/{name}" if relative_dir else name
                full = current / name
                if full.is_symlink():
                    continue
                if self.filter.reject(name, relative):
                    continue
                keep_dirs.append(name)
            dirnames[:] = keep_dirs

            for name in sorted(filenames):
                relative = f"{relative_dir}/{name}" if relative_dir else name
                full = current / name
                if full.is_symlink():
                    continue
                if self.filter.reject(name, relative):
                    continue
                # Only files that actually start with YAML front matter become
                # pages; everything else (including binary assets) is copied.
                if not _has_front_matter(full):
                    result.static_files.append(StaticFile(self.config.source, str(full), relative))
                    continue
                text = self._read_text(full)
                if text is None:
                    continue
                try:
                    data, content = self._frontmatter(text, str(full))
                except FrontMatterError as exc:
                    self.warnings.append(str(exc))
                    continue
                if data is None:  # pragma: no cover - guarded by _has_front_matter
                    result.static_files.append(StaticFile(self.config.source, str(full), relative))
                    continue
                data = normalize_dates(data, self.config.timezone_name)
                page = Page(self.config, str(full), relative, data, content)
                page.compute_url()
                result.pages.append(page)

    # --------------------------------------------------------------- helpers
    def _walk(self, directory: Path) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = [
                name for name in sorted(dirnames)
                if not name.startswith((".", "#", "~"))
                and not (current / name).is_symlink()
            ]
            for name in sorted(filenames):
                if name.startswith((".", "#", "~")) or name.endswith(("~", ".")):
                    continue
                path = current / name
                if path.is_symlink():
                    continue
                yield path

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _frontmatter(self, text: str, source_path: str) -> tuple[dict[str, Any] | None, str]:
        try:
            data, content = split(text)
        except FrontMatterError as exc:
            if self.config.get("strict_front_matter"):
                raise
            self.warnings.append(f"{source_path}: {exc}")
            return None, text
        return data, content


def has_liquid_construct(content: str) -> bool:
    """Jekyll skips the Liquid pass entirely when a document has no tags."""
    return bool(content) and ("{" + "%" in content or "{" + "{" in content)


_FM_HEADER_RE = re.compile(rb"\A(?:\xef\xbb\xbf)?---[ \t]*\r?\n")


def _has_front_matter(path) -> bool:
    """Cheap binary-safe check (Jekyll's `has_yaml_header?`)."""
    try:
        with open(path, "rb") as handle:
            first_line = handle.readline(4096)
    except OSError:
        return False
    return _FM_HEADER_RE.match(first_line) is not None


def _now(timezone_name: str | None):
    import datetime as dt

    from .dates import tzinfo

    return dt.datetime.now(tz=tzinfo(timezone_name))