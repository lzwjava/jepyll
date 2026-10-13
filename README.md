# jepyll

**jepyll** is a small static site generator that speaks enough Jekyll to run a
real blog — written in Python instead of Ruby.

It exists because migrating a Jekyll site to another generator usually means
rewriting 400 posts and every layout. jepyll takes the opposite approach: it
reads the *same* source tree (`_config.yml`, `_posts`, `_layouts`,
`_includes`, `_sass`, front matter, permalinks) and produces the *same* URLs
and the *same* HTML, so an existing Jekyll site can keep its content and its
backlinks untouched.

It implements the Jekyll features a typical blog actually uses — and nothing
else.

```
jepyll build -s ~/projects/my-jekyll-blog        # build once
jepyll serve -s ~/projects/my-jekyll-blog        # build + watch + http server
jepyll new   "Hello World" --lang en             # scaffold a post
jepyll clean                                     # remove the generated site
```

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[sass]"      # libsass powers SCSS compilation
jepyll --version
```

Requirements: Python ≥ 3.10, `PyYAML`, `Markdown`, `Pygments`, `libsass`
(optional but recommended — otherwise a `sass`/`sassc` binary on `PATH` is
used).

## Commands

| Command | Description |
| --- | --- |
| `jepyll build` | Read the source, render everything, write `_site/` |
| `jepyll serve` | Build, serve `_site/` over HTTP, rebuild on changes |
| `jepyll new TITLE` | Create `_posts/YYYY-MM-DD-slug.md` |
| `jepyll clean` | Delete the generated output (keeps `keep_files`) |

Common flags:

```
-s, --source DIR        site source (default: .)
-d, --destination DIR   output dir (default: _site)
-c, --config FILE       extra/override config files (repeatable)
-j, --jobs N            render in N parallel processes (Linux/macOS)
    --drafts            include _drafts
    --future            include posts dated in the future
    --trace             print every warning
-q, --quiet             less output
```

`serve` flags: `--host`, `--port` (default 4000), `--no-watch`.
The dev server resolves extensionless URLs (`/my-post` → `my-post.html`)
exactly like `jekyll serve`.

## What is supported

**Content & structure**

- YAML front matter (including Jekyll's empty `---\n---\n` blocks)
- Pages anywhere in the tree (files with front matter), static files
  (everything else, including binaries), `exclude:` / `include:` filtering
  with Jekyll's special-directory rules (`_`, `.`, `#`, `~`)
- Posts in `_posts/**` with `YYYY-MM-DD-slug.md` filenames, date/slug/title
  extraction, `published: false`, future-post filtering
- Collections (`collections:` in `_config.yml`, `_label/` dirs) with their own
  `permalink` and `output` settings
- Drafts (`_drafts/`, `--drafts`)
- `_data/` (YAML, JSON, CSV) as `site.data`
- `_layouts` with layout inheritance and `{{ content }}`
- `_includes` with parameters (`{% include post-list.html lang='en' %}`)
- Permalinks: the built-in styles (`date`, `pretty`, ...) and custom
  templates (`/:title`, `/notes/:path/`), explicit `permalink:` front matter,
  Jekyll's URL sanitising / placeholder rules (`:year`, `:month`, `:title`,
  `:path`, `:collection`, ...)

**Liquid**

A from-scratch implementation of the subset real sites use:

- `{% assign %}`, `{% capture %}`, `{% if/elsif/else/unless %}`,
  `{% case/when %}`, `{% for %}` (with `limit`, `offset`, `reversed`, `else`,
  `forloop.*`), `{% include %}`, `{% raw %}`, `{% comment %}`
- `{% seo %}` (jekyll-seo-tag style title/Open Graph/Twitter/JSON-LD block)
  and `{% highlight lang %}` (Pygments, rouge-compatible markup)
- outputs, filter chains, whitespace control (`{%- -%}`), dynamic lookups
  (`site[type]`), ranges (`(1..10)`), `contains`, `and/or/not`
- 60+ filters: `date`, `date_to_rfc822`, `date_to_xmlschema`, `append`,
  `split`, `default`, `xml_escape`, `relative_url`, `absolute_url`, `where`,
  `map`, `group_by`, `sort`, `divided_by`, `jsonify`, `strip_html`, ...
- Jekyll's lenient behaviour: `{{ ... }}` renders as an empty string, unknown
  filters pass their input through (both behave exactly like Ruby Liquid)

**Markdown & assets**

- kramdown/GFM-style Markdown via `python-markdown` + Pygments:
  fenced code with highlighting, tables, footnotes, attribute lists
  (`{: .class}` and `![](){: .responsive }`), strikethrough, bare-URL
  autolink, raw HTML
- MathJax-friendly math protection: `$x$`, `$$...$$`, `\( ... \)` and
  `\[ ... \]` survive Markdown untouched (but are *not* protected inside
  fenced code blocks, matching Jekyll)
