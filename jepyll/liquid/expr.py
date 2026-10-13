"""Liquid expression parsing and evaluation.

Implements the expression grammar used inside ``{{ ... }}``, ``{% if %}``,
``{% assign %}`` ... : lookups (``site.posts``, ``page.path``,
``site[type]``, ``date_parts[2]``), literals, ranges, the comparison
operators (``== != > < >= <= contains``), ``and``/``or``/``not`` and
filter chains (``| date: "%Y"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .errors import LiquidSyntaxError


# --------------------------------------------------------------------------
# expression lexer
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ExprToken:
    kind: str  # ident, number, string, op, punct, eof
    value: Any
    pos: int


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_?!]*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_TWO_CHAR_OPS = ("==", "!=", "<=", ">=")
_ONE_CHAR_OPS = set("()[],:|.<>")
_PUNCT_CHARS = _ONE_CHAR_OPS | {"-"}


def lex_expression(text: str) -> list[ExprToken]:
    tokens: list[ExprToken] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "'\"":
            end = index + 1
            buf: list[str] = []
            while end < length:
                cur = text[end]
                if cur == "\\" and end + 1 < length:
                    buf.append(text[end + 1])
                    end += 2
                    continue
                if cur == char:
                    break
                buf.append(cur)
                end += 1
            if end >= length or text[end] != char:
                raise LiquidSyntaxError(f"unterminated string in expression: {text!r}")
            tokens.append(ExprToken("string", "".join(buf), index))
            index = end + 1
            continue
        if text.startswith("..", index):
            tokens.append(ExprToken("op", "..", index))
            index += 2
            continue
        two = text[index:index + 2]
        if two in _TWO_CHAR_OPS:
            tokens.append(ExprToken("op", two, index))
            index += 2
            continue
        number = _NUMBER_RE.match(text, index)
        if number and not (char == "." and text[index:index + 2] == ".."):
            raw = number.group(0)
            value: Any = float(raw) if "." in raw else int(raw)
            tokens.append(ExprToken("number", value, index))
            index = number.end()
            continue
        ident = _IDENT_RE.match(text, index)
        if ident:
            tokens.append(ExprToken("ident", ident.group(0), index))
            index = ident.end()
            continue
        if char in _PUNCT_CHARS:
            kind = "op" if char in ".<-" else "punct"
            tokens.append(ExprToken(kind, char, index))
            index += 1
            continue
        if char in ">*&":
            tokens.append(ExprToken("op", char, index))
            index += 1
            continue
        raise LiquidSyntaxError(f"unexpected character {char!r} in expression: {text!r}")
    tokens.append(ExprToken("eof", None, length))
    return tokens


# --------------------------------------------------------------------------
# expression AST
# --------------------------------------------------------------------------
class Expr:
    __slots__ = ()

    def evaluate(self, ctx: "Context") -> Any:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(slots=True)
class Literal(Expr):
    value: Any

    def evaluate(self, ctx: "Context") -> Any:
        return self.value


@dataclass(slots=True)
class Lookup(Expr):
    name: str
    parts: list[tuple[str, Any]]  # ("attr", name) | ("index", Expr)

    def evaluate(self, ctx: "Context") -> Any:
        value = ctx.resolve(self.name)
        for kind, arg in self.parts:
            if kind == "attr":
                value = ctx.get_member(value, arg)
            else:
                index = arg.evaluate(ctx)
                value = ctx.index(value, index)
        return value


@dataclass(slots=True)
class Range(Expr):
    start: Expr
    end: Expr

    def evaluate(self, ctx: "Context") -> list[Any]:
        return list(range(int(self.start.evaluate(ctx)), int(self.end.evaluate(ctx)) + 1))


@dataclass(slots=True)
class Unary(Expr):
    op: str
    operand: Expr

    def evaluate(self, ctx: "Context") -> Any:
        value = self.operand.evaluate(ctx)
        if self.op == "not":
            return not truthy(value)
        if self.op == "-":
            return -_to_number(value)
        raise LiquidSyntaxError(f"unknown unary operator {self.op!r}")


@dataclass(slots=True)
class Binary(Expr):
    op: str
    left: Expr
    right: Expr

    def evaluate(self, ctx: "Context") -> Any:
        if self.op == "and":
            return truthy(self.left.evaluate(ctx)) and truthy(self.right.evaluate(ctx))
        if self.op == "or":
            return truthy(self.left.evaluate(ctx)) or truthy(self.right.evaluate(ctx))
        left = self.left.evaluate(ctx)
        right = self.right.evaluate(ctx)
        op = self.op
        if op == "==":
            return loose_equal(left, right)
        if op == "!=":
            return not loose_equal(left, right)
        if op == "contains":
            if left is None:
                return False
            if isinstance(left, dict):
                key = right if isinstance(right, str) else str(right)
                return key in left
            try:
                return right in left
            except TypeError:
                return False
        if op in ("<", ">", "<=", ">="):
            if left is None or right is None:
                return False
            try:
                if isinstance(left, str) or isinstance(right, str):
                    left, right = str(left), str(right)
                else:
                    left, right = _to_number(left), _to_number(right)
            except (TypeError, ValueError):
                return False
            return {"<": left < right, ">": left > right,
                    "<=": left <= right, ">=": left >= right}[op]
        raise LiquidSyntaxError(f"unknown operator {op!r}")


@dataclass(slots=True)
class FilterChain(Expr):
    base: Expr
    filters: list[tuple[str, list[Expr]]]

    def evaluate(self, ctx: "Context") -> Any:
        value = self.base.evaluate(ctx)
        for name, args in self.filters:
            argv = [arg.evaluate(ctx) for arg in args]
            value = ctx.apply_filter(name, value, argv)
        return value


# --------------------------------------------------------------------------
# expression parser
# --------------------------------------------------------------------------
class ExprParser:
    def __init__(self, tokens: list[ExprToken], source: str = ""):
        self.tokens = tokens
        self.pos = 0
        self.source = source

    # -- helpers ----------------------------------------------------------
    @property
    def current(self) -> ExprToken:
        return self.tokens[self.pos]

    def advance(self) -> ExprToken:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def peek_is(self, *values: Any) -> bool:
        token = self.current
        return token.kind in ("ident", "op", "punct") and token.value in values

    def error(self, message: str) -> LiquidSyntaxError:
        return LiquidSyntaxError(f"{message} (in `{self.source}`)")

    def expect_punct(self, value: str) -> None:
        token = self.advance()
        if token.kind != "punct" or token.value != value:
            raise self.error(f"expected {value!r}, got {token.value!r}")

    # -- grammar ----------------------------------------------------------
    def parse(self, stop_at_params: bool = False) -> Expr:
        expr = self.parse_or(stop_at_params)
        filters: list[tuple[str, list[Expr]]] = []
        while self.peek_is("|"):
            self.advance()
            name_token = self.advance()
            if name_token.kind != "ident":
                raise self.error(f"expected filter name, got {name_token.value!r}")
            args: list[Expr] = []
            if self.peek_is(":"):
                self.advance()
                args = self._parse_args(stop_at_params)
            elif self.current.kind not in ("eof",) and not (
                stop_at_params and self._at_param()
            ):
                # Liquid also allows `filter arg1 arg2` (space separated)
                if not self.peek_is("|"):
                    args = self._parse_args(stop_at_params)
            filters.append((str(name_token.value), args))
        return FilterChain(expr, filters) if filters else expr

    def _at_param(self) -> bool:
        """`limit:`/`offset:`/`reversed` style tag parameters."""
        token = self.current
        if token.kind != "ident":
            return False
        nxt = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
        if token.value == "reversed":
            return True
        return bool(nxt and nxt.kind == "punct" and nxt.value == ":")

    def _parse_args(self, stop_at_params: bool) -> list[Expr]:
        args: list[Expr] = []
        while True:
            if stop_at_params and self._at_param():
                break
            if self.current.kind == "eof" or self.peek_is("|"):
                break
            args.append(self.parse_or(False))
            if self.peek_is(","):
                self.advance()
                continue
            if self.current.kind == "eof" or self.peek_is("|"):
                break
            if stop_at_params and self._at_param():
                break
            # space separated args
            if self.current.kind in ("ident", "number", "string", "punct"):
                continue
            break
        return args

    def parse_or(self, stop_at_params: bool) -> Expr:
        node = self.parse_and(stop_at_params)
        while self.peek_is("or"):
            self.advance()
            node = Binary("or", node, self.parse_and(stop_at_params))
        return node

    def parse_and(self, stop_at_params: bool) -> Expr:
        node = self.parse_not(stop_at_params)
        while self.peek_is("and"):
            self.advance()
            node = Binary("and", node, self.parse_not(stop_at_params))
        return node

    def parse_not(self, stop_at_params: bool) -> Expr:
        if self.peek_is("not"):
            self.advance()
            return Unary("not", self.parse_not(stop_at_params))
        return self.parse_comparison(stop_at_params)

    def parse_comparison(self, stop_at_params: bool) -> Expr:
        node = self.parse_range(stop_at_params)
        while True:
            if self.peek_is("==", "!=", "<=", ">=", "<", ">", "contains"):
                op = str(self.advance().value)
                node = Binary(op, node, self.parse_range(stop_at_params))
                continue
            break
        return node

    def parse_range(self, stop_at_params: bool) -> Expr:
        node = self.parse_primary(stop_at_params)
        if self.peek_is(".."):
            self.advance()
            end = self.parse_primary(stop_at_params)
            return Range(node, end)
        return node

    def parse_primary(self, stop_at_params: bool) -> Expr:
        token = self.current
        if token.kind == "string":
            self.advance()
            return Literal(token.value)
        if token.kind == "number":
            self.advance()
            return Literal(token.value)
        if token.kind == "punct" and token.value == "(":
            self.advance()
            node = self.parse_or(False)
            self.expect_punct(")")
            return node
        if token.kind == "punct" and token.value == "[":
            # rare: literal array `[1, 2]`
            self.advance()
            items: list[Any] = []
            while not (self.current.kind == "punct" and self.current.value == "]"):
                items.append(self.parse_or(False).evaluate(_EMPTY_CTX))
                if self.peek_is(","):
                    self.advance()
            self.expect_punct("]")
            return Literal(items)
        if token.kind == "ident":
            self.advance()
            name = str(token.value)
            lowered = name.lower()
            if lowered in ("nil", "null", "none"):
                return Literal(None)
            if lowered == "true":
                return Literal(True)
            if lowered == "false":
                return Literal(False)
            if lowered in ("empty", "blank"):
                return Literal(_Empty() if lowered == "empty" else _Blank())
            parts: list[tuple[str, Any]] = []
            parts.extend(self._parse_tail(stop_at_params))
            return Lookup(name, parts)
        if token.kind == "op" and token.value == "-":
            self.advance()
            operand = self.parse_primary(stop_at_params)
            return Unary("-", operand)
        raise self.error(f"unexpected token {token.value!r}")

    def _parse_tail(self, stop_at_params: bool) -> list[tuple[str, Any]]:
        parts: list[tuple[str, Any]] = []
        while True:
            token = self.current
            if token.kind == "op" and token.value == ".":
                nxt = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
                if nxt is None or nxt.kind not in ("ident", "number"):
                    break
                self.advance()
                name = self.advance()
                parts.append(("attr", str(name.value)))
                continue
            if token.kind == "punct" and token.value == "[":
                self.advance()
                if self.current.kind == "number":
                    index_expr: Expr = Literal(int(self.advance().value))
                elif self.current.kind == "string":
                    index_expr = Literal(str(self.advance().value))
                else:
                    index_expr = self.parse_or(False)
                self.expect_punct("]")
                parts.append(("index", index_expr))
                continue
            break
        return parts


class _Empty:
    def __repr__(self) -> str:  # pragma: no cover
        return "empty"


class _Blank:
    def __repr__(self) -> str:  # pragma: no cover
        return "blank"


def parse_expression(text: str, stop_at_params: bool = False) -> Expr:
    tokens = lex_expression(text)
    if not tokens or tokens[0].kind == "eof":
        raise LiquidSyntaxError("empty expression")
    parser = ExprParser(tokens, text)
    expr = parser.parse(stop_at_params)
    return expr


# --------------------------------------------------------------------------
# evaluation helpers
# --------------------------------------------------------------------------
def truthy(value: Any) -> bool:
    """Ruby truthiness: only ``nil`` and ``false`` are falsy."""
    if value is None or value is False:
        return False
    if isinstance(value, (_Empty, _Blank)):
        return not is_empty(value)
    return True


def is_empty(value: Any) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def loose_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (_Empty, _Blank)) or isinstance(right, (_Empty, _Blank)):
        return is_empty(left) if isinstance(left, (_Empty, _Blank)) else is_empty(right)
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right if (isinstance(left, bool) and isinstance(right, bool)) else False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    try:
        return left == right
    except Exception:  # pragma: no cover - defensive
        return False


def _to_number(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return 0
    return 0


# --------------------------------------------------------------------------
# rendering context
# --------------------------------------------------------------------------
class Context:
    """Scope stack + filter registry used while rendering a template."""

    def __init__(
        self,
        scopes: list[dict[str, Any]] | None = None,
        registers: dict[str, Any] | None = None,
        filters: "dict[str, Callable]" | None = None,
        environment: Any = None,
    ):
        self.scopes: list[dict[str, Any]] = scopes if scopes is not None else [{}]
        self.registers = registers or {}
        self.filters = filters or {}
        self.environment = environment
        self.warnings: list[str] = []

    # -- scopes -----------------------------------------------------------
    def push(self, scope: dict[str, Any] | None = None) -> None:
        self.scopes.append(scope if scope is not None else {})

    def pop(self) -> dict[str, Any]:
        return self.scopes.pop()

    def set(self, name: str, value: Any) -> None:
        self.scopes[-1][name] = value

    def resolve(self, name: str) -> Any:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # -- value access -----------------------------------------------------
    @staticmethod
    def get_member(value: Any, name: str) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            if name in value:
                return value[name]
            return None
        if isinstance(value, (list, tuple, str)):
            if name in ("size", "first", "last", "length"):
                if name in ("size", "length"):
                    return len(value)
                if name == "first":
                    return value[0] if value else None
                return value[-1] if value else None
            return None
        if name.endswith("?") and hasattr(value, name[:-1]):
            attr = getattr(value, name[:-1])
            return attr() if callable(attr) else attr
        if hasattr(value, name):
            attr = getattr(value, name)
            return attr() if callable(attr) else attr
        if isinstance(value, object) and hasattr(value, "__dict__"):
            return value.__dict__.get(name)
        return None

    @staticmethod
    def index(value: Any, key: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return value.get(key) if not isinstance(key, int) else None
        if isinstance(value, (list, tuple, str)):
            if isinstance(key, str):
                key = int(key) if key.lstrip("-").isdigit() else None
            if key is None:
                return None
            try:
                return value[key]
            except IndexError:
                return None
        return None

    # -- filters ----------------------------------------------------------
    def apply_filter(self, name: str, value: Any, args: list[Any]) -> Any:
        func = self.filters.get(name)
        if func is None:
            # Ruby Liquid returns the input unchanged unless strict_filters is on.
            self.warnings.append(f"unknown filter: {name}")
            return value
        try:
            return func(value, *args)
        except LiquidSyntaxError:
            raise
        except Exception as exc:  # pragma: no cover - filter bugs shouldn't kill a build
            self.warnings.append(f"filter `{name}` failed: {exc}")
            return value


def evaluate(expr: Expr, ctx: Context) -> Any:
    return expr.evaluate(ctx)


# A throwaway context used while evaluating literal array syntax.
_EMPTY_CTX = Context(filters={})