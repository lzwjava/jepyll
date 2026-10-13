"""Tests for front matter, URLs/slugify and dates."""

import datetime as dt

from jepyll.dates import date_to_rfc822, date_to_xmlschema, strftime, tzinfo
from jepyll.frontmatter import split
from jepyll.urls import (
    add_permalink_suffix,
    build_url,
    converter_output_ext,
    destination_for,
    permalink_extension,
    slugify,
    titleize_slug,
)


def test_front_matter_basic():
    data, body = split("---\ntitle: X\nlayout: post\n---\ncontent")
    assert data == {"title": "X", "layout": "post"}
    assert body == "content"


def test_front_matter_empty_block():
    # Jekyll accepts `---\n---\n` (used by SCSS files)
    data, body = split("---\n---\n\nbody\n")
    assert data == {}
    assert body == "\nbody\n"


def test_front_matter_multiline_yaml():
    source = "---\ndescription: >-\n  one\n  two\ntitle: T\n---\nbody"
    data, body = split(source)
    assert data == {"description": "one two", "title": "T"}
    assert body == "body"


def test_front_matter_absent():
    data, body = split("just text")
    assert data is None
    assert body == "just text"


def test_slugify_modes():
    assert slugify("Hello World") == "hello-world"
    assert slugify("Hello World", cased=True) == "Hello-World"
    assert slugify("ffmpeg-en", mode="pretty", cased=True) == "ffmpeg-en"
    assert titleize_slug("my-post-title") == "My Post Title"


def test_post_url_like_jekyll():
    # permalink /:title + a dated filename -> /ffmpeg-en -> ffmpeg-en.html
    url = build_url(template="/:title", placeholders={
        "title": slugify("ffmpeg-en", mode="pretty", cased=True),
    })
    assert url == "/ffmpeg-en"
    assert destination_for(url, ".html") == "/ffmpeg-en.html"


def test_page_urls_like_jekyll():
    # root page: /:path/:basename with an empty path squeezes to /name
    assert build_url(template="/:path/:basename",
                     placeholders={"path": "", "basename": "index-en"}) == "/index-en"
    assert build_url(template="/:path/:basename",
                     placeholders={"path": "notes", "basename": "x"}) == "/notes/x"
    # index pages resolve to a directory URL
    assert build_url(template="/:path/", placeholders={"path": ""}) == "/"
    assert destination_for("/", ".html") == "/index.html"
    # trailing slash collection permalink -> index.html
    assert destination_for("/notes/x/", ".html") == "/notes/x/index.html"


def test_permalink_extension_and_output_ext():
    assert permalink_extension("/404.html") == ".html"
    assert permalink_extension("/notes/:path/") is None
    assert permalink_extension("/:title") is None
    md = {".md", "markdown", "mkd"}
    assert converter_output_ext(".md", md) == ".html"
    assert converter_output_ext(".xml", md) == ".xml"
    assert converter_output_ext(".scss", md) == ".css"


def test_add_permalink_suffix():
    assert add_permalink_suffix("/:path/:basename", "pretty") == "/:path/:basename/"
    assert add_permalink_suffix("/:path/:basename", "date") == "/:path/:basename:output_ext"
    assert add_permalink_suffix("/:path/:basename", "/:title") == "/:path/:basename"


def test_dates_and_timezone():
    zone = tzinfo("Asia/Shanghai")
    assert strftime(dt.datetime(2024, 3, 10), "%Y.%m.%d") == "2024.03.10"
    assert strftime(dt.datetime(2024, 3, 10), "%Y-%-m-%-d") == "2024-3-10"
    assert strftime(dt.datetime(2024, 3, 10), "%e") == "10"
    stamp = dt.datetime(2025, 1, 11, tzinfo=zone)
    assert date_to_rfc822(stamp) == "Sat, 11 Jan 2025 00:00:00 +0800"
    assert date_to_xmlschema(stamp) == "2025-01-11T00:00:00+08:00"