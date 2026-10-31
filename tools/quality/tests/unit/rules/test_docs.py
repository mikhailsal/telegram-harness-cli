import pytest

from quality.policy import Category, Thresholds
from quality.rules.docs import check_docs, is_page
from quality.rules.model import CandidateFile
from quality.testkit.candidates import candidate
from quality.testkit.policies import build_policy

COMMANDS = {"check": frozenset[str](), "policy": frozenset({"validate", "schema"})}
DEFAULTS = build_policy().thresholds
SMALL = DEFAULTS.model_copy(
    update={"doc_page_lines": 4, "doc_page_words": 6, "agents_lines": 3, "agents_words": 4}
)
UNINDEXED = "page is not linked from README.md, AGENTS.md, or a linked page"

type Found = list[tuple[str, str, str]]


def doc(path: str, text: str | bytes | None) -> CandidateFile:
    return candidate(path, text, Category.DOCS)


def run(*files: CandidateFile, thresholds: Thresholds = DEFAULTS) -> Found:
    return [
        (d.rule, d.message, d.location.render() if d.location else "")
        for d in check_docs(files, thresholds, COMMANDS)
    ]


def agents(*pages: str) -> CandidateFile:
    return doc("AGENTS.md", "".join(f"[p]({page})\n" for page in pages) or "# Agents\n")


def test_defaults_are_the_template_thresholds() -> None:
    assert (DEFAULTS.doc_page_lines, DEFAULTS.doc_page_words) == (300, 2000)
    assert (DEFAULTS.agents_lines, DEFAULTS.agents_words) == (150, 1000)


def test_a_linked_and_consistent_corpus_passes() -> None:
    files = [
        doc("AGENTS.md", "# Agents\n\nSee [docs](docs/) and [readme](README.md#top).\n"),
        doc("README.md", "# Top\n\nRun `quality check`.\n"),
        doc("docs/README.md", "# Docs\n\n- [Guide](guide.md#usage)\n"),
        doc("docs/guide.md", "# Guide\n\n## Usage\n\n[run](../tools/run.py#L3) [up](#guide)\n"),
        candidate("tools/run.py", "print(1)\n"),
    ]
    assert run(*files) == []


def test_agents_md_is_required() -> None:
    assert run(doc("README.md", "# Top\n")) == [
        ("docs.agents-missing", "the repository root has no AGENTS.md", "")
    ]


def test_without_an_index_every_page_is_unindexed() -> None:
    assert run(doc("docs/a.md", "# A\n"), doc("CLAUDE.md", "# Claude\n"))[1:] == [
        ("docs.unindexed", UNINDEXED, "docs/a.md"),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a b c\n\n\n\n", None),
        ("a b c\n\n\n\nd", "5 lines (max 4) and 4 prose words (max 6)"),
        ("a b c\n\n\n\n\n", "5 lines (max 4) and 3 prose words (max 6)"),
        ("one two three four five six\n", None),
        ("one two three four five six seven\n", "1 lines (max 4) and 7 prose words (max 6)"),
        (
            "x\n\n```sh\ncode words are not prose\n```\n",
            "5 lines (max 4) and 1 prose words (max 6)",
        ),
        ("one two `code` <br> three four five six -- *\n", None),
        ("one <b>bold</b> two\n", None),
    ],
)
def test_page_size_counts_physical_lines_and_prose_words(text: str, expected: str | None) -> None:
    found = run(agents("docs/page.md"), doc("docs/page.md", text), thresholds=SMALL)
    assert found == ([("docs.page-size", expected, "docs/page.md")] if expected else [])


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md"])
def test_agent_files_use_the_agent_limits(name: str) -> None:
    within_page_limits = "[x](README.md)\n\n\none\n"
    files = [doc("README.md", "# Top\n"), agents("README.md"), doc(name, within_page_limits)]
    assert run(*files, thresholds=SMALL) == [
        ("docs.agents-size", "4 lines (max 3) and 2 prose words (max 4)", name)
    ]


def test_agent_word_limit_is_separate() -> None:
    files = [agents(), doc("CLAUDE.md", "one two three four five\n")]
    assert run(*files, thresholds=SMALL) == [
        ("docs.agents-size", "1 lines (max 3) and 5 prose words (max 4)", "CLAUDE.md")
    ]


@pytest.mark.parametrize(
    "file",
    [
        doc("LICENSE.md", "x\n" * 10),
        doc("docs/NOTICE.txt", "x\n" * 10),
        doc("COPYING.rst", "x\n" * 10),
        doc("licence.markdown", "x\n" * 10),
        doc("docs/data.json", "x\n" * 10),
        candidate("src/notes.md", "x\n" * 10, Category.SOURCE),
        candidate("tests/fixture.md", "x\n" * 10, Category.TESTS),
        candidate("vendor/lib/README.md", "x\n" * 10, Category.VENDOR),
    ],
    ids=lambda file: file.path,
)
def test_legal_notices_and_other_categories_are_not_pages(file: CandidateFile) -> None:
    assert not is_page(file)
    assert run(agents(), file, thresholds=SMALL) == []


