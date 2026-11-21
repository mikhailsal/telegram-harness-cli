import json
from pathlib import Path
from typing import Any

import pytest

from quality.cli import ci_cmd
from quality.cli.main import main
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, browser_spec, policy_toml

CI = '[ci]\nworkflow = ".github/workflows/quality.yml"\nrequired_jobs = ["gate", "audit"]\n'
NODE = ComponentSpec(id="node", root="node", profile="typescript")
PHP = ComponentSpec(id="site", root="site", profile="php")
HELPER = ComponentSpec(id="php-helper", root="helper", profile="php", role="helper")


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


@pytest.mark.parametrize(
    ("case", "profiles"),
    [
        ("typescript", "typescript\n"),
        ("php", "php\ntypescript\n"),
        ("helper change", "php\ntypescript\n"),
        ("browser lane", "typescript\n"),
        ("maintainer", "php\ntypescript\n"),
    ],
)
def test_profiles_use_the_gate_candidate_and_lane(
    project: GitRepo, capsys: pytest.CaptureFixture[str], case: str, profiles: str
) -> None:
    components = (
        (NODE, browser_spec(), HELPER, PHP)
        if case in {"php", "browser lane"}
        else (NODE, browser_spec(), HELPER)
    )
    mode = "maintainer" if case == "maintainer" else "project"
    project.write("quality.toml", policy_toml(*components, mode=mode, extra=CI))
    project.write("helper/src/entry.php", "<?php\n")
    base = project.commit_all("exported project")
    path = "helper/src/entry.php" if case == "helper change" else "node/src/index.ts"
    project.write(path, "/* A candidate change */\n")
    project.commit_all("candidate change")
    args = ["ci", "prepare", "--stage", "merge", "--base", base, "--head", "HEAD", "--profiles"]
    if case == "browser lane":
        args += ["--lane", "browser"]
    assert main(args) == 0
    assert capsys.readouterr().out == profiles


def test_a_bad_prepare_range_fails_without_a_profile_list(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ci", "prepare", "--stage", "merge", "--head", "HEAD", "--profiles"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "gate.arguments" in err


def test_prepare_provisions_the_selected_lane(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("quality.toml", policy_toml(NODE, browser_spec(), PHP, extra=CI))
    project.commit_all("project")
    monkeypatch.setattr(ci_cmd, "local_setups", dict)
    assert main(["ci", "prepare", "--stage", "commit", "--lane", "browser"]) == 3
    out = capsys.readouterr().out
    assert "'web' uses the typescript profile" in out
    assert "'node'" not in out
    assert "'site'" not in out
