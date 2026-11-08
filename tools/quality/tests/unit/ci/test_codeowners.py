import pytest

from quality.ci.codeowners import (
    owners_of,
    parse_codeowners,
    pattern_regex,
    shadowing,
    unowned,
)

PATH = ".github/CODEOWNERS"


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("*", "a/b/c.py", True),
        ("*.js", "web/app.js", True),
        ("*.js", "web/app.ts", False),
        ("quality.toml", "quality.toml", True),
        ("quality.toml", "nested/quality.toml", True),
        ("/quality.toml", "nested/quality.toml", False),
        ("docs/*", "docs/a.md", True),
        ("docs/*", "docs/a/b.md", False),
        ("docs/", "docs/a/b.md", True),
        ("docs/", "src/docs/a.md", True),
        ("/docs/", "src/docs/a.md", False),
        ("docs", "docs/a/b.md", True),
        ("docs/**", "docs/a/b.md", True),
        ("**/logs", "deep/x/logs/a.txt", True),
        ("**/logs", "logs", True),
        ("tools/**/ci", "tools/a/b/ci/run.sh", True),
        ("tools/**/ci", "tools/ci", True),
        ("src/?.py", "src/a.py", True),
        ("src/?.py", "src/ab.py", False),
        ("a.b", "aXb", False),
    ],
)
def test_patterns_match_as_github_documents(pattern: str, path: str, *, matches: bool) -> None:
    assert (pattern_regex(pattern).fullmatch(path) is not None) is matches


def test_the_last_matching_entry_decides_the_owners() -> None:
    text = "* @org/everyone\n/tools/ @lead dev@example.com\n/tools/scratch/\n"
    entries, problems = parse_codeowners(PATH, text)
    assert problems == []
    assert owners_of(entries, "README.md") == ("@org/everyone",)
    assert owners_of(entries, "tools/a.py") == ("@lead", "dev@example.com")
    assert owners_of(entries, "tools/scratch/x") == ()


def test_comments_blank_lines_and_escaped_hashes_are_read_as_github_does() -> None:
    text = "# owners\n\n\\#notes.md @alice # the notes\n  \nREADME.md @bob\n"
    entries, problems = parse_codeowners(PATH, text)
    assert problems == []
    assert [(entry.line, entry.pattern, entry.owners) for entry in entries] == [
        (3, "\\#notes.md", ("@alice",)),
        (5, "README.md", ("@bob",)),
    ]
    assert owners_of(entries, "#notes.md") == ("@alice",)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("!docs/ @alice", "pattern '!docs/' uses syntax GitHub does not support"),
        ("src/[ab].py @alice", "pattern 'src/[ab].py' uses syntax GitHub does not support"),
        ("docs/ alice", "'alice' is not a user, team, or email address"),
        ("docs/ @-alice", "'@-alice' is not a user, team, or email address"),
    ],
)
def test_lines_github_would_ignore_are_reported(line: str, message: str) -> None:
    entries, problems = parse_codeowners(PATH, f"* @owner\n{line}\n")
    assert len(entries) == 1
    assert [(d.rule, d.message, d.data["symbol"]) for d in problems] == [
        ("ci.codeowners", message, line)
    ]
    assert problems[0].location is not None
    assert problems[0].location.line == 2


def test_unowned_control_files_are_reported_at_the_file() -> None:
    entries, _ = parse_codeowners(PATH, "/quality.toml @owner\n")
    found = list(unowned(PATH, entries, ["quality.toml", ".github/CODEOWNERS"]))
    assert [(d.message, d.data["symbol"]) for d in found] == [
        ("control file '.github/CODEOWNERS' has no owner in .github/CODEOWNERS", PATH)
    ]
    assert found[0].location is not None
    assert found[0].location.path == ".github/CODEOWNERS"


def test_the_configured_file_must_exist() -> None:
    assert [d.message for d in shadowing(PATH, ["README.md"])] == [
        "the CODEOWNERS file '.github/CODEOWNERS' does not exist"
    ]


def test_github_reads_codeowners_only_from_three_places() -> None:
    assert [d.message for d in shadowing("owners/CODEOWNERS", ["owners/CODEOWNERS"])] == [
        "GitHub does not read CODEOWNERS from 'owners/CODEOWNERS'"
    ]


def test_an_earlier_location_shadows_the_configured_file() -> None:
    present = [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]
    assert [d.message for d in shadowing("docs/CODEOWNERS", present)] == [
        "GitHub reads '.github/CODEOWNERS' before the configured 'docs/CODEOWNERS'",
        "GitHub reads 'CODEOWNERS' before the configured 'docs/CODEOWNERS'",
    ]
    assert list(shadowing(".github/CODEOWNERS", present)) == []
    assert list(shadowing("CODEOWNERS", ["CODEOWNERS", "docs/CODEOWNERS"])) == []
