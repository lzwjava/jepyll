"""Command line interface: ``jepyll build | serve | clean | new``."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .site import Site


def _common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-s", "--source", default=".", help="site source directory (default: .)")
    parser.add_argument("-d", "--destination", help="output directory (default: _site)")
    parser.add_argument(
        "-c", "--config", action="append", dest="configs",
        help="config file(s), can be given multiple times (default: _config.yml)",
    )
    parser.add_argument("--drafts", action="store_true", help="include _drafts")
    parser.add_argument("--future", action="store_true", help="include posts with a future date")
    parser.add_argument("-q", "--quiet", action="store_true", help="less output")


def _build_config(args: argparse.Namespace) -> Config:
    overrides: dict[str, Any] = {}
    if args.destination:
        overrides["destination"] = args.destination
    if getattr(args, "drafts", False):
        overrides["show_drafts"] = True
    if getattr(args, "future", False):
        overrides["future"] = True
    return Config.load(args.source, config_files=args.configs, overrides=overrides or None)


def _print_warnings(site: Site, limit: int | None = 20) -> None:
    if not site.warnings:
        return
    shown = site.warnings if limit is None else site.warnings[:limit]
    for warning in shown:
        print(f"  warning: {warning}", file=sys.stderr)
    if limit is not None and len(site.warnings) > limit:
        print(f"  ... and {len(site.warnings) - limit} more warnings", file=sys.stderr)


def _summary(config: Config, stats: dict[str, Any], elapsed: float, quiet: bool) -> None:
    destination = config.destination
    collections = ", ".join(
        f"{count} {label}" for label, count in stats.get("collections", {}).items()
    )
    if not quiet:
        print(f"  pages: {stats.get('pages', 0)}"
              + (f" ({collections})" if collections else ""))
        print(f"  rendered: {stats.get('rendered', 0)} documents")
        print(f"  static files: {stats.get('static_files', 0)}"
              f" ({stats.get('static_copied', 0)} copied,"
              f" {stats.get('stale_removed', 0)} stale removed)")
    print(f"  done in {elapsed:.1f}s -> {destination}")


def command_build(args: argparse.Namespace) -> int:
    config = _build_config(args)
    print(f"jepyll {__version__} - building {config.source}")
    site = Site(config)
    import time

    started = time.time()
    stats = site.build(jobs=max(1, args.jobs), verbose=not args.quiet)
    _print_warnings(site, limit=None if args.trace else 20)
    _summary(config, stats, time.time() - started, args.quiet)
    errors = stats.get("errors") or []
    if errors:
        for error in errors:
            print(f"  error: {error}", file=sys.stderr)
        print(f"{len(errors)} error(s) - build failed", file=sys.stderr)
        return 1
    return 0


def command_clean(args: argparse.Namespace) -> int:
    config = _build_config(args)
    destination = config.destination
    if not destination.exists():
        print(f"nothing to clean ({destination} does not exist)")
        return 0
    import shutil

    keep = [k.strip("/") for k in (config.get("keep_files") or [])]
    removed = 0
    for entry in sorted(destination.iterdir()):
        if entry.name in keep:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    print(f"cleaned {removed} entries from {destination}")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    from .server import find_free_port, serve

    config = _build_config(args)
    if args.destination:
        config["destination"] = args.destination

    site = Site(config)
    print(f"jepyll {__version__} - serving {config.source}")
    site.build(jobs=max(1, args.jobs), verbose=not args.quiet)
    _print_warnings(site)
    errors = site.stats.get("errors") or []
    if errors:
        for error in errors:
            print(f"  error: {error}", file=sys.stderr)

    def rebuild() -> None:
        fresh = Site(_build_config(args))
        fresh.build(jobs=max(1, args.jobs), verbose=False)
        _print_warnings(fresh, limit=10)

    port = find_free_port(args.host, args.port)
    watch_skip = {_relative_destination(config)}
    serve(
        config.destination,
        host=args.host,
        port=port,
        rebuild=rebuild if not args.no_watch else None,
        watch=Path(config.source),
        watch_skip=watch_skip,
    )
    return 0


def _relative_destination(config: Config) -> str:
    destination = config.get("destination") or "_site"
    return str(Path(destination))


def command_new(args: argparse.Namespace) -> int:
    config = _build_config(args)
    slug = slugify_title(args.title)
    today = dt.date.today().isoformat()
    name = f"{today}-{slug}.md"

    if args.collection == "posts":
        target_dir = Path(config.collections_dir) / "_posts"
        if args.lang:
            target_dir = target_dir / args.lang
            name = f"{today}-{slug}-{args.lang}.md"
    else:
        target_dir = Path(config.collections_dir) / f"_{args.collection}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    if target.exists():
        print(f"error: {target} already exists", file=sys.stderr)
        return 1

    title = args.title
    target.write_text(
        "---\n"
        f"layout: {args.layout}\n"
        f"title: {title}\n"
        f"date: {today}\n"
        + (f"lang: {args.lang}\n" if args.lang else "")
        + "---\n\n"
        f"# {title}\n\nWrite your post here.\n",
        encoding="utf-8",
    )
    print(f"created {target}")
    return 0


def slugify_title(title: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", title).strip("-").lower()
    return slug or "untitled"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jepyll",
        description="jepyll - a small Jekyll-compatible static site generator",
    )
    parser.add_argument("--version", action="version", version=f"jepyll {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build the site")
    _common_options(build)
    build.add_argument("-j", "--jobs", type=int, default=1,
                       help="render in N parallel processes (Linux/macOS)")
    build.add_argument("--trace", action="store_true", help="print every warning")
    build.set_defaults(func=command_build)

    serve = subparsers.add_parser("serve", help="build, serve and rebuild on changes")
    _common_options(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4000)
    serve.add_argument("--no-watch", action="store_true", help="do not rebuild on changes")
    serve.add_argument("-j", "--jobs", type=int, default=1)
    serve.set_defaults(func=command_serve)

    clean = subparsers.add_parser("clean", help="remove the generated site")
    _common_options(clean)
    clean.set_defaults(func=command_clean)

    new = subparsers.add_parser("new", help="create a new post/collection entry")
    _common_options(new)
    new.add_argument("title", help="post title")
    new.add_argument("--lang", help="language suffix, e.g. en or zh")
    new.add_argument("--collection", default="posts", help="collection label (default: posts)")
    new.add_argument("--layout", default="post", help="layout name (default: post)")
    new.set_defaults(func=command_new)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        if getattr(args, "trace", False):
            raise
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())