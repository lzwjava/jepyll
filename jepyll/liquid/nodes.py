"""AST nodes for Liquid templates (parse once, render many times)."""

from __future__ import annotations

from typing import Any, Callable

from .errors import LiquidRenderError
from .expr import Expr, Context, truthy


class Node:
    __slots__ = ("line",)

    def __init__(self, line: int = 1):
        self.line = line

    def render(self, ctx: Context, out: list[str]) -> None:  # pragma: no cover
        raise NotImplementedError

    def references_site(self) -> bool:  # cheap hint for caching decisions
        return False


def render_all(nodes: list[Node], ctx: Context, out: list[str]) -> None:
    for node in nodes:
        node.render(ctx, out)


class Text(Node):
    __slots__ = ("value",)

    def __init__(self, value: str, line: int = 1):
        super().__init__(line)
        self.value = value

    def render(self, ctx: Context, out: list[str]) -> None:
        out.append(self.value)


class Output(Node):
    __slots__ = ("expr",)

    def __init__(self, expr: Expr, line: int = 1):
        super().__init__(line)
        self.expr = expr

    def render(self, ctx: Context, out: list[str]) -> None:
        value = self.expr.evaluate(ctx)
        out.append(stringify(value))


class Assign(Node):
    __slots__ = ("name", "expr")

    def __init__(self, name: str, expr: Expr, line: int = 1):
        super().__init__(line)
        self.name = name
        self.expr = expr

    def render(self, ctx: Context, out: list[str]) -> None:
        ctx.set(self.name, self.expr.evaluate(ctx))


class Capture(Node):
    __slots__ = ("name", "body")

    def __init__(self, name: str, body: list[Node], line: int = 1):
        super().__init__(line)
        self.name = name
        self.body = body

    def render(self, ctx: Context, out: list[str]) -> None:
        buffer: list[str] = []
        render_all(self.body, ctx, buffer)
        ctx.set(self.name, "".join(buffer))


class If(Node):
    __slots__ = ("branches", "else_body")

    def __init__(
        self,
        branches: list[tuple[Expr, list[Node]]],
        else_body: list[Node] | None = None,
        line: int = 1,
    ):
        super().__init__(line)
        self.branches = branches
        self.else_body = else_body or []

    def render(self, ctx: Context, out: list[str]) -> None:
        for condition, body in self.branches:
            if truthy(condition.evaluate(ctx)):
                render_all(body, ctx, out)
                return
        render_all(self.else_body, ctx, out)


class Case(Node):
    __slots__ = ("subject", "whens", "else_body")

    def __init__(
        self,
        subject: Expr,
        whens: list[tuple[list[Expr], list[Node]]],
        else_body: list[Node] | None = None,
        line: int = 1,
    ):
        super().__init__(line)
        self.subject = subject
        self.whens = whens
        self.else_body = else_body or []

    def render(self, ctx: Context, out: list[str]) -> None:
        value = self.subject.evaluate(ctx)
        from .expr import loose_equal

        for choices, body in self.whens:
            if any(loose_equal(value, choice.evaluate(ctx)) for choice in choices):
                render_all(body, ctx, out)
                return
        render_all(self.else_body, ctx, out)


class For(Node):
    __slots__ = ("var", "collection", "body", "else_body", "limit", "offset", "reversed_")

    def __init__(
        self,
        var: str,
        collection: Expr,
        body: list[Node],
        else_body: list[Node] | None = None,
        limit: Expr | None = None,
        offset: Expr | None = None,
        reversed_: bool = False,
        line: int = 1,
    ):
        super().__init__(line)
        self.var = var
        self.collection = collection
        self.body = body
        self.else_body = else_body or []
        self.limit = limit
        self.offset = offset
        self.reversed_ = reversed_

    def render(self, ctx: Context, out: list[str]) -> None:
        items = to_iterable(self.collection.evaluate(ctx))
        offset = int(self.offset.evaluate(ctx)) if self.offset is not None else 0
        if offset:
            items = items[offset:]
        if self.reversed_:
            items = list(reversed(items))
        if self.limit is not None:
            items = items[: int(self.limit.evaluate(ctx))]

        if not items:
            render_all(self.else_body, ctx, out)
            return

        total = len(items)
        ctx.push()
        scope = ctx.scopes[-1]
        try:
            for position, item in enumerate(items):
                scope[self.var] = item
                scope["forloop"] = {
                    "index": position + 1,
                    "index0": position,
                    "rindex": total - position,
                    "rindex0": total - position - 1,
                    "first": position == 0,
                    "last": position == total - 1,
                    "length": total,
                }
                render_all(self.body, ctx, out)
        finally:
            ctx.pop()


class Raw(Node):
    __slots__ = ("value",)

    def __init__(self, value: str, line: int = 1):
        super().__init__(line)
        self.value = value

    def render(self, ctx: Context, out: list[str]) -> None:
        out.append(self.value)


class Include(Node):
    __slots__ = ("filename", "params")

    def __init__(self, filename: str, params: dict[str, Expr | Any], line: int = 1):
        super().__init__(line)
        self.filename = filename
        self.params = params

    def render(self, ctx: Context, out: list[str]) -> None:
        env = ctx.environment
        if env is None or env.loader is None:
            raise LiquidRenderError(f"no include loader configured ({self.filename})")
        try:
            source = env.loader(self.filename)
        except (FileNotFoundError, KeyError) as exc:
            raise LiquidRenderError(
                f"Could not locate the included file '{self.filename}', "
                f"ensure it exists in the _includes directory"
            ) from exc

        resolved: dict[str, Any] = {"include": {}}
        for key, value in self.params.items():
            resolved["include"][key] = value.evaluate(ctx) if isinstance(value, Expr) else value

        ctx.push(resolved)
        try:
            template = env.from_string(source, name=self.filename)
            template.render_nodes(ctx, out)
        finally:
            ctx.pop()


class Comment(Node):
    __slots__ = ()

    def render(self, ctx: Context, out: list[str]) -> None:
        return


class CustomNode(Node):
    """Node produced by a registered custom tag (``{% seo %}`` ...)."""

    __slots__ = ("handler", "payload")

    def __init__(self, handler: Callable[..., str], payload: Any, line: int = 1):
        super().__init__(line)
        self.handler = handler
        self.payload = payload

    def render(self, ctx: Context, out: list[str]) -> None:
        out.append(self.handler(ctx, self.payload))


def stringify(value: Any) -> str:
    from .filters import to_s

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple, set)):
        # Ruby's `Array#to_s` / `Hash#to_s` don't raise, they interpolate.
        if isinstance(value, dict):
            return "{" + ", ".join(f"{k}=>{to_s(v)}" for k, v in value.items()) + "}"
        if isinstance(value, set):
            value = list(value)
        return "[" + ", ".join(to_s(item) for item in value) + "]"
    return to_s(value)


def to_iterable(value: Any) -> list[Any]:
    if value is None or value is False:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, str):
        return [value]
    if hasattr(value, "__iter__"):
        return list(value)
    return []