import pytest

from quality.gitio import Repository, parse_version, version_findings
from quality.process import Completed
from quality.testkit.gitrepo import GitRepo


def printing(monkeypatch: pytest.MonkeyPatch, output: bytes) -> None:
    """Make every Git call print `output`, as `git --version` would."""

    def git(self: Repository, *args: str) -> Completed:
        return Completed(("git", *args), 0, output, b"", 0.0)

    monkeypatch.setattr(Repository, "git", git)


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("git version 2.34.1\n", (2, 34, 1)),
        ("git version 2.45.2.windows.1", (2, 45, 2)),
        ("  git version 3.0\n", (3, 0, 0)),
        ("git version 2.39.3 (Apple Git-146)", (2, 39, 3)),
    ],
)
def test_versions_are_parsed(text: str, version: tuple[int, int, int]) -> None:
    assert parse_version(text) == version


@pytest.mark.parametrize("text", ["", "git version", "git version two", "hub version 2.34.1"])
def test_unrecognized_output_is_none(text: str) -> None:
    assert parse_version(text) is None


def test_the_installed_git_is_supported(repo: GitRepo) -> None:
    shown, found = version_findings(repo.repository())
    assert found == []
    assert parse_version(f"git version {shown}") is not None


@pytest.mark.parametrize(
    ("output", "shown"),
    [(b"git version 2.34.0\n", "2.34.0"), (b"git version 10.1\n", "10.1.0")],
)
def test_the_minimum_and_newer_pass(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch, output: bytes, shown: str
) -> None:
    printing(monkeypatch, output)
    assert version_findings(repo.repository()) == (shown, [])


@pytest.mark.parametrize("output", [b"git version 2.33.8\n", b"git version 1.99\n"])
def test_an_older_git_is_reported(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    printing(monkeypatch, output)
    shown, found = version_findings(repo.repository())
    (problem,) = found
    assert problem.rule == "git.version"
    assert problem.message == f"Git {shown} is older than 2.34"


def test_unrecognized_output_is_reported(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    printing(monkeypatch, b"  not git at all\n")
    shown, found = version_findings(repo.repository())
    assert shown == "not git at all"
    assert [(d.rule, d.message) for d in found] == [
        ("git.version", "unrecognized output of 'git --version': 'not git at all'")
    ]
