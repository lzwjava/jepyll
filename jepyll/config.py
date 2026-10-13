"""Site configuration: loading and defaulting ``_config.yml`` like Jekyll does."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from . import JEKYLL_COMPAT_VERSION

# Directories/files that are never copied into the site (mirrors Jekyll's
# Jekyll::Configuration::DEFAULT_EXCLUDES - user "exclude" entries are merged
# with these, exactly like `add_default_excludes` in the Ruby source).
DEFAULT_EXCLUDES = [
    ".sass-cache",
    ".jekyll-cache",
    ".ruby-lsp",
    "gemfiles",
    "Gemfile",
    "Gemfile.lock",
    "node_modules",
    "vendor/bundle/",
    "vendor/cache/",
    "vendor/gems/",
    "vendor/ruby/",
]

# Built-in permalink styles (Jekyll::Configuration::STYLE_TO_PERMALINK).
STYLE_TO_PERMALINK = {
    "none": "/:categories/:title:output_ext",
    "date": "/:categories/:year/:month/:day/:title:output_ext",
    "ordinal": "/:categories/:year/:y_day/:title:output_ext",
    "pretty": "/:categories/:year/:month/:day/:title/",
    "weekdate": "/:categories/:year/W:week/:short_day/:title:output_ext",
}

# Values Jekyll ships with (only the ones that matter for rendering).
DEFAULTS: dict[str, Any] = {
    "source": ".",
    "destination": "_site",
    "collections_dir": "",
    "keep_files": [".git", ".svn"],
    "encoding": "utf-8",
    "markdown_ext": "markdown,mkdown,mkdn,mkd,md",
    "strict_front_matter": False,
    "show_drafts": False,
    "future": False,
    "unpublished": False,
    "limit_posts": 0,
    "plugins": [],
    "theme": None,
    "markdown": "kramdown",
    "highlighter": "rouge",
    "lsi": False,
    "excerpt_separator": "\n\n",
    "permalink": "date",
    "timezone": None,
    "exclude": [],
    "include": [],
    "defaults": [],
    "collections": {},
    "sass": {},
    "page_excerpts": False,
    "baseurl": "",
    "url": "",
    "jsonify_normalize_opts": None,
}

MARKDOWN_EXTENSIONS = {
    ext.strip()
    for ext in DEFAULTS["markdown_ext"].split(",")
    if ext.strip()
}


def _stringify_keys(data: Any) -> Any:
    """Recursively convert mapping keys to strings (Jekyll `stringify_keys`)."""
    if isinstance(data, dict):
        return {str(k): _stringify_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_stringify_keys(v) for v in data]
    return data


def style_to_permalink(style: Any) -> str:
    """Resolve a `permalink` config value to a permalink template."""
    if isinstance(style, str) and style in STYLE_TO_PERMALINK:
        return STYLE_TO_PERMALINK[style]
    if isinstance(style, str):
        return style
    return str(style)


class Config(dict):
    """A dict of configuration values with the helpers Jekyll's Site needs."""

    def __init__(self, data: dict[str, Any], source: str, config_files: list[str]):
        super().__init__(data)
        self.source = str(Path(source).resolve())
        self.destination_raw = data["destination"]
        self.config_files = config_files

    # ------------------------------------------------------------------ load
    @classmethod
    def load(
        cls,
        source: str = ".",
        config_files: list[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> "Config":
        source = str(Path(source).resolve())
        if config_files is None:
            candidate = Path(source) / "_config.yml"
            config_files = [str(candidate)] if candidate.exists() else []

        data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        for path in config_files:
            p = Path(path)
            if not p.exists():
                continue
            with p.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{p} is not a YAML mapping")
            data.update(_stringify_keys(loaded))

        if overrides:
            data.update(_stringify_keys(overrides))

        data = cls._add_default_collections(data)
        data["exclude"] = list(dict.fromkeys(list(data.get("exclude") or []) + DEFAULT_EXCLUDES))
        data["plugins"] = list(data.get("plugins") or [])
        data["jekyll_version"] = JEKYLL_COMPAT_VERSION
        return cls(data, source, config_files)

    @staticmethod
    def _add_default_collections(data: dict[str, Any]) -> dict[str, Any]:
        """Mirror `Configuration#add_default_collections`."""
        collections = data.get("collections")
        if collections is None:
            data["collections"] = {}
            return data
        if isinstance(collections, list):  # `collections: [foo, bar]`
            collections = {str(name): {} for name in collections}
        merged: dict[str, Any] = {"posts": {}}
        for label, meta in collections.items():
            merged.setdefault(str(label), {})
            if meta:
                merged[str(label)].update(meta)
        merged["posts"]["output"] = True
        if data.get("permalink"):
            merged["posts"].setdefault("permalink", style_to_permalink(data["permalink"]))
        data["collections"] = merged
        return data

    # -------------------------------------------------------------- accessors
    @property
    def destination(self) -> Path:
        dest = Path(self.destination_raw)
        if not dest.is_absolute():
            dest = Path(self.source) / dest
        return dest.resolve()

    @property
    def collections_dir(self) -> Path:
        """Directory that contains `_posts` & friends (Jekyll `collections_dir`)."""
        rel = (self.get("collections_dir") or "").strip("/")
        return (Path(self.source) / rel) if rel else Path(self.source)

    @property
    def permalink_style(self) -> str:
        return str(self.get("permalink") or "date")

    @property
    def collections(self) -> dict[str, dict[str, Any]]:
        return dict(self.get("collections") or {})

    @property
    def exclude(self) -> list[str]:
        return list(self.get("exclude") or [])

    @property
    def include(self) -> list[str]:
        return list(self.get("include") or [])

    @property
    def show_drafts(self) -> bool:
        return bool(self.get("show_drafts"))

    @property
    def future(self) -> bool:
        return bool(self.get("future"))

    @property
    def theme(self) -> str | None:
        return self.get("theme")

    @property
    def timezone_name(self) -> str:
        return str(self.get("timezone") or "UTC")

    @property
    def markdown_extensions(self) -> set[str]:
        return MARKDOWN_EXTENSIONS

    # ---------------------------------------------------------------- helpers
    def in_source_dir(self, *parts: str) -> str:
        return str(Path(self.source).joinpath(*parts))

    def in_dest_dir(self, *parts: str) -> str:
        return str(Path(self.destination).joinpath(*parts))

    def sass_load_paths(self) -> list[str]:
        """Load paths for `@import` resolution: `_sass` plus bundled themes."""
        paths = [self.in_source_dir("_sass")]
        theme = self.theme
        if theme:
            bundled = Path(__file__).parent / "data" / "themes" / theme / "_sass"
            if bundled.is_dir():
                paths.append(str(bundled))
            site_theme = Path(self.source) / "_sass" / theme
            if site_theme.is_dir():
                paths.append(str(site_theme))
        site_sass = Path(self.source) / "_sass"
        if site_sass.is_dir() and str(site_sass) not in paths:
            paths.append(str(site_sass))
        return paths