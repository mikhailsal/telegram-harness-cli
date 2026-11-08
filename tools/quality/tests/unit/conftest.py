from pathlib import Path

import pytest

from quality.adapters import native
from quality.testkit.gitrepo import GitRepo
from quality.testkit.suites import direct


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    return GitRepo.create(tmp_path / "repo")


@pytest.fixture(autouse=True)
def direct_suites(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start suites without the network namespace stub, which has tests of its own."""
    monkeypatch.setattr(native, "contained", direct())
