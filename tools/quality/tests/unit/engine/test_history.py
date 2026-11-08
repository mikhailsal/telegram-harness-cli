from pathlib import Path

import pytest

from quality.adapters import native, secrets
from quality.diagnostics import Kind
from quality.engine import check_history
from quality.engine.history import blob_location
from quality.gitio import HistoryBlob, RevisionRange
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo

TOKEN = "ghp_" + "8x9QeK2mVb7TzLwR4nJp6sYd3cHf1gAa0uXo"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def leaky_history(repo: GitRepo) -> tuple[str, str]:
    repo.write("README.md", "# Project\n")
    base = repo.commit_all("base")
    repo.write("config/settings.txt", f"token = {TOKEN}\n")
    repo.write("assets/huge.bin", b"\0" * 2048)
    repo.commit_all("oops")
    (repo.path / "config/settings.txt").unlink()
    (repo.path / "assets/huge.bin").unlink()
    tip = repo.commit_all("clean up")
    return base, tip


def test_secrets_and_large_blobs_deleted_before_the_tip_fail(repo: GitRepo, work: Path) -> None:
    base, tip = leaky_history(repo)
    found = check_history(
        repo.repository(), RevisionRange(base, tip), work, tools=pinned_tools(), blob_limit=1024
    )
    assert [diagnostic.rule for diagnostic in found] == ["files.blob-size", "secrets.detected"]
    size, secret = found
    assert size.location is not None
    assert size.location.path == "assets/huge.bin"
    assert size.message.startswith("history blob ")
    assert size.message.endswith(": blob is 2048 bytes; the limit is 1024 bytes")
    assert secret.location is not None
    assert (secret.location.path, secret.location.line) == ("config/settings.txt", 1)
    assert secret.message.startswith("history blob ")
    assert TOKEN not in repr(found)


def test_a_clean_range_passes(repo: GitRepo, work: Path) -> None:
    _, tip = leaky_history(repo)
    repo.write("docs/more.md", "# More\n")
    newer = repo.commit_all("docs")
    span = RevisionRange(tip, newer)
    assert check_history(repo.repository(), span, work, tools=pinned_tools(), blob_limit=1024) == []


def test_scanner_failures_are_reported_with_size_findings(
    repo: GitRepo, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, tip = leaky_history(repo)

    def broken(*_: object, **__: object) -> list[secrets.Finding]:
        native.fail("gitleaks", "crashed")

    monkeypatch.setattr(secrets, "scan_directory", broken)
    found = check_history(
        repo.repository(), RevisionRange(base, tip), work, tools=pinned_tools(), blob_limit=1024
    )
    assert [diagnostic.rule for diagnostic in found] == ["files.blob-size", "tools.failed"]
    assert found[1].kind is Kind.ENVIRONMENT


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("dir/file.txt", "dir/file.txt"),
        ("../../escape/./x", "escape/x"),
        ("..", "blob"),
        ("oid", "blob"),
    ],
)
def test_blob_locations_stay_inside_the_scan_tree(path: str, expected: str) -> None:
    assert blob_location(HistoryBlob("oid", 1, path)) == f"oid/{expected}"