def test_other_page_formats_are_size_checked_but_not_parsed() -> None:
    files = [
        agents(),
        doc("docs/guide.rst", "Title\n=====\n\n`broken <missing.rst>`_ and words\n\n"),
        doc("docs/notes.TXT", "quality nope [x](missing.md)\n"),
        doc("docs/manual.adoc", "== Title\n"),
    ]
    assert run(*files, thresholds=SMALL) == [
        ("docs.page-size", "5 lines (max 4) and 5 prose words (max 6)", "docs/guide.rst")
    ]


def test_undecodable_and_unreadable_pages_are_left_to_other_rules() -> None:
    files = [agents(), doc("docs/latin1.md", b"# Caf\xe9\n" * 10), doc("docs/link.md", None)]
    assert run(*files, thresholds=SMALL) == []


def test_broken_links_are_reported_at_their_lines() -> None:
    text = (
        "# Page\n"
        "\n"
        "[ok](https://example.com) [mail](mailto:a@b.c) [top](#page) [back](#)\n"
        "[missing](gone.md) [case](Other.md)\n"
        "[escape](../../x.md) [empty]()\n"
        '<a href="other.md#nope">x</a> ![img](img/logo.PNG)\n'
    )
    files = [
        agents("docs/page.md", "docs/other.md"),
        doc("docs/page.md", text),
        doc("docs/other.md", "# Other\n"),
        candidate("docs/img/logo.png", b"\x89PNG", Category.ASSETS),
    ]
    assert run(*files) == [
        ("docs.broken-link", "'docs/gone.md' does not exist", "docs/page.md:4"),
        (
            "docs.broken-link",
            "'docs/Other.md' differs in case from 'docs/other.md'",
            "docs/page.md:4",
        ),
        ("docs.broken-link", "'../../x.md' points outside the repository", "docs/page.md:5"),
        ("docs.broken-link", "empty link target", "docs/page.md:5"),
        (
            "docs.broken-link",
            "'docs/other.md' has no heading or anchor '#nope'",
            "docs/page.md:6",
        ),
        (
            "docs.broken-link",
            "'docs/img/logo.PNG' differs in case from 'docs/img/logo.png'",
            "docs/page.md:6",
        ),
    ]


def test_pages_are_indexed_through_chains_and_directory_links() -> None:
    files = [
        doc("README.md", "[docs](docs)\n"),
        doc("CLAUDE.md", "[c](notes/c.md)\n"),
        agents(),
        doc("docs/README.md", "[a](a.md) [ext](https://example.com/b.md)\n"),
        doc("docs/a.md", "[b](sub/b.md) [loop](../README.md)\n"),
        doc("docs/sub/b.md", "[a](../a.md)\n"),
        doc("notes/c.md", "# C\n"),
        doc("docs/orphan.md", "[a](a.md)\n"),
        doc("docs/sub/also-orphan.markdown", "# Orphan\n"),
        doc(".github/PULL_REQUEST_TEMPLATE.md", "# Template\n"),
        doc("docs/.drafts/idea.md", "# Idea\n"),
        doc(".hidden-page.md", "# Not hidden by its own name\n"),
    ]
    assert run(*files) == [
        ("docs.unindexed", UNINDEXED, ".hidden-page.md"),
        ("docs.unindexed", UNINDEXED, "docs/orphan.md"),
        ("docs.unindexed", UNINDEXED, "docs/sub/also-orphan.markdown"),
    ]


def test_a_link_to_an_undecodable_page_does_not_index_through_it() -> None:
    files = [
        agents("docs/latin1.md"),
        doc("docs/latin1.md", b"[next](next.md)\xe9\n"),
        doc("docs/next.md", "# Next\n"),
    ]
    assert run(*files) == [("docs.unindexed", UNINDEXED, "docs/next.md")]


def test_documented_commands_must_exist() -> None:
    text = (
        "# Commands\n"
        "\n"
        "`quality check --json`, `quality check anything`, `quality policy validate`.\n"
        "`quality --version`, `quality <command>`, `quality policy <action>`, `quality ...`.\n"
        "`quality [cmd]`, `quality {a,b}`, `quality $COMMAND`, `quality policy --help`.\n"
        "`quality nope`, `quality policy nope`, `quality check-all`.\n"
        "\n"
        "```sh\n"
        "quality policy schema --check\n"
        "./quality bootstrap\n"
        "```\n"
    )
    assert run(agents("doc.md"), doc("doc.md", text)) == [
        ("docs.unknown-command", "'quality nope' is not a command", "doc.md:6"),
        ("docs.unknown-command", "'quality policy nope' is not a command", "doc.md:6"),
        ("docs.unknown-command", "'quality check-all' is not a command", "doc.md:6"),
        ("docs.unknown-command", "'quality bootstrap' is not a command", "doc.md:10"),
    ]
