"""jepyll - a small, Jekyll-compatible static site generator written in Python.

The goal is *not* to clone every Jekyll feature, but to faithfully reproduce the
subset of Jekyll that a real blog needs:

* YAML front matter, pages, posts and collections
* ``_layouts`` (with layout inheritance), ``_includes``, ``_sass``, ``_data``
* a Liquid template engine (the subset of tags/filters Jekyll actually uses)
* Markdown (kramdown/GFM style) conversion with Pygments highlighting
* SCSS -> CSS compilation, static file copying, permalinks, RSS, sitemap
"""

__version__ = "0.1.0"

JEKYLL_COMPAT_VERSION = "4.4.1"
"""The Jekyll version whose behaviour we aim to reproduce."""