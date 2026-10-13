"""Development server: serve the built site and rebuild on source changes."""

from __future__ import annotations

import http.server
import os
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Callable


def _iter_source_files(source: Path, skip: set[str]):
    """Yield ``(path, mtime, size)`` for every file that affects the build."""
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(dirpath)
        relative_dir = "" if current == source else str(current.relative_to(source))
        kept = []
        for name in dirnames:
            relative = f"{relative_dir}/{name}" if relative_dir else name
            # hidden dirs are ignored; `_site` (the destination) is skipped by
            # the caller, every other `_`-dir is content and must be watched.
            if name.startswith((".", "#", "~")) or relative in skip:
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name.startswith((".", "#", "~")) or name.endswith("~"):
                continue
            path = current / name
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if relative in skip:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            yield relative, int(stat.st_mtime), stat.st_size


def snapshot(source: Path, skip: set[str]) -> dict[str, tuple[int, int]]:
    return dict(_iter_source_files(source, skip))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler that also resolves extensionless URLs like Jekyll does.

    Jekyll's dev server maps ``/my-post`` -> ``my-post.html`` (and
    ``/my-post/`` -> ``my-post/index.html``), which is exactly what this
    site's ``permalink: /:title`` URLs rely on.
    """

    def translate_path(self, path: str) -> str:  # noqa: D102
        resolved = super().translate_path(path)
        if os.path.exists(resolved):
            return resolved
        for suffix in (".html", ".htm", ".xml", ".txt", ".json"):
            candidate = resolved + suffix
            if os.path.exists(candidate):
                return candidate
        for candidate in (os.path.join(resolved, "index.html"),
                          os.path.join(resolved, "index.htm")):
            if os.path.exists(candidate):
                return candidate
        return resolved

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if self.path not in ("/favicon.ico",):
            super().log_message(fmt, *args)


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve(
    destination: Path,
    host: str = "127.0.0.1",
    port: int = 4000,
    rebuild: Callable[[], None] | None = None,
    watch: Path | None = None,
    watch_skip: set[str] | None = None,
    poll_interval: float = 1.0,
    verbose: bool = True,
) -> None:
    """Serve ``destination`` and call ``rebuild()`` whenever sources change."""

    def handler(*args, **kwargs):  # pragma: no cover - trivial glue
        kwargs.setdefault("directory", str(destination))
        return QuietHandler(*args, **kwargs)

    server = ReusableTCPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"\nServing {destination} at http://{host}:{port}/ (ctrl-c to stop)")

    if rebuild is not None and watch is not None:
        skip = watch_skip or set()
        baseline = snapshot(watch, skip)
        while True:
            time.sleep(poll_interval)
            try:
                current = snapshot(watch, skip)
            except OSError:  # pragma: no cover - transient FS errors
                continue
            if current != baseline:
                baseline = current
                started = time.time()
                try:
                    rebuild()
                except Exception as exc:  # noqa: BLE001
                    print(f"  rebuild failed: {exc}")
                else:
                    if verbose:
                        print(f"  rebuilt in {time.time() - started:.2f}s")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.shutdown()


def find_free_port(host: str, preferred: int) -> int:
    for port in (preferred, preferred + 1, preferred + 2):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"no free port near {preferred}")