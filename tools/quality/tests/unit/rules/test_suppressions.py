import pytest

from quality.diagnostics import Location, Severity
from quality.policy import Category
from quality.rules.model import CandidateFile
from quality.rules.suppressions import check_suppressions, markers
from quality.testkit.candidates import candidate

# Built at run time so this file does not carry the marker the secret scanner honors.
SCANNER = "gitleaks" + ":allow"


def found(file: CandidateFile) -> list[tuple[int, str]]:
    return list(markers(file))


def code(
    path: str, text: str | bytes, language: str, category: Category = Category.SOURCE
) -> CandidateFile:
    return candidate(path, text, category, language=language)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("x = 1  # noqa: E501\n", [(1, "noqa: E501")]),
        ("x = 1  # NoQA:   E501   \n", [(1, "NoQA: E501")]),
        ("x: int = f()  # type: ignore[assignment]\n", [(1, "type: ignore[assignment]")]),
        ("if x:  # pragma: no cover\n    pass\n", [(1, "pragma: no cover")]),
        ("# fmt: off\nx = [1,2]\n# fmt: on\n", [(1, "fmt: off")]),
        ("# pyright: basic\n", [(1, "pyright: basic")]),
        ("import os  # ruff: noqa\n", [(1, "ruff: noqa")]),
        ("x = 1  # nosec B101\n", [(1, "nosec B101")]),
        ("# see: type: ignore below\n", [(1, "type: ignore below")]),
        ('s = "# noqa"\n', []),
        ('s = """\n# type: ignore\n"""\n', []),
        ("# an ordinary comment\nx = 1\n", []),
    ],
)
def test_python_comments_are_found_with_the_tokenizer(
    text: str, expected: list[tuple[int, str]]
) -> None:
    assert found(code("src/app/a.py", text, "python")) == expected


def reported(file: CandidateFile) -> list[tuple[int, str]]:
    found = check_suppressions([file])
    return [(d.location.line or 0, d.data["symbol"]) for d in found if d.location is not None]


def test_python_markers_before_a_tokenizer_error_are_reported_once() -> None:
    assert reported(code("src/app/a.py", "x = (\n# noqa\n", "python")) == [(2, "noqa")]


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("def f():\n  x = 1\n y = 2  # noqa\n", 3),
        ("x = '\0'\ny = 2  # noqa\n", 2),
        (b"x = '\xff'\ny = 2  # noqa\n", 2),
    ],
    ids=["dedent", "null-byte", "not-utf-8"],
)
def test_python_files_that_do_not_tokenize_are_scanned_by_line(
    text: str | bytes, line: int
) -> None:
    assert reported(code("src/app/a.py", text, "python")) == [(line, "noqa")]


def test_lines_are_counted_by_newlines_only() -> None:
    text = "a = 1\f\x0b\u2028\nb = 2  # noqa\n"
    assert reported(code("src/app/a.py", text, "python")) == [(2, "noqa")]
    script = code("web/a.ts", "a\u2028b\n// eslint-disable-line\n", "typescript")
    assert reported(script) == [(2, "eslint-disable-line")]
    doc = candidate("docs/a.md", f"x\u2028y\n{SCANNER}\n", Category.DOCS)
    assert reported(doc) == [(2, SCANNER)]


@pytest.mark.parametrize("category", [Category.TESTS, Category.CONFIG])
def test_python_markers_count_in_tests_and_configuration(category: Category) -> None:
    file = code("tests/conftest.py", "import x  # noqa: F401\n", "python", category)
    assert found(file) == [(1, "noqa: F401")]


@pytest.mark.parametrize("category", [Category.DOCS, Category.GENERATED, Category.VENDOR])
def test_tool_markers_are_ignored_where_tools_do_not_check(category: Category) -> None:
    file = code("x/a.py", "x = 1  # noqa\n", "python", category)
    assert found(file) == []