- SCSS → CSS with `@import` resolution (`_sass/`, bundled themes)
- Static file copying (mtime/size aware), stale output pruning

**Site payload**: `site.title`, `site.time`, `site.pages`, `site.posts`
(newest first), `site.documents`, `site.static_files`, `site.data`,
`site.tags`, `site.categories`, `site.collections`, one entry per collection
(`site.notes`, ...), plus every top-level `_config.yml` key.

## What is *not* supported

- Ruby plugins. `plugins:` entries are checked: `jekyll-sitemap`,
  `jekyll-seo-tag` and `jekyll-include-cache` are emulated (a source
  `sitemap.xml` always wins), anything else produces a warning.
- Generators such as `jekyll-paginate`, `jekyll-feed`, `jekyll-related-posts`
  (there is no paginator object).
- Themes as gems: only their SCSS (`_sass/`) is resolved — jepyll ships the
  `jekyll-theme-cayman` partials bundled.
- `jekyll serve`'s WEBrick middlewares beyond extensionless URLs, incremental
  regeneration (`--incremental`), LSI, `excerpt_separator` rendering
  (`page_excerpts`), `jekyll-toc`, and the `github-metadata` drop
  (`site.github` resolves to `nil`, which is what GitHub Pages does on a
  non-GitHub build anyway).
- Plugins that rely on Ruby ERB/arbitrary code execution.

## Migrating an existing Jekyll blog

1. Point jepyll at your site: `jepyll build -s /path/to/blog`.
2. Read the warnings — they tell you about unknown plugins, missing layouts
   and unknown Liquid filters. Unknown filters are *not* fatal (Ruby Liquid
   ignores them too), so a clean build means a working site.
3. Compare the output with your current `_site/`:
   `diff <(cd old/_site && find . -type f | sort) <(cd new/_site && find . -type f | sort)`
4. `jepyll serve -s /path/to/blog` and click around.

Known behavioural differences worth knowing about:

- Output HTML is not byte-identical to kramdown/rouge (e.g. Pygments emits
  `<div class="highlight">` instead of kramdown's
  `<div class="language-x highlighter-rouge">` wrapper); token classes are
  rouge-compatible, so the same stylesheets apply.
- SCSS is compiled with libsass rather than dart-sass: attribute selectors
  are quoted (`input[type="button"]`) and comments land on their own line.
  Functionally equivalent CSS.
- Front matter and Liquid are evaluated per document exactly like Jekyll —
  content inside `{% raw %}` is never parsed, but code fences do *not*
  protect Liquid (neither do they in Jekyll).

## Architecture

```
jepyll/
├── cli.py          argparse CLI (build | serve | clean | new)
├── config.py       _config.yml loading + Jekyll defaults/excludes/collections
├── frontmatter.py  YAML front matter split (Jekyll's regexp semantics)
├── urls.py         slugify, permalink templates, destination paths
├── dates.py        timezones + Ruby-compatible strftime (%-d, %e, ...)
├── reader.py       walks the source: layouts, includes, collections, pages,
│                   static files, data (Jekyll's EntryFilter rules)
├── document.py     Page / Document / StaticFile + the Liquid drop view
├── site.py         payload, render pipeline, writing, pruning, --jobs workers
├── converters.py   Markdown (math masking, IAL, strikethrough, autolink)
├── sass.py         libsass / sass CLI backends
├── server.py       dev server with extensionless URLs + file watcher
├── liquid/
│   ├── lexer.py    tokens + whitespace control
│   ├── expr.py     expression parser, context, Ruby truthiness
│   ├── filters.py  the filter library
│   ├── nodes.py    AST nodes (if/for/case/include/...)
│   ├── parser.py   token stream → AST
│   └── tags.py     {% seo %}, {% highlight %}
└── data/themes/    bundled theme SCSS (jekyll-theme-cayman)
```

Rendering follows Jekyll's `Renderer#render_document`: Liquid → Markdown →
layout chain → write.

### Performance

Reading and rendering a 44 500-document site (30 k posts + 14.5 k pages,
250 MB of assets) takes:

| | wall time |
| --- | --- |
| `jepyll build` (serial) | ~2.2 min |
| `jepyll build -j 4` | ~42 s |

Workers use `fork` (Linux/macOS) so the parsed site is shared copy-on-write
instead of being re-read per process.

## Development

```bash
pip install -e ".[dev,sass]"
python -m pytest            # 41 tests: liquid, urls, dates, end-to-end build
```

The end-to-end test builds `tests/fixtures/mini_site`, a miniature site that
exercises layouts, includes, collections, drafts, permalinks, RSS, sitemap,
SCSS and the Markdown feature set, and asserts on the produced HTML.