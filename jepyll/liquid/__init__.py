"""A compact Liquid implementation for jepyll.

Supported tags: ``assign``, ``capture``, ``if``/``elsif``/``else``,
``unless``, ``case``/``when``, ``for`` (with ``limit``/``offset``/``reversed``
and ``else``), ``include``, ``raw``, ``comment`` plus the Jekyll-specific
custom tags registered by the site builder (``seo``, ``highlight``).

Whitespace control (``{%- ... -%}``), filter chains, dynamic lookups such as
``site[type]`` and ``forloop`` variables all behave like Liquid 4.
"""

from __future__ import annotations

from typing import Any, Callable

from .errors import LiquidError, LiquidRenderError, LiquidSyntaxError
from .expr import Context, parse_expression
from .filters import STANDARD_FILTERS, make_url_filters, to_s
from .lexer import tokenize
from .nodes import Node
from .parser import Parser

__all__ = [
    "Environment",
    "Template",
    "LiquidError",
    "LiquidSyntaxError",
    "LiquidRenderError",
    "Context",
    "parse_expression",
    "tokenize",
    "to_s",
]


class Template:
    """A parsed template that can be rendered many times."""

    __slots__ = ("nodes", "source", "name")

    def __init__(self, nodes: list[Node], source: str, name: str = "<template>"):
        self.nodes = nodes
        self.source = source
        self.name = name

    def render(self, context: dict[str, Any], registers: dict[str, Any]) -> str:

        site = registers.get("site") or {}
        filters = dict(STANDARD_FILTERS)
        filters.update(make_url_filters(site if isinstance(site, dict) else {}))
        registers = dict(registers)
        registers.setdefault("site", site)
        ctx = Context(
            scopes=[dict(context)],
            registers=registers,
            filters=filters,
            environment=registers.get("environment"),
        )
        out: list[str] = []
        self.render_nodes(ctx, out)
        collected = registers.get("_warnings")
        if isinstance(collected, list) and ctx.warnings:
            collected.extend(f"{self.name}: {warning}" for warning in ctx.warnings)
        return "".join(out)

    def render_nodes(self, ctx: Context, out: list[str]) -> None:
        for node in self.nodes:
            node.render(ctx, out)


class Environment:
    """Holds the include loader, custom tags and the template cache."""

    def __init__(
        self,
        loader: Callable[[str], str] | None = None,
        custom_tags: dict[str, Callable] | None = None,
    ):
        self.loader = loader
        self.custom_tags: dict[str, Callable] = dict(custom_tags or {})
        self._cache: dict[tuple[str, int], Template] = {}

    def from_string(self, source: str, name: str | None = None, cache: bool = True) -> Template:
        if name is None or not cache:
            return Template(Parser(self, name or "<template>").parse(source), source, name or "<template>")
        key = (name, hash(source))
        template = self._cache.get(key)
        if template is None:
            template = Template(Parser(self, name).parse(source), source, name)
            self._cache[key] = template
        return template

    def render(
        self,
        source: str,
        context: dict[str, Any],
        registers: dict[str, Any],
        name: str | None = None,
        cache: bool = True,
    ) -> str:
        template = self.from_string(source, name, cache=cache)
        registers = dict(registers)
        registers["environment"] = self
        return template.render(context, registers)