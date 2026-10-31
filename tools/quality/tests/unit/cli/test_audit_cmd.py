import json
from pathlib import Path
from typing import Any

import pytest

from quality.cli.main import main
from quality.testkit.gitrepo import GitRepo
from quality.testkit.osvdb import Advisory, advisory_db, uv_lock
from quality.testkit.policies import policy_toml
from quality.toolchain import cache_root


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    cache = cache_root().parent.parent
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("uv.lock", uv_lock(jinja2="2.10.1"))
    repo.commit_all("base")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.chdir(repo.path)
    return repo


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return advisory_db(tmp_path / "db", Advisory("TEST-1", "jinja2", "2.10.1"))


def audit(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = main(["audit", *args, "--json"])
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.usefixtures("project")
def test_a_clean_index_passes_with_a_summary(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["audit", "--offline-db", str(database)]) == 0
    output = capsys.readouterr().out
    assert "PASSED" in output
    assert "audited lockfiles: uv.lock; 0 covered by exceptions" in output


def test_a_vulnerable_revision_fails(
    project: GitRepo, database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = project.head()
    project.write("uv.lock", uv_lock(jinja2="2.10"))
    project.commit_all("downgrade")
    code, report = audit(capsys, "--rev", "HEAD", "--offline-db", str(database))
    assert (code, report["command"], report["lockfiles"], report["excepted"]) == (
        1,
        "audit",
        ["uv.lock"],
        0,
    )
    assert [d["rule"] for d in report["diagnostics"]] == ["dependencies.vulnerable"]
    assert audit(capsys, "--rev", clean, "--offline-db", str(database))[0] == 0


def test_no_lockfiles_report_none(
    project: GitRepo, database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project.git("rm", "-q", "uv.lock")
    assert main(["audit", "--offline-db", str(database)]) == 0
    assert "audited lockfiles: none;" in capsys.readouterr().out


@pytest.mark.usefixtures("project")
def test_an_unknown_revision_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    code, report = audit(capsys, "--rev", "missing")
    assert (code, [d["rule"] for d in report["diagnostics"]]) == (3, ["git.revision-unresolvable"])


@pytest.mark.usefixtures("project")
def test_a_scanner_that_is_not_installed_is_an_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))
    code, report = audit(capsys)
    assert code == 3
    assert "osv-scanner" in report["diagnostics"][0]["message"]
