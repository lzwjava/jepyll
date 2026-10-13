"""Exceptions raised by the Liquid implementation."""

from __future__ import annotations


class LiquidError(Exception):
    """Base class for all Liquid errors."""


class LiquidSyntaxError(LiquidError):
    """Raised while parsing a template."""


class LiquidRenderError(LiquidError):
    """Raised while rendering a template."""