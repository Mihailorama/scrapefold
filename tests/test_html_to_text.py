"""Tests for scrapefold.html_to_text — HTML to plain text / markdown conversion.

Covers: empty input, plain paragraphs, heading hierarchy, bullet/ordered lists,
inline/block code, tables, links (with/without base_url), images, script/style
stripping, malformed HTML, nested elements, and html_to_both consistency.
"""

from __future__ import annotations

from scrapefold.html_to_text import html_to_both, html_to_markdown, html_to_text

# ---------------------------------------------------------------------------
# Empty / whitespace-only input
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty_text() -> None:
    assert html_to_text("") == ""


def test_empty_string_returns_empty_markdown() -> None:
    assert html_to_markdown("") == ""


def test_whitespace_only_returns_empty_text() -> None:
    assert html_to_text("   \n\t  ") == ""


def test_whitespace_only_returns_empty_markdown() -> None:
    assert html_to_markdown("   \n\t  ") == ""


# ---------------------------------------------------------------------------
# Plain paragraphs
# ---------------------------------------------------------------------------


def test_plain_paragraph_text() -> None:
    html = "<p>Hello, world.</p>"
    result = html_to_text(html)
    assert "Hello, world." in result


def test_plain_paragraph_markdown() -> None:
    html = "<p>Hello, world.</p>"
    result = html_to_markdown(html)
    assert "Hello, world." in result


def test_multiple_paragraphs_preserve_breaks_in_text() -> None:
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    result = html_to_text(html)
    assert "First paragraph." in result
    assert "Second paragraph." in result
    # The two paragraphs should be separated by whitespace (not run together)
    assert "First paragraph.Second" not in result


# ---------------------------------------------------------------------------
# Heading hierarchy
# ---------------------------------------------------------------------------


def test_h1_rendered_as_markdown_h1() -> None:
    html = "<h1>Main Title</h1>"
    result = html_to_markdown(html)
    assert result.strip().startswith("# ")
    assert "Main Title" in result


def test_h2_rendered_as_markdown_h2() -> None:
    html = "<h2>Section</h2>"
    result = html_to_markdown(html)
    assert "## " in result
    assert "Section" in result


def test_h3_rendered_as_markdown_h3() -> None:
    html = "<h3>Subsection</h3>"
    result = html_to_markdown(html)
    assert "### " in result
    assert "Subsection" in result


def test_headings_stripped_to_plain_text_in_text_mode() -> None:
    html = "<h1>Title</h1><h2>Subtitle</h2>"
    result = html_to_text(html)
    assert "Title" in result
    assert "Subtitle" in result
    # No markdown heading markers
    assert "#" not in result


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_unordered_list_in_markdown() -> None:
    html = "<ul><li>Apple</li><li>Banana</li><li>Cherry</li></ul>"
    result = html_to_markdown(html)
    assert "Apple" in result
    assert "Banana" in result
    assert "Cherry" in result
    # Should have list markers
    assert "-" in result or "*" in result


def test_ordered_list_in_markdown() -> None:
    html = "<ol><li>First</li><li>Second</li><li>Third</li></ol>"
    result = html_to_markdown(html)
    assert "First" in result
    assert "Second" in result
    assert "Third" in result
    # Should have numeric markers
    assert "1." in result


def test_list_items_in_text_mode() -> None:
    html = "<ul><li>Alpha</li><li>Beta</li></ul>"
    result = html_to_text(html)
    assert "Alpha" in result
    assert "Beta" in result


# ---------------------------------------------------------------------------
# Inline and block code
# ---------------------------------------------------------------------------


def test_inline_code_in_markdown() -> None:
    html = "<p>Use <code>print()</code> to output.</p>"
    result = html_to_markdown(html)
    assert "`print()`" in result or "`print()`" in result
    assert "print()" in result


def test_pre_code_block_in_markdown() -> None:
    html = "<pre><code>def hello():\n    return 42\n</code></pre>"
    result = html_to_markdown(html)
    assert "```" in result
    assert "def hello():" in result
    assert "return 42" in result


def test_pre_code_block_preserved_verbatim_in_text() -> None:
    html = "<pre><code>def hello():\n    return 42\n</code></pre>"
    result = html_to_text(html)
    assert "def hello():" in result
    assert "return 42" in result


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_table_in_markdown_uses_pipe_syntax() -> None:
    html = """
    <table>
      <tr><th>Name</th><th>Age</th></tr>
      <tr><td>Alice</td><td>30</td></tr>
      <tr><td>Bob</td><td>25</td></tr>
    </table>
    """
    result = html_to_markdown(html)
    assert "|" in result
    assert "Name" in result
    assert "Alice" in result
    assert "Bob" in result


def test_table_in_text_mode_has_content() -> None:
    html = """
    <table>
      <tr><th>Name</th><th>Age</th></tr>
      <tr><td>Alice</td><td>30</td></tr>
    </table>
    """
    result = html_to_text(html)
    assert "Name" in result
    assert "Alice" in result
    assert "30" in result


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_links_text_mode_no_base_url_keeps_anchor_text_drops_url() -> None:
    html = '<p>Visit <a href="https://example.com/foo">our site</a> for details.</p>'
    result = html_to_text(html)
    assert "our site" in result
    assert "https://example.com/foo" not in result


def test_links_text_mode_with_base_url_inlines_resolved_url() -> None:
    html = '<p>See <a href="/about">about page</a>.</p>'
    result = html_to_text(html, base_url="https://example.com")
    assert "about page" in result
    assert "https://example.com/about" in result


