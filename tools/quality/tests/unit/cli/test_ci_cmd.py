import json
from pathlib import Path
from typing import Any

import pytest

from quality.cli.main import main
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml

CI = '[ci]\nworkflow = ".github/workflows/quality.yml"\nrequired_jobs = ["gate", "audit"]\n'


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(extra=CI))
    repo.commit_all("policy")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def needs(tmp_path: Path, **results: str) -> Path:
    path = tmp_path / "needs.json"
    path.write_text(json.dumps({job: {"result": result} for job, result in results.items()}))
    return path


def verdict(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = main(["ci", "verdict", *args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def rules(report: dict[str, Any]) -> list[str]:
    return [diagnostic["rule"] for diagnostic in report["diagnostics"]]


@pytest.mark.usefixtures("project")
def test_every_required_job_succeeding_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["ci", "verdict", "--needs", str(needs(tmp_path, gate="success", audit="success"))])
    assert code == 0
    assert "PASSED" in capsys.readouterr().out


@pytest.mark.usefixtures("project")
def test_a_cancelled_required_job_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = needs(tmp_path, gate="success", audit="cancelled")
    code, report = verdict(capsys, "--needs", str(path))
    assert (code, rules(report), report["command"]) == (1, ["ci.job-failed"], "ci verdict")


def test_the_policy_comes_from_the_named_revision(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = project.head()
    project.write("quality.toml", policy_toml(extra=CI.replace(', "audit"', "")))
    project.commit_all("only the gate")
    path = needs(tmp_path, gate="success", audit="success")
    assert verdict(capsys, "--needs", str(path))[0] == 1
    assert verdict(capsys, "--needs", str(path), "--rev", first)[0] == 0


@pytest.mark.parametrize(
    ("policy", "message"),
    [(None, "has no quality.toml"), (policy_toml(), "has no [ci] table")],
)
def test_a_revision_without_ci_policy_is_a_usage_error(
    project: GitRepo,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    policy: str | None,
    message: str,
) -> None:
    if policy is None:
        project.git("rm", "-q", "quality.toml")
    else:
        project.write("quality.toml", policy)
    project.commit_all("change the policy")
    code, report = verdict(capsys, "--needs", str(needs(tmp_path, gate="success")))
    assert (code, rules(report)) == (2, ["ci.results"])
    assert message in report["diagnostics"][0]["message"]


def test_an_invalid_policy_reports_its_diagnostics(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("quality.toml", "schema_version = 1\n")
    project.commit_all("break the policy")
    code, report = verdict(capsys, "--needs", str(needs(tmp_path, gate="success")))
    assert code == 2
    assert "ci.results" not in rules(report)


@pytest.mark.usefixtures("project")
@pytest.mark.parametrize("content", [None, "[1]"])
def test_unusable_results_are_usage_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    path = tmp_path / "needs.json"
    if content is not None:
        path.write_text(content)
    code, report = verdict(capsys, "--needs", str(path))
    assert (code, rules(report)) == (2, ["ci.results"])


@pytest.mark.usefixtures("project")
def test_an_unknown_revision_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, report = verdict(capsys, "--needs", str(needs(tmp_path)), "--rev", "missing")
    assert (code, rules(report)) == (3, ["git.revision-unresolvable"])
