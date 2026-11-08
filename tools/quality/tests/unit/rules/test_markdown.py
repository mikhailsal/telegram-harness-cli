import pytest

from quality.rules.markdown import Command, Link, count_words, parse_markdown, slug


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, World!", "hello-world"),
        ("Über straße 2.0", "über-straße-20"),
        ("Привет мир", "привет-мир"),
        ("日本語の見出し", "日本語の見出し"),
        ("snake_case and-dash", "snake_case-and-dash"),
        ("  leading space", "--leading-space"),
        ("Café (é) \u2013 \u201cquotes\u201d", "café-é--quotes"),
        ("C++ & C#", "c--c"),
    ],
)
def test_slug_follows_github(text: str, expected: str) -> None:
    assert slug(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("one two  three", 3),
        ("-- — * 42 ok", 2),
        ("a\tb\nc", 3),
        ("(x) [y]", 2),
    ],
)
def test_words_need_a_letter_or_digit(text: str, expected: int) -> None:
    assert count_words(text) == expected


def test_duplicate_headings_get_numbered_anchors() -> None:
    facts = parse_markdown("# Setup\n\n## Setup\n\n## Set-up!\n\n### Setup-1\n\n# Setup\n")
    assert facts.anchors == {"setup", "setup-1", "set-up", "setup-1-1", "setup-2"}


def test_heading_anchors_include_code_emphasis_and_link_text() -> None:
    facts = parse_markdown("## Run `quality check` *now*\n\n## See [the docs](x.md)\n")
    assert facts.anchors == {"run-quality-check-now", "see-the-docs"}


def test_html_ids_and_names_are_anchors() -> None:
    text = '<a id="custom"></a> and <a name=\'other\'></a>\n\n<div ID="block">\n</div>\n'
    assert parse_markdown(text).anchors == {"custom", "other", "block"}


def test_links_images_and_html_targets_keep_their_lines() -> None:
    text = (
        "First [a](a.md#x) and ![img](pic.png)\n"
        "soft break [b](b.md)  \n"
        "hard break <https://example.com> [c](c.md)\n"
        "\n"
        "Inline <img src=\"i.png\"> <a href='d.md'>d</a>\n"
        "\n"
        "<div>\n"
        '<a href="e.md">e</a>\n'
        "<img SRC='f.png'>\n"
        "</div>\n"
        "\n"
        "| a | b |\n"
        "| --- | --- |\n"
        "| [t](t.md) | x |\n"
    )
    assert parse_markdown(text).links == [
        Link("a.md#x", 1),
        Link("pic.png", 1),
        Link("b.md", 2),
        Link("https://example.com", 3),
        Link("c.md", 3),
        Link("i.png", 5),
        Link("d.md", 5),
        Link("e.md", 8),
        Link("f.png", 9),
        Link("t.md", 14),
    ]


def test_prose_excludes_code_html_and_autolink_text() -> None:
    text = (
        "# Title words\n"
        "\n"
        "Some *emphasis* and `code span` here <https://example.com/long/path>\n"
        "\n"
        "    indented code block words\n"
        "\n"
        "```text\n"
        "fenced words are not prose\n"
        "```\n"
        "\n"
        "<div>html block words</div>\n"
    )
    facts = parse_markdown(text)
    assert facts.prose == [
        (1, "Title words"),
        (3, "Some "),
        (3, "emphasis"),
        (3, " and "),
        (3, " here "),
    ]
    assert facts.prose_words == 6


def test_commands_come_from_inline_code_and_shell_fences() -> None:
    text = (
        "Run `quality check --json` or `./quality explain rule.id`, not quality prose.\n"
        "\n"
        "```sh\n"
        "quality tools install\n"
        "cd x && quality policy validate; (quality inventory)\n"
        "echo quality-check notquality check\n"
        "```\n"
        "\n"
        "```python\n"
        "quality fake\n"
        "```\n"
        "\n"
        "```\n"
        "quality bare\n"
        "```\n"
        "\n"
        "```console title=example\n"
        "$ quality inventory --rev HEAD\n"
        "```\n"
        "\n"
        "    quality indented\n"
    )
    assert parse_markdown(text).commands == [
        Command(("check", "--json"), 1),
        Command(("explain", "rule.id"), 1),
        Command(("tools", "install"), 4),
        Command(("policy", "validate"), 5),
        Command(("inventory",), 5),
        Command(("bare",), 14),
        Command(("inventory", "--rev", "HEAD"), 18),
    ]


def test_a_bare_program_name_is_not_a_command() -> None:
    assert parse_markdown("Use `quality` here.\n\n```sh\nquality\n```\n").commands == []


def test_empty_document_has_no_facts() -> None:
    facts = parse_markdown("")
    assert (facts.anchors, facts.links, facts.commands, facts.prose) == (set(), [], [], [])
    assert facts.prose_words == 0
