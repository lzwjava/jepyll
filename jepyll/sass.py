"""SCSS -> CSS compilation.

Jekyll delegates this to ``jekyll-sass-converter``; jepyll supports two
backends (first one available wins):

1. ``libsass`` (Python package, ``pip install libsass``)
2. the ``sass`` command line binary (dart-sass / sassc)
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter


@dataclass
class SassResult:
    css: str
    source_map: str | None = None


class SassError(RuntimeError):
    pass


def _libsass():
    try:
        import sass  # type: ignore

        return sass
    except Exception:
        return None


def _cli() -> list[str] | None:
    for candidate in ("sass", "sassc"):
        binary = shutil.which(candidate)
        if not binary:
            continue
        try:
            probe = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return [binary]
    return None


def available() -> list[str]:
    names = []
    if _libsass() is not None:
        names.append("libsass")
    if _cli() is not None:
        names.append("sass-cli")
    return names


def compile_string(source: str, load_paths: list[str], style: str = "expanded") -> str:
    """Compile SCSS given as a string (front matter must already be removed)."""
    sass = _libsass()
    if sass is not None:
        try:
            return sass.compile(string=source, include_paths=load_paths, output_style=style)
        except Exception as exc:  # sass.CompileError
            raise SassError(str(exc)) from exc
    return _compile_with_cli(source, load_paths, style)


def compile_file(
    path: str | Path,
    load_paths: list[str],
    style: str = "expanded",
    source_map: bool = False,
) -> SassResult:
    """Compile an SCSS file, optionally producing a ``.map`` source map."""
    path = Path(path)
    data, body = frontmatter.split(path.read_text(encoding="utf-8"))

    sass = _libsass()
    if sass is not None:
        with tempfile.TemporaryDirectory(prefix="jepyll-sass-") as tmp:
            tmp_path = Path(tmp) / path.name
            tmp_path.write_text(body, encoding="utf-8")
            if source_map:
                try:
                    result = sass.compile(
                        filename=str(tmp_path),
                        include_paths=load_paths,
                        output_style=style,
                        source_map_filename=path.with_suffix(".css").name + ".map",
                        source_map_contents=True,
                    )
                except Exception:
                    result = None
                if isinstance(result, tuple):
                    css, source_map_text = result
                    css = _rewrite_map_url(css, path.with_suffix(".css").name + ".map")
                    return SassResult(css, source_map_text)
            try:
                css = sass.compile(
                    filename=str(tmp_path), include_paths=load_paths, output_style=style
                )
            except Exception as exc:
                raise SassError(f"{path}: {exc}") from exc
            if source_map:
                css = _rewrite_map_url(css, path.with_suffix(".css").name + ".map")
            return SassResult(css)

    css = _compile_with_cli(body, load_paths, style, str(path))
    return SassResult(css)


def _rewrite_map_url(css: str, map_name: str) -> str:
    marker = "/*# sourceMappingURL="
    if marker in css:
        head = css.split(marker, 1)[0]
        return head + f"{marker}{map_name} */"
    return css.rstrip() + f"\n{marker}{map_name} */\n"


def _compile_with_cli(
    source: str, load_paths: list[str], style: str, filename: str | None = None
) -> str:
    command = _cli()
    if command is None:
        raise SassError(
            "No SCSS compiler available: install `libsass` (pip install libsass) "
            "or the `sass` command line tool."
        )
    with tempfile.TemporaryDirectory(prefix="jepyll-sass-") as tmp:
        tmp_path = Path(tmp) / (Path(filename).name if filename else "input.scss")
        out_path = tmp_path.with_suffix(".css")
        tmp_path.write_text(source, encoding="utf-8")
        cmd = list(command)
        if Path(cmd[0]).name == "sassc":
            cmd += ["-s", style]
            for path in load_paths:
                cmd += ["-I", path]
            cmd += [str(tmp_path)]
        else:  # dart-sass
            cmd += [f"--style={style}", "--no-source-map", "--quiet"]
            for path in load_paths:
                cmd += [f"--load-path={path}"]
            cmd += [str(tmp_path), str(out_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SassError(f"failed to run {cmd[0]}: {exc}") from exc
        if proc.returncode != 0:
            raise SassError(proc.stderr.strip() or f"{cmd[0]} failed")
        if Path(cmd[0]).name == "sassc":
            return proc.stdout
        return out_path.read_text(encoding="utf-8")


def compile_to_file(
    source_path: str | Path,
    destination: str | Path,
    load_paths: list[str],
    style: str = "expanded",
    source_map: bool = True,
) -> Path:
    result = compile_file(source_path, load_paths, style=style, source_map=source_map)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(result.css, encoding="utf-8")
    if result.source_map is not None:
        map_path = Path(str(destination) + ".map")
        map_path.write_text(result.source_map, encoding="utf-8")
    return destination