import pytest

from quality.diagnostics import Location, Severity
from quality.discovery import FileRecord, Region
from quality.policy import Category, Limit, Thresholds
from quality.rules.files import check_blob, check_files, limit_severity, limit_text
from quality.rules.model import CandidateFile, physical_lines
from quality.testkit.candidates import candidate
from quality.testkit.policies import build_policy

THRESHOLDS: Thresholds = build_policy().thresholds
BLOB = THRESHOLDS.blob_bytes


def findings(file: CandidateFile) -> list[tuple[str, str, Severity, str | None]]:
    found = list(check_files([file], THRESHOLDS))
    assert {d.location for d in found} <= {Location(file.path)}
    return [(d.rule, d.message, d.severity, d.component) for d in found]


def test_candidate_builds_records_for_each_category() -> None:
    source = candidate("src/app/a.py", "x = 1\n", language="python")
    assert source.record == FileRecord(
        "src/app/a.py", Category.SOURCE, "app", "python", Region.SOURCE
    )
    assert (source.size, source.data) == (6, b"x = 1\n")
    tests = candidate("tests/test_a.py", b"\x00", Category.TESTS)
    assert (tests.record.region, tests.size, tests.data) == (Region.TESTS, 1, b"\x00")
    docs = candidate("README.md", None, Category.DOCS, component=None)
    assert (docs.record.region, docs.record.component, docs.size, docs.data) == (
        Region.OTHER,
        None,
        0,
        None,
    )


def test_candidate_file_exposes_path_category_and_text() -> None:
    file = candidate("docs/a.md", "héllo", Category.DOCS)
    assert (file.path, file.category, file.text()) == ("docs/a.md", Category.DOCS, "héllo")
    assert candidate("a.bin", b"\xff\xfe", Category.ASSETS).text() is None
    assert candidate("link", None).text() is None


@pytest.mark.parametrize(
    ("data", "expected"),
    [(b"", 0), (b"a", 1), (b"a\n", 1), (b"a\nb", 2), (b"\n\n", 2), (b"a\r\nb\r\n", 2)],
)
def test_physical_lines_count_an_unterminated_last_line(data: bytes, expected: int) -> None:
    assert physical_lines(data) == expected


@pytest.mark.parametrize(
    ("value", "limit", "expected"),
    [
        (3, Limit(warning=3, error=4), None),
        (4, Limit(warning=3, error=4), Severity.WARNING),
        (5, Limit(warning=3, error=4), Severity.ERROR),
        (50, Limit(error=50), None),
        (51, Limit(error=50), Severity.ERROR),
        (5, Limit(warning=5), None),
        (6, Limit(warning=5), Severity.WARNING),
    ],
)
def test_limit_severity_triggers_only_above_the_threshold(
    value: int, limit: Limit, expected: Severity | None
) -> None:
    assert limit_severity(value, limit) is expected


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (Limit(warning=250, error=400), "warning above 250, error above 400"),
        (Limit(error=50), "error above 50"),
        (Limit(warning=5), "warning above 5"),
    ],
)
def test_limit_text_names_each_threshold(limit: Limit, expected: str) -> None:
    assert limit_text(limit) == expected


def test_check_blob_allows_exactly_the_limit() -> None:
    assert check_blob("a.bin", 100, 100) is None
    found = check_blob("a.bin", 101, 100, origin="history blob 0123: ")
    assert found is not None
    assert (found.rule, found.message, found.location) == (
        "files.blob-size",
        "history blob 0123: blob is 101 bytes; the limit is 100 bytes",
        Location("a.bin"),
    )


@pytest.mark.parametrize(
    "category",
    [Category.GENERATED, Category.VENDOR, Category.ASSETS, Category.DOCS, Category.SOURCE],
)
def test_blob_limit_applies_to_every_category(category: Category) -> None:
    exact = candidate("big.dat", b"x" * BLOB, category)
    over = CandidateFile(exact.record, BLOB + 1, None)
    assert findings(exact) == []
    assert findings(over) == [
        (
            "files.blob-size",
            f"blob is {BLOB + 1} bytes; the limit is {BLOB} bytes",
            Severity.ERROR,
            None,
        )
    ]


@pytest.mark.parametrize(
    "category", [Category.SOURCE, Category.TESTS, Category.DOCS, Category.CONFIG]
)
def test_text_categories_must_be_utf8(category: Category) -> None:
    assert findings(candidate("a.txt", b"caf\xe9\n", category)) == [
        ("files.encoding", "file is not valid UTF-8", Severity.ERROR, None)
    ]


@pytest.mark.parametrize("category", [Category.ASSETS, Category.GENERATED, Category.VENDOR])
def test_other_categories_may_hold_binary_data(category: Category) -> None:
    assert findings(candidate("a.bin", b"\xff\x00\xfe", category)) == []


def test_links_and_unread_blobs_are_only_size_checked() -> None:
    assert findings(candidate("src/app/link.py", None)) == []


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (250, []),
        (
            251,
            [("files.source-lines", "251 lines (warning above 250, error above 400)", "warning")],
        ),
        (
            400,
            [("files.source-lines", "400 lines (warning above 250, error above 400)", "warning")],
        ),
        (401, [("files.source-lines", "401 lines (warning above 250, error above 400)", "error")]),
    ],
)
@pytest.mark.parametrize("category", [Category.SOURCE, Category.TESTS])
def test_source_and_test_files_have_line_limits(
    category: Category, lines: int, expected: list[tuple[str, str, str]]
) -> None:
    file = candidate("src/app/long.py", "x = 1\n" * lines, category)
    assert [(rule, message, str(severity)) for rule, message, severity, _ in findings(file)] == (
        expected
    )
    assert all(component == "app" for *_, component in findings(file))


@pytest.mark.parametrize("category", [Category.DOCS, Category.CONFIG, Category.GENERATED])
def test_line_limits_skip_other_categories(category: Category) -> None:
    assert findings(candidate("long.txt", "x\n" * 401, category)) == []


def test_unterminated_last_line_counts_toward_the_limit() -> None:
    file = candidate("src/app/long.py", "x = 1\n" * 250 + "y = 2")
    assert [message for _, message, _, _ in findings(file)] == [
        "251 lines (warning above 250, error above 400)"
    ]
