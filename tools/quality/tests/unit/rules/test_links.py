import pytest

from quality.policy import Category
from quality.rules.links import Corpus, Target, is_markdown, resolve
from quality.testkit.candidates import candidate


@pytest.mark.parametrize(
    ("path", "expected"),
    [("a.md", True), ("docs/B.MD", True), ("c.markdown", True), ("d.rst", False), ("md", False)],
)
def test_markdown_pages_are_recognized_by_suffix(path: str, expected: bool) -> None:
    assert is_markdown(path) is expected


@pytest.mark.parametrize(
    "target",
    ["https://example.com/a.md", "http://x", "mailto:a@b.c", "ftp://x/y", "//cdn.example/x.md"],
)
def test_external_links_are_not_resolved(target: str) -> None:
    assert resolve("docs/a.md", target) is None


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ("docs/a.md", "b.md", Target("docs/b.md", None)),
        ("docs/a.md", "./b.md#Part", Target("docs/b.md", "Part")),
        ("docs/a.md", "../README.md", Target("README.md", None)),
        ("docs/a.md", "/tools/x.py#L10", Target("tools/x.py", "L10")),
        ("docs/a.md", "/", Target(".", None)),
        ("docs/a.md", "sub/", Target("docs/sub", None)),
        ("docs/a.md", "b.md?plain=1#x", Target("docs/b.md", "x")),
        ("docs/a.md", "my%20file.md#%C3%BCber", Target("docs/my file.md", "über")),
        ("docs/a.md", "b.md#", Target("docs/b.md", None)),
        ("docs/a.md", "#local", Target("docs/a.md", "local")),
        ("docs/a.md", "#", Target("docs/a.md", None)),
        ("docs/a.md", "?query", Target("docs/a.md", None)),
        ("a.md", ".", Target(".", None)),
    ],
)
def test_internal_links_resolve_to_repository_paths(
    source: str, target: str, expected: Target
) -> None:
    assert resolve(source, target) == expected


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ("docs/a.md", "", "empty link target"),
        ("a.md", "../x.md", "'../x.md' points outside the repository"),
        ("a.md", "..", "'..' points outside the repository"),
        ("docs/a.md", "../../x.md#y", "'../../x.md#y' points outside the repository"),
    ],
)
def test_impossible_links_are_explained(source: str, target: str, expected: str) -> None:
    assert resolve(source, target) == expected


def corpus() -> Corpus:
    return Corpus(
        [
            candidate("README.md", "# Top\n\nSee [docs](docs/) and [guide](docs/guide.md).\n"),
            candidate("docs/README.md", "# Docs\n\n## Index\n", Category.DOCS),
            candidate("docs/guide.md", "# Guide\n\n[x](https://a.b) [y](../missing.md)\n"),
            candidate("docs/deep/nested/page.md", "# Deep\n", Category.DOCS),
            candidate("docs/deep/nested/other.md", "# Other\n", Category.DOCS),
            candidate("tools/run.py", "print(1)\n"),
            candidate("docs/latin1.md", b"# Caf\xe9\n", Category.DOCS),
            candidate("docs/link.md", None, Category.DOCS),
            candidate("notes/plain.txt", "[x](../README.md)\n", Category.DOCS),
        ]
    )


def test_directories_include_every_ancestor_and_the_root() -> None:
    assert corpus().directories == {
        ".",
        "docs",
        "docs/deep",
        "docs/deep/nested",
        "tools",
        "notes",
    }


def test_facts_are_parsed_once_and_only_for_utf8_markdown() -> None:
    found = corpus()
    first = found.facts("docs/README.md")
    assert first is not None
    assert first.anchors == {"docs", "index"}
    assert found.facts("docs/README.md") is first
    assert found.facts("docs/latin1.md") is None
    assert found.facts("docs/link.md") is None
    assert found.facts("notes/plain.txt") is None
    assert found.facts("docs/absent.md") is None


@pytest.mark.parametrize(
    ("target", "page"),
    [
        (Target("docs", None), "docs/README.md"),
        (Target(".", "top"), "README.md"),
        (Target("docs/guide.md", None), "docs/guide.md"),
    ],
)
def test_directories_show_their_readme(target: Target, page: str) -> None:
    assert corpus().page_of(target) == page


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (Target("docs/guide.md", None), None),
        (Target("docs", None), None),
        (Target("docs/README.md", "index"), None),
        (Target("docs", "index"), None),
        (Target(".", "top"), None),
        (Target("tools/run.py", "L10"), None),
        (Target("tools/run.py", "L1-L5"), None),
        (Target("tools/run.py", "L2C3-L4C1"), None),
        (Target("Docs/Guide.md", None), "'Docs/Guide.md' differs in case from 'docs/guide.md'"),
        (Target("DOCS", None), "'DOCS' differs in case from 'docs'"),
        (Target("docs/absent.md", None), "'docs/absent.md' does not exist"),
        (Target("docs/README.md", "Index"), "'docs/README.md' has no heading or anchor '#Index'"),
        (Target("docs", "missing"), "'docs/README.md' has no heading or anchor '#missing'"),
        (Target("docs/guide.md", "L10"), "'docs/guide.md' has no heading or anchor '#L10'"),
        (Target("tools/run.py", "main"), "'tools/run.py' has no anchors; '#main' cannot resolve"),
        (Target("tools", "L1"), "'tools' has no anchors; '#L1' cannot resolve"),
        (
            Target("docs/latin1.md", "cafe"),
            "'docs/latin1.md' has no anchors; '#cafe' cannot resolve",
        ),
    ],
)
def test_targets_must_exist_with_exact_case_and_anchor(
    target: Target, expected: str | None
) -> None:
    assert corpus().problem(target) == expected


def test_linked_pages_are_existing_markdown_pages() -> None:
    found = corpus()
    assert list(found.linked_pages("README.md")) == ["docs/README.md", "docs/guide.md"]
    assert list(found.linked_pages("docs/guide.md")) == []
    assert list(found.linked_pages("notes/plain.txt")) == []
    assert list(found.linked_pages("docs/absent.md")) == []


def test_links_to_non_markdown_files_and_escapes_are_not_pages() -> None:
    found = Corpus(
        [
            candidate("README.md", "[a](tools/run.py) [b](../out.md) [c](mailto:x@y.z) [d]()\n"),
            candidate("tools/run.py", "print(1)\n"),
        ]
    )
    assert list(found.linked_pages("README.md")) == []
