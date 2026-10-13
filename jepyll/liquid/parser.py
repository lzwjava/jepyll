"""Token stream -> AST for Liquid templates."""

from __future__ import annotations

import re
from typing import Any

from . import expr as expr_mod
from .errors import LiquidSyntaxError
from .expr import Expr, ExprParser, Unary, lex_expression, parse_expression
from .lexer import COMMENT, OUTPUT, TAG, TEXT, Token, tokenize
from .nodes import (
    Assign,
    Capture,
    Case,
    Comment,
    For,
    If,
    Include,
    Node,
    Output,
    Raw,
    Text,
)

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_ASSIGN_RE = re.compile(rf"({_IDENT})\s*=\s*(.*)\Z", re.DOTALL)
_FOR_RE = re.compile(rf"({_IDENT})\s+in\s+(.*)\z", re.DOTALL)
_TAG_RE = re.compile(rf"({_IDENT})(?:\s+(.*))?\z", re.DOTALL)
_PARAM_RE = re.compile(r"(limit|offset)\s*:\s*(\S+)")
_INCLUDE_FILE_RE = re.compile(r"""(?:"([^"]+)"|'([^']+)'|(\S+))(?:\s+(.*))?\z""", re.DOTALL)
_VALID_INCLUDE_PARAM = re.compile(
    r"""([\w-]+)\s*=\s*(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)'|([\w.-]+))"""
)
_CONDITIONAL_ENDS = {"elsif", "else", "endif"}