@pytest.mark.parametrize("language", ["typescript", "javascript"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("// eslint-disable-next-line no-console\n", [(1, "eslint-disable-next-line no-console")]),
        ("x(); /* istanbul ignore next */\n", [(1, "istanbul ignore next")]),
        ("/**\n * @ts-expect-error legacy\n */\n", [(2, "@ts-expect-error legacy")]),
        ("// @ts-ignore\n", [(1, "@ts-ignore")]),
        ("/* c8 ignore start */\n", [(1, "c8 ignore start")]),
        ("// biome-ignore lint: reason\n", [(1, "biome-ignore lint: reason")]),
        ('const s = "eslint-disable";\n', []),
        ('const s = "eslint-disable"; // fine\n', []),
        ("// a plain comment\n", []),
    ],
)
def test_script_markers_must_be_in_comments(
    language: str, text: str, expected: list[tuple[int, str]]
) -> None:
    assert found(code("web/src/a.ts", text, language)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("print(x); // ignore: avoid_print\n", [(1, "ignore: avoid_print")]),
        ("// ignore_for_file: type=lint\n", [(1, "ignore_for_file: type=lint")]),
        ("// coverage:ignore-line\n", [(1, "coverage:ignore-line")]),
        ("// dart format off\n", [(1, "dart format off")]),
        ("final s = 'ignore: x';\n", []),
    ],
)
def test_dart_markers(text: str, expected: list[tuple[int, str]]) -> None:
    assert found(code("lib/a.dart", text, "dart")) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# @phpstan-ignore-next-line\n", [(1, "@phpstan-ignore-next-line")]),
        ("$a = 1; // phpcs:ignore Generic.Rule\n", [(1, "phpcs:ignore Generic.Rule")]),
        ("/** @psalm-suppress MixedAssignment */\n", [(1, "@psalm-suppress MixedAssignment")]),
        ("/**\n * @codeCoverageIgnore\n */\n", [(2, "@codeCoverageIgnore")]),
        ("$s = '@phpstan-ignore';\n", []),
    ],
)
def test_php_markers(text: str, expected: list[tuple[int, str]]) -> None:
    assert found(code("src/A.php", text, "php")) == expected


def test_unknown_languages_only_report_the_scanner_marker() -> None:
    assert found(code("tool.go", "// nolint\n", "go")) == []


def test_markdown_lint_markers_count_in_documentation() -> None:
    text = (
        "# Title\n\n<!-- pyml disable-next-line md013 -->\nlong line\n<!--pyml disable md033-->\n"
    )
    file = candidate("docs/page.md", text, Category.DOCS)
    assert found(file) == [(3, "pyml disable-next-line md013"), (5, "pyml disable md033")]


@pytest.mark.parametrize(
    "file",
    [
        candidate("docs/page.rst", "<!-- pyml disable md013 -->\n", Category.DOCS),
        candidate("src/app/page.md", "<!-- pyml disable md013 -->\n", Category.SOURCE),
        candidate("docs/page.md", "pyml disable outside a comment\n", Category.DOCS),
    ],
    ids=["not-markdown", "not-docs", "not-a-comment"],
)
def test_markdown_markers_elsewhere_are_ignored(file: CandidateFile) -> None:
    assert found(file) == []


@pytest.mark.parametrize("category", list(Category))
def test_the_scanner_marker_counts_in_every_category(category: Category) -> None:
    file = candidate("any/file.txt", f"token = 'abc'  # {SCANNER}\n", category)
    assert found(file) == [(1, SCANNER)]


def test_the_scanner_marker_counts_in_binary_content() -> None:
    file = candidate("img.png", b"\x89PNG\xff\n" + SCANNER.encode() + b"\x00\n", Category.ASSETS)
    assert found(file) == [(2, SCANNER)]


def test_python_and_scanner_markers_on_one_line_are_both_reported() -> None:
    file = code("src/app/a.py", f"key = 'x'  # noqa: S105 {SCANNER}\n", "python")
    assert found(file) == [(1, f"noqa: S105 {SCANNER}"), (1, SCANNER)]


@pytest.mark.parametrize("category", [Category.CONFIG, Category.ASSETS])
def test_each_scanner_ignore_entry_is_a_suppression(category: Category) -> None:
    text = "# reviewed\n\n  src/a.py:generic-api-key:3  \nabc123:b.py:aws-access-token:9\n"
    file = candidate("sub/.gitleaksignore", text, category)
    assert found(file) == [(3, "src/a.py:generic-api-key:3"), (4, "abc123:b.py:aws-access-token:9")]


def test_files_merely_named_like_the_ignore_file_are_not_ignore_lists() -> None:
    assert found(candidate("docs/gitleaksignore.md", "a:b:1\n", Category.DOCS)) == []


def test_links_and_unread_blobs_have_no_markers() -> None:
    assert found(candidate("src/app/link.py", None, language="python")) == []


def test_each_marker_is_a_diagnostic_with_its_symbol() -> None:
    file = code("src/app/a.py", "x = 1  # noqa: E501\n", "python")
    [diagnostic] = check_suppressions([file])
    assert (
        diagnostic.rule,
        diagnostic.severity,
        diagnostic.message,
        diagnostic.location,
        diagnostic.component,
        dict(diagnostic.data),
    ) == (
        "suppressions.inline",
        Severity.ERROR,
        "inline suppression 'noqa: E501' needs a registered exception",
        Location("src/app/a.py", 1),
        "app",
        {"symbol": "noqa: E501"},
    )


def test_each_file_reports_its_own_markers() -> None:
    file = candidate("src/app/a.py", "x = 1  # noqa\n", language="python")
    assert [d.location for d in check_suppressions([file, file])] == [
        Location("src/app/a.py", 1),
        Location("src/app/a.py", 1),
    ]
