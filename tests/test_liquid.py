"""Unit tests for the Liquid implementation."""

import datetime as dt

import pytest

from jepyll.liquid import Environment, LiquidSyntaxError, LiquidError
from jepyll.liquid.tags import build_custom_tags

SHANGHAI = dt.timezone(dt.timedelta(hours=8))


@pytest.fixture()
def env() -> Environment:
    includes = {
        "footer.html": "F:{{ site.title }}{% if include.x %} X={{ include.x }}{% endif %}",
        "missing-params.html": "[{{ include.a }}][{{ include.b }}]",
    }
    return Environment(loader=lambda name: includes[name], custom_tags=build_custom_tags())


def render(env: Environment, template: str, context=None, extra_registers=None) -> str:
    context = context or {}
    registers = {"site": context.get("site", {}), "page": context.get("page", {})}
    registers.update(extra_registers or {})
    return env.render(template, context, registers)


def test_variables_and_filters(env):
    assert render(env, "{{ site.title }}", {"site": {"title": "Hi"}}) == "Hi"
    assert render(env, "{{ 'a-b-c' | split: '-' | last }}") == "c"
    assert render(env, "{{ 'x' | prepend: 'y' | append: 'z' }}") == "yxz"
    assert render(env, "{{ nil | default: 'd' }}") == "d"
    assert render(env, "{{ '<b>x</b>' | strip_html }}") == "x"


def test_conditionals_and_truthiness(env):
    assert render(env, "{% if page.image %}y{% else %}n{% endif %}",
                  {"page": {"image": False}}) == "n"
    assert render(env, "{% if page.image %}y{% else %}n{% endif %}",
                  {"page": {"image": "a.png"}}) == "y"
    assert render(env, "{% unless a %}empty{% endunless %}") == "empty"
    assert render(env, "{% unless a %}x{% else %}y{% endunless %}", {"a": 1}) == "y"
    assert render(env, "{% if a contains 'b' %}y{% endif %}", {"a": "abc"}) == "y"
    assert render(env, "{% if site.title and page.title %}both{% endif %}",
                  {"site": {"title": "a"}, "page": {"title": "b"}}) == "both"


def test_case_when(env):
    template = "{% case page.lang %}{% when 'en' %}E{% when 'zh', 'yue' %}Z{% else %}O{% endcase %}"
    assert render(env, template, {"page": {"lang": "en"}}) == "E"
    assert render(env, template, {"page": {"lang": "yue"}}) == "Z"
    assert render(env, template, {"page": {"lang": "ja"}}) == "O"


def test_for_loops(env):
    posts = {"site": {"posts": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}}
    assert render(env, "{% for p in site.posts limit:2 %}{{ p.title }};{% endfor %}", posts) == "a;b;"
    assert render(env, "{% for p in site.posts reversed %}{{ p.title }}{% endfor %}", posts) == "cba"
    assert render(env, "{% for p in (1..3) reversed %}{{ p }}{% endfor %}") == "321"
    assert render(env, "{% for p in empty_list %}x{% else %}none{% endfor %}",
                  {"empty_list": []}) == "none"
    loop = "{% for i in (1..3) %}{% if forloop.first %}F{% endif %}{% if forloop.last %}L{% endif %}{% endfor %}"
    assert render(env, loop) == "FL"


def test_assign_capture_dynamic_lookup(env):
    assert render(env, "{% assign a = 'x' %}{{ a | upcase }}") == "X"
    assert render(env, "{% capture t %}{% if 1 == 1 %}yes{% endif %}{% endcapture %}[{{ t }}]") == "[yes]"
    assert render(env, "{{ site[type].size }}", {"site": {"notes": [1, 2, 3]}, "type": "notes"}) == "3"
    assert render(env, "{{ parts[2] }}", {"parts": ["_posts", "en", "file.md"]}) == "file.md"


def test_whitespace_control(env):
    assert render(env, "{%- if true -%}   spaced   {%- endif -%}") == "spaced"
    assert render(env, "a \n{%- if true -%} b {%- endif -%}") == "ab"


def test_raw_and_comment(env):
    assert render(env, "{% raw %}{{ not rendered }}{% endraw %}") == "{{ not rendered }}"
    assert render(env, "{% comment %}ignored {{ x }}{% endcomment %}ok") == "ok"
    # raw swallows malformed tags too
    assert render(env, "text {% raw %}{% %}{% endraw %} after") == "text {% %} after"


def test_includes(env):
    assert render(env, "{% include footer.html x='1' %}", {"site": {"title": "T"}}) == "F:T X=1"
    assert render(env, "{% include missing-params.html a='A' b=2 %}") == "[A][2]"


def test_missing_include_raises(env):
    with pytest.raises(LiquidError):
        render(env, "{% include nope.html %}")


def test_lenient_expressions_like_ruby(env):
    # Ruby Liquid turns non-expressions into lookups that render as ""
    assert render(env, "{{ ... }}") == ""
    assert render(env, "{{ 变量名 }}") == ""
    assert render(env, "{{ < math > }}") == ""
    assert render(env, "{{}}") == ""
    assert render(env, "{{ }}") == ""


def test_unknown_filter_is_lenient(env):
    assert render(env, '{{ "x" | asset_path }}') == "x"


def test_unknown_tag_raises(env):
    with pytest.raises(LiquidSyntaxError):
        render(env, "{% bogus %}x{% endbogus %}")


def test_unterminated_blocks_raise(env):
    with pytest.raises(LiquidSyntaxError):
        render(env, "{% if x %}no end")
    with pytest.raises(LiquidSyntaxError):
        render(env, "{% for i in list %}no end")


def test_date_filters(env):
    stamp = dt.datetime(2025, 1, 11, tzinfo=SHANGHAI)
    context = {"site": {"time": stamp}}
    assert render(env, "{{ site.time | date: '%Y.%m.%d' }}", context) == "2025.01.11"
    assert render(env, "{{ site.time | date_to_rfc822 }}", context) == "Sat, 11 Jan 2025 00:00:00 +0800"
    assert render(env, "{{ site.time | date_to_xmlschema }}", context) == "2025-01-11T00:00:00+08:00"
    assert render(env, "{{ '2024-03-10' | date: '%Y' }}") == "2024"


def test_numeric_filters(env):
    assert render(env, "{{ 10 | divided_by: 3 }}") == "3"
    assert render(env, "{{ 10 | divided_by: 3.0 }}")[:3] == "3.3"
    assert render(env, "{% assign x = 21 | divided_by: 7 | times: 6 %}{{ x }}") == "18"
    assert render(env, "{{ 2 | plus: 3 }}") == "5"


def test_array_filters(env):
    context = {"posts": [{"type": "note"}, {"type": "post"}, {"type": "note"}]}
    assert render(env, "{{ posts | where: 'type', 'note' | size }}", context) == "2"
    assert render(env, "{{ posts | map: 'type' | join: ',' }}", context) == "note,post,note"
    assert render(env, "{{ posts | size }}", context) == "3"


def test_relative_url_filter(env):
    registers = {"site": {"baseurl": "/blog", "url": "https://example.com"}}
    template = "{{ '/a/b.html' | relative_url }} {{ '/a/b.html' | absolute_url }}"
    result = env.render(template, {"site": registers["site"]}, registers)
    assert result == "/blog/a/b.html https://example.com/blog/a/b.html"