def test_links_text_mode_with_base_url_absolute_url_unchanged() -> None:
    html = '<p>Go to <a href="https://other.com/page">external</a>.</p>'
    result = html_to_text(html, base_url="https://example.com")
    assert "external" in result
    assert "https://other.com/page" in result


def test_links_markdown_mode_produces_markdown_link_syntax() -> None:
    html = '<a href="https://example.com">Example</a>'
    result = html_to_markdown(html)
    assert "Example" in result
    assert "https://example.com" in result


def test_links_markdown_mode_resolves_relative_with_base_url() -> None:
    html = '<a href="/docs">Docs</a>'
    result = html_to_markdown(html, base_url="https://example.com")
    assert "Docs" in result
    assert "https://example.com/docs" in result


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_images_dropped_in_text_mode() -> None:
    html = '<p>Before <img src="photo.jpg" alt="A photo"> After</p>'
    result = html_to_text(html)
    assert "Before" in result
    assert "After" in result
    assert "photo.jpg" not in result


def test_images_kept_in_markdown_mode() -> None:
    html = '<img src="photo.jpg" alt="A photo">'
    result = html_to_markdown(html)
    assert "photo.jpg" in result
    assert "A photo" in result


def test_images_markdown_syntax_uses_exclamation() -> None:
    html = '<img src="logo.png" alt="Logo">'
    result = html_to_markdown(html)
    assert "![" in result


# ---------------------------------------------------------------------------
# Script and style stripping
# ---------------------------------------------------------------------------


def test_script_tags_stripped_from_text() -> None:
    html = "<p>Content</p><script>alert('xss')</script>"
    result = html_to_text(html)
    assert "Content" in result
    assert "alert" not in result
    assert "xss" not in result


def test_style_tags_stripped_from_text() -> None:
    html = "<p>Content</p><style>body { color: red; }</style>"
    result = html_to_text(html)
    assert "Content" in result
    assert "color: red" not in result


def test_script_tags_stripped_from_markdown() -> None:
    html = "<h1>Title</h1><script>var x = 1;</script><p>Body</p>"
    result = html_to_markdown(html)
    assert "Title" in result
    assert "Body" in result
    assert "var x" not in result


def test_style_tags_stripped_from_markdown() -> None:
    html = "<h2>Heading</h2><style>.foo { margin: 0; }</style><p>Para</p>"
    result = html_to_markdown(html)
    assert "Heading" in result
    assert "Para" in result
    assert "margin" not in result


# ---------------------------------------------------------------------------
# Malformed HTML
# ---------------------------------------------------------------------------


def test_unclosed_tags_do_not_raise_in_text() -> None:
    html = "<p>Unclosed paragraph<p>Second paragraph"
    result = html_to_text(html)
    assert "Unclosed paragraph" in result
    assert "Second paragraph" in result


def test_unclosed_tags_do_not_raise_in_markdown() -> None:
    html = "<h1>Title<p>Content without closing tags"
    result = html_to_markdown(html)
    assert "Title" in result
    assert "Content without closing tags" in result


def test_deeply_malformed_html_does_not_raise() -> None:
    html = "<<p>>Broken<<<</p>"
    # Should not raise, should return something (even if empty)
    result = html_to_text(html)
    assert isinstance(result, str)


def test_none_like_empty_string_does_not_raise() -> None:
    # Simulate a whitespace-padded empty document
    html = "\n\n\t\n"
    result = html_to_text(html)
    assert result == ""


# ---------------------------------------------------------------------------
# Nested elements
# ---------------------------------------------------------------------------


def test_nested_elements_text_mode() -> None:
    html = "<div><p><strong>Bold</strong> and <em>italic</em> text.</p></div>"
    result = html_to_text(html)
    assert "Bold" in result
    assert "italic" in result
    assert "text." in result


def test_nested_list_items_in_markdown() -> None:
    html = """
    <ul>
      <li>Item 1
        <ul>
          <li>Sub-item A</li>
          <li>Sub-item B</li>
        </ul>
      </li>
      <li>Item 2</li>
    </ul>
    """
    result = html_to_markdown(html)
    assert "Item 1" in result
    assert "Sub-item A" in result
    assert "Sub-item B" in result
    assert "Item 2" in result


# ---------------------------------------------------------------------------
# html_to_both consistency
# ---------------------------------------------------------------------------


def test_html_to_both_returns_tuple() -> None:
    html = "<p>Hello</p>"
    result = html_to_both(html)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_html_to_both_text_matches_html_to_text() -> None:
    html = "<h1>Title</h1><p>Body text here.</p>"
    text, _markdown = html_to_both(html)
    assert text == html_to_text(html)


def test_html_to_both_markdown_matches_html_to_markdown() -> None:
    html = "<h1>Title</h1><p>Body text here.</p>"
    _text, markdown = html_to_both(html)
    assert markdown == html_to_markdown(html)


def test_html_to_both_with_base_url_propagates() -> None:
    html = '<p>See <a href="/page">page</a>.</p>'
    text, markdown = html_to_both(html, base_url="https://site.com")
    assert "https://site.com/page" in text
    assert "https://site.com/page" in markdown


def test_html_to_both_empty_input() -> None:
    text, markdown = html_to_both("")
    assert text == ""
    assert markdown == ""


# ---------------------------------------------------------------------------
# Whitespace collapsing
# ---------------------------------------------------------------------------


def test_excessive_whitespace_collapsed_in_text() -> None:
    html = "<p>Too    many     spaces   here.</p>"
    result = html_to_text(html)
    # Should not have runs of multiple spaces
    assert "    " not in result
    assert "Too" in result
    assert "spaces" in result


def test_no_leading_trailing_whitespace_in_text() -> None:
    html = "<p>Clean text.</p>"
    result = html_to_text(html)
    assert result == result.strip()