class Parser:
    def __init__(self, environment: Any, source_name: str = "<template>"):
        self.environment = environment
        self.source_name = source_name

    # ------------------------------------------------------------------ api
    def parse(self, source: str) -> list[Node]:
        tokens = tokenize(source)
        nodes, index = self._parse_nodes(tokens, 0, set())
        if index < len(tokens):
            token = tokens[index]
            raise self._error(token, f"unexpected {{% {token.value.strip()} %}}")
        return nodes

    # ------------------------------------------------------------- internals
    def _parse_nodes(
        self, tokens: list[Token], index: int, ends: set[str]
    ) -> tuple[list[Node], int]:
        nodes: list[Node] = []
        while index < len(tokens):
            token = tokens[index]
            if token.kind == TEXT:
                if token.value:
                    nodes.append(Text(token.value, token.line))
                index += 1
                continue
            if token.kind == COMMENT:
                index += 1
                continue
            if token.kind == OUTPUT:
                inner = token.value.strip()
                if not inner:
                    # Ruby Liquid renders `{{ }}` / `{{}}` as an empty string.
                    nodes.append(Output(expr_mod.Literal(None), token.line))
                    index += 1
                    continue
                nodes.append(Output(self._lenient_expr(inner, token), token.line))
                index += 1
                continue

            name, rest = self._split_tag(token)
            if name in ends:
                return nodes, index
            if name == "raw":
                body, index = self._consume_until(tokens, index + 1, "endraw")
                nodes.append(Raw(body, token.line))
                continue
            if name == "comment":
                _body, index = self._consume_until(
                    tokens, index + 1, "endcomment", nested="comment"
                )
                nodes.append(Comment(token.line))
                continue

            node, index = self._parse_tag(tokens, index, name, rest)
            if node is not None:
                nodes.append(node)

        if ends:
            raise LiquidSyntaxError(
                f"{self.source_name}: unexpected end of template, "
                f"expected one of {sorted(ends)}"
            )
        return nodes, index

    def _parse_tag(
        self, tokens: list[Token], index: int, name: str, rest: str
    ) -> tuple[Node | None, int]:
        token = tokens[index]
        line = token.line

        if name in ("endif", "endunless", "endcase", "endfor", "endcapture",
                    "endhighlight", "endraw", "endcomment", "endliquid", "when",
                    "elsif", "else"):
            raise self._error(token, f"unexpected {{% {name} %}}")

        if name in ("if", "unless"):
            condition = self._expr(rest, token)
            if name == "unless":
                condition = Unary("not", condition)
            ends = {"elsif", "else", "endunless"} if name == "unless" else _CONDITIONAL_ENDS
            end_tag = "endunless" if name == "unless" else "endif"
            body, index = self._parse_nodes(tokens, index + 1, ends)
            branches: list[tuple[Expr, list[Node]]] = [(condition, body)]
            branches, else_body, index = self._parse_conditional_tail(
                tokens, index, branches, end_tag=end_tag
            )
            return If(branches, else_body, line), index

        if name == "case":
            return self._parse_case(tokens, index, rest, line)

        if name == "for":
            return self._parse_for(tokens, index, rest)

        if name == "assign":
            match = _ASSIGN_RE.match(rest.strip())
            if not match:
                raise self._error(token, "{% assign %} expects `variable = expression`")
            return Assign(match.group(1), self._expr(match.group(2), token), line), index + 1

        if name == "capture":
            body, index = self._parse_nodes(tokens, index + 1, {"endcapture"})
            if index >= len(tokens):
                raise LiquidSyntaxError(f"{self.source_name}: missing {{% endcapture %}}")
            return Capture(rest.strip(), body, line), index + 1

        if name == "include":
            return self._parse_include(rest, token), index + 1

        if name in ("increment", "decrement", "layout", "form", "highlight",
                    "post_url", "page_url", "link", "include_relative", "toc",
                    "listing", "table", "excerpt", "absolute_url", "relative_url"):
            # Registered custom tags may still provide these.
            handler = (self.environment.custom_tags or {}).get(name)
            if handler is None:
                raise self._error(
                    token,
                    f"{{% {name} %}} is not supported by jepyll "
                    f"(only the tags Jekyll blogs actually use are implemented)",
                )

        handler = (self.environment.custom_tags or {}).get(name)
        if handler is not None:
            return handler(self, tokens, index, name, rest)

        raise self._error(token, f"Unknown tag '{name}'")

    # -- if / unless --------------------------------------------------------
    def _parse_conditional_tail(
        self,
        tokens: list[Token],
        index: int,
        branches: list[tuple[Expr, list[Node]]],
        end_tag: str = "endif",
    ) -> tuple[list[tuple[Expr, list[Node]]], list[Node], int]:
        else_body: list[Node] = []
        allowed = _CONDITIONAL_ENDS | {end_tag}
        while index < len(tokens):
            token = tokens[index]
            if token.kind != TAG:
                raise self._error(token, "expected a tag inside {% if %}")
            name, rest = self._split_tag(token)
            if name == "elsif":
                condition = self._expr(rest, token)
                body, index = self._parse_nodes(tokens, index + 1, allowed)
                branches.append((condition, body))
                continue
            if name == "else":
                else_body, index = self._parse_nodes(tokens, index + 1, {end_tag})
                continue
            if name == end_tag:
                return branches, else_body, index + 1
            raise self._error(token, f"unexpected {{% {name} %}} inside conditional")
        raise LiquidSyntaxError(f"{self.source_name}: missing {{% {end_tag} %}}")

    # -- case ---------------------------------------------------------------
    def _parse_case(
        self, tokens: list[Token], index: int, rest: str, line: int
    ) -> tuple[Case, int]:
        token = tokens[index]
        subject = self._expr(rest, token)
        whens: list[tuple[list[Expr], list[Node]]] = []
        else_body: list[Node] = []
        index += 1
        while index < len(tokens):
            inner = tokens[index]
            if inner.kind in (TEXT, COMMENT):
                index += 1
                continue
            if inner.kind != TAG:
                raise self._error(inner, "expected {% when %} inside {% case %}")
            inner_name, inner_rest = self._split_tag(inner)
            if inner_name == "when":
                choices = [self._expr(part, inner) for part in _split_choices(inner_rest)]
                body, index = self._parse_nodes(
                    tokens, index + 1, {"when", "else", "endcase"}
                )
                whens.append((choices, body))
                continue
            if inner_name == "else":
                else_body, index = self._parse_nodes(tokens, index + 1, {"endcase"})
                continue
            if inner_name == "endcase":
                return Case(subject, whens, else_body, line), index + 1
            raise self._error(inner, f"unexpected {{% {inner_name} %}} inside {{% case %}}")
        raise LiquidSyntaxError(f"{self.source_name}: missing {{% endcase %}}")

    # -- for ----------------------------------------------------------------
    def _parse_for(self, tokens: list[Token], index: int, rest: str) -> tuple[For, int]:
        token = tokens[index]
        match = _FOR_RE.match(rest.strip())
        if not match:
            raise self._error(token, "{% for %} expects `item in collection`")
        var, collection_text = match.group(1), match.group(2)
        lexed = lex_expression(collection_text)
        parser = ExprParser(lexed, collection_text)
        collection = parser.parse(stop_at_params=True)

        limit_expr = offset_expr = None
        reversed_flag = False
        if parser.pos < len(lexed):
            leftover = collection_text[lexed[parser.pos].pos:]
            for param_match in _PARAM_RE.finditer(leftover):
                kind = param_match.group(1)
                raw = param_match.group(2).rstrip(",;")
                expr = self._expr(raw, token) if not _is_number(raw) else (
                    expr_mod.Literal(int(raw) if raw.lstrip("-").isdigit() else float(raw))
                )
                if kind == "limit":
                    limit_expr = expr
                else:
                    offset_expr = expr
            if re.search(r"\breversed\b", leftover):
                reversed_flag = True

        body, index = self._parse_nodes(tokens, index + 1, {"else", "endfor"})
        else_body: list[Node] = []
        if index < len(tokens) and self._split_tag(tokens[index])[0] == "else":
            else_body, index = self._parse_nodes(tokens, index + 1, {"endfor"})
        if index >= len(tokens):
            raise LiquidSyntaxError(f"{self.source_name}: missing {{% endfor %}}")
        return (
            For(var, collection, body, else_body, limit_expr, offset_expr,
                reversed_flag, token.line),
            index + 1,
        )

    # -- include ------------------------------------------------------------
    def _parse_include(self, rest: str, token: Token) -> Include:
        match = _INCLUDE_FILE_RE.match(rest.strip())
        if not match:
            raise self._error(token, "{% include %} expects a file name")
        filename = match.group(1) or match.group(2) or match.group(3)
        params_text = (match.group(4) or "").strip()
        params: dict[str, Any] = {}
        consumed = 0
        if params_text:
            for param in _VALID_INCLUDE_PARAM.finditer(params_text):
                consumed = param.end()
                key, dq, sq, raw = param.group(1), param.group(2), param.group(3), param.group(4)
                if dq is not None:
                    params[key] = dq
                elif sq is not None:
                    params[key] = sq
                elif raw is not None:
                    params[key] = raw
            remainder = params_text[consumed:].strip().lstrip(",")
            if remainder:
                raise self._error(token, f"could not parse include parameters: {rest!r}")
        return Include(filename, params, token.line)

    # -- helpers ------------------------------------------------------------
    def _expr(self, text: str, token: Token) -> Expr:
        text = text.strip()
        if not text:
            raise self._error(token, "missing expression")
        try:
            return parse_expression(text)
        except LiquidSyntaxError as exc:
            raise self._error(token, str(exc)) from exc

    @staticmethod
    def _tag_name(token: Token) -> str:
        """First word of a tag, never raising (used while scanning raw blocks)."""
        stripped = token.value.strip()
        if not stripped:
            return ""
        return stripped.split(None, 1)[0]

    @staticmethod
    def _lenient_expr(text: str, token: Token) -> Expr:
        """Ruby Liquid is lenient: anything that isn't an expression becomes a
        variable lookup (``{{ ... }}``, ``{{ < math > }}``, ``{{ 变量名 }}`` ...)
        which simply resolves to nil and renders as an empty string."""
        try:
            return parse_expression(text)
        except LiquidSyntaxError:
            lookup = text.strip().split(None, 1)[0] if text.strip() else ""
            return expr_mod.Lookup(lookup, [])

    @staticmethod
    def _split_tag(token: Token) -> tuple[str, str]:
        match = _TAG_RE.match(token.value.strip())
        if not match:
            raise LiquidSyntaxError(f"malformed tag: {{% {token.value} %}}")
        return match.group(1), (match.group(2) or "").strip()

    def _consume_until(
        self, tokens: list[Token], index: int, end_name: str, nested: str | None = None
    ) -> tuple[str, int]:
        parts: list[str] = []
        depth = 1
        while index < len(tokens):
            token = tokens[index]
            if token.kind == TAG:
                name = self._tag_name(token)
                if nested and name == nested:
                    depth += 1
                elif name == end_name:
                    depth -= 1
                    if depth == 0:
                        return "".join(parts), index + 1
            if token.kind == TEXT:
                parts.append(token.value)
            else:
                parts.append(token.raw)
            index += 1
        raise LiquidSyntaxError(f"{self.source_name}: missing {{% {end_name} %}}")

    def _error(self, token: Token, message: str) -> LiquidSyntaxError:
        return LiquidSyntaxError(f"{self.source_name}:{token.line}: {message}")


def _split_choices(text: str) -> list[str]:
    """`{% when "a", "b" %}` -> ['"a"', '"b"'] (commas outside of strings)."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False