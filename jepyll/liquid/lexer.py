"""Tokenizer for Liquid templates.

Turns a template into a flat list of :class:`Token` objects (text, ``{{ output }}``,
``{% tag %}`` and ``{# comment #}``) and applies Liquid's whitespace control
(``{{- ... -}}`` / ``{%- ... -%}``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TEXT = "text"
OUTPUT = "output"
TAG = "tag"
COMMENT = "comment"

_TOKEN_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)


@dataclass(slots=True)
class Token:
    kind: str
    value: str  # inner content (without the {{ }} / {% %} delimiters)
    line: int = 1
    lstrip: bool = False  # ``{%-`` / ``{{-``
    rstrip: bool = False  # ``-%}}`` / ``-}}``
    raw: str = ""  # original source, kept for {% raw %}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    line = 1
    for match in _TOKEN_RE.finditer(source):
        if match.start() > pos:
            chunk = source[pos:match.start()]
            tokens.append(Token(TEXT, chunk, line))
            line += chunk.count("\n")
        raw = match.group(0)
        inner = raw[2:-2]
        token_line = line
        line += raw.count("\n")

        if raw.startswith("{{"):
            kind = OUTPUT
        elif raw.startswith("{%"):
            kind = TAG
        else:
            kind = COMMENT

        lstrip = inner.startswith("-")
        if lstrip:
            inner = inner[1:]
        rstrip = inner.endswith("-")
        if rstrip:
            inner = inner[:-1]

        tokens.append(Token(kind, inner, token_line, lstrip, rstrip, raw))
        pos = match.end()

    if pos < len(source):
        tokens.append(Token(TEXT, source[pos:], line))

    _apply_whitespace_control(tokens)
    return tokens


def _apply_whitespace_control(tokens: list[Token]) -> None:
    for index, token in enumerate(tokens):
        if token.kind == TEXT:
            continue
        if token.lstrip and index > 0 and tokens[index - 1].kind == TEXT:
            tokens[index - 1].value = tokens[index - 1].value.rstrip()
        if token.rstrip and index + 1 < len(tokens) and tokens[index + 1].kind == TEXT:
            tokens[index + 1].value = tokens[index + 1].value.lstrip()