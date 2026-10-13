"""End-to-end test: build the fixture site and check the output."""

import shutil
from pathlib import Path

import pytest

from jepyll.config import Config
from jepyll.site import Site

FIXTURE = Path(__file__).parent / "fixtures" / "mini_site"


@pytest.fixture(scope="module")
def built_site(tmp_path_factory) -> tuple[Config, Path]:
    destination = tmp_path_factory.mktemp("out")
    source = Path(tmp_path_factory.mktemp("src"))
    shutil.copytree(FIXTURE, source, dirs_exist_ok=True)
    config = Config.load(str(source), overrides={"destination": str(destination)})
    site = Site(config)
    stats = site.build(jobs=1, verbose=False)
    assert stats["errors"] == []
    return config, Path(config.destination)


def read(built, *parts) -> str:
    return (built[1].joinpath(*parts)).read_text(encoding="utf-8")


def test_output_paths(built_site):
    destination = built_site[1]
    expected = [
        "index.html",
        "404.html",
        "robots.txt",
        "sitemap.xml",
        "ffmpeg-port-en.html",          # permalink /:title
        "ffmpeg-port-zh.html",
        "sample-note-en.html",
        "notes/inline-note-en.html",    # page living in notes/
        "notes/2025-07-07-collection-note-en/index.html",  # collection permalink
        "feeds/feed.xml",
        "assets/css/style.css",         # SCSS compiled
        "assets/js/app.js",             # static file
        "images/pic.svg",
    ]
    missing = [path for path in expected if not (destination / path).exists()]
    assert missing == []


def test_excluded_files_are_not_copied(built_site):
    assert not (built_site[1] / "scripts" / "README.txt").exists()


def test_future_posts_are_skipped(built_site):
    assert not (built_site[1] / "future-post-en.html").exists()
    assert "future-post-en" not in read(built_site, "sitemap.xml")


def test_post_layout_chain_and_liquid(built_site):
    html = read(built_site, "ffmpeg-port-en.html")
    assert "<title>Porting FFmpeg to Android | Mini Site</title>" in html  # seo tag
    assert "<!DOCTYPE html>" in html  # default layout applied
    assert '<h1>Porting FFmpeg to Android (lang: en)</h1>' in html
    assert '<p class="lang">English</p>' in html
    assert "<time>2014.03.10</time>" in html
    assert "class=\"site-footer\"" in html  # footer include


def test_markdown_features(built_site):
    html = read(built_site, "ffmpeg-port-en.html")
    assert "<h2>Background</h2>" in html
    assert 'class="highlight"' in html and "<span class=\"k\">def</span>" in html
    assert "$$w_1*a_1 + w_2*a_2 + b$$" in html  # math untouched by markdown
    assert "$x_1 + x_2$" in html
    assert '<img alt="" class="responsive" src="images/pic.svg" />' in html
    assert '<del>struck</del>' in html
    assert '<a href="https://example.com/bare-link">' in html  # GFM autolink
    assert 'class="footnote-ref"' in html
    assert "<table>" in html
    # {% raw %} keeps liquid out of the output
    assert "{% include not-an-include.html %}" in html


def test_home_page_lists_posts(built_site):
    html = read(built_site, "index.html")
    assert 'href="/ffmpeg-port-en"' in html
    assert 'href="/ffmpeg-port-zh"' not in html  # language filter
    assert "2014.03.10" in html


def test_rss_feed(built_site):
    xml = read(built_site, "feeds/feed.xml")
    assert "<rss version=\"2.0\">" in xml
    assert xml.count("<item>") == 2  # limit:2
    assert "&lt;p&gt;" in xml  # post content xml-escaped
    assert "<pubDate>Sat" in xml or "<pubDate>" in xml


def test_sitemap(built_site):
    xml = read(built_site, "sitemap.xml")
    assert "https://example.com/ffmpeg-port-en" in xml
    assert "https://example.com/404.html" in xml


def test_robots_uses_site_url(built_site):
    assert "Sitemap: https://example.com/sitemap.xml" in read(built_site, "robots.txt")


def test_sass_output(built_site):
    css = read(built_site, "assets/css/style.css")
    assert "color: #333" in css
    assert "background-color: #157878" in css
    assert (built_site[1] / "assets/css/style.css.map").exists()


def test_notes_count_include(built_site):
    html = read(built_site, "notes-en.html")
    assert "1 notes" in html  # the `_notes` collection has exactly one entry


def test_drafts_flag(tmp_path_factory):
    destination = tmp_path_factory.mktemp("drafts-out")
    source = Path(__file__).parent / "fixtures" / "mini_site"
    config = Config.load(str(source), overrides={
        "destination": str(destination), "show_drafts": True,
    })
    site = Site(config)
    stats = site.build(jobs=1, verbose=False)
    assert stats["errors"] == []
    assert (Path(config.destination) / "draft-post.html").exists()


def test_future_flag(tmp_path_factory):
    destination = tmp_path_factory.mktemp("future-out")
    source = Path(__file__).parent / "fixtures" / "mini_site"
    config = Config.load(str(source), overrides={"destination": str(destination), "future": True})
    site = Site(config)
    site.build(jobs=1, verbose=False)
    assert (Path(config.destination) / "future-post-en.html").exists()


def test_parallel_build_matches_serial(built_site, tmp_path_factory):
    import filecmp

    destination = tmp_path_factory.mktemp("parallel-out")
    source = built_site[0].source
    config = Config.load(source, overrides={"destination": str(destination)})
    site = Site(config)
    stats = site.build(jobs=2, verbose=False)
    assert stats["errors"] == []
    serial = built_site[1]
    parallel = Path(config.destination)
    for name in ("index.html", "ffmpeg-port-en.html", "sitemap.xml"):
        assert filecmp.cmp(serial / name, parallel / name, shallow=False), name