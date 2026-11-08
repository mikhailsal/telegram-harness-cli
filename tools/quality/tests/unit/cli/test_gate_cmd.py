import json
from pathlib import Path
from typing import Any

import pytest

from quality.cli import check_cmd, gate_cmd
from quality.cli.main import main
from quality.engine import ProfileAdapter
from quality.policy import Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml
from quality.testkit.suites import ScriptedRunner
from quality.toolchain import cache_root

TOKEN = "ghp_" + "8x9QeK2mVb7TzLwR4nJp6sYd3cHf1gAa0uXo"
README = "# Project\n\nSee [agent instructions](AGENTS.md).\n"
AGENTS = "# Agents\n\nRun `quality gate` in CI.\n"


def scripted() -> dict[Profile, ProfileAdapter]:
    return {Profile.PYTHON: ScriptedRunner({}, cover=True)}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    cache = cache_root().parent.parent
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", README)
    repo.write("AGENTS.md", AGENTS)
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.commit_all("base")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setattr(gate_cmd, "local_profiles", scripted)
    monkeypatch.setattr(check_cmd, "local_profiles", scripted)
    monkeypatch.chdir(repo.path)
    return repo


def pull_request(repo: GitRepo) -> tuple[str, str, str, str]:
    """Return the fork point, the moved base, the head, and the tested merge."""
    fork = repo.head()
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("src/app/feature.py", "a = 1\nb = 2\n")
    head = repo.commit_all("feature")
    repo.git("checkout", "-q", "main")
    repo.write("src/app/upstream.py", "c = 3\nd = 4\ne = 5\n")
    base = repo.commit_all("main moves on")
    repo.git("merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    return fork, base, head, repo.head()


def gate(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = main(["gate", *args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def changed(report: dict[str, Any]) -> object:
    (coverage,) = report["coverage"]
    return coverage["changed"]


def rules(report: dict[str, Any]) -> list[str]:
    return [diagnostic["rule"] for diagnostic in report["diagnostics"]]


def test_a_pull_request_owns_only_its_lines(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    fork, base, head, tested = pull_request(project)
    code, report = gate(
        capsys, "--stage", "merge", "--base", base, "--head", head, "--tested", tested
    )
    assert (code, report["command"], report["diagnostics"]) == (0, "gate", [])
    assert changed(report) == {"covered": 2, "total": 2}
    tree = project.git("rev-parse", f"{tested}^{{tree}}").strip()
    assert report["candidate"] == {
        "stage": "merge",
        "lane": None,
        "base": base,
        "head": head,
        "tested": tested,
        "queue": False,
        "tree": tree,
        "base_tree": project.git("rev-parse", f"{base}^{{tree}}").strip(),
    }
    code, pushed = gate(capsys, "--stage", "commit", "--base", fork, "--head", tested)
    # The scripted runner makes the first two lines of each file executable.
    assert (code, changed(pushed)) == (0, {"covered": 4, "total": 4})
    code, queued = gate(capsys, "--stage", "merge", "--base", base, "--head", tested, "--queue")
    assert (code, changed(queued)) == (0, {"covered": 2, "total": 2})


def test_a_secret_removed_before_the_tip_fails_the_merge_gate(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    base = project.head()
    project.git("checkout", "-q", "-b", "feature")
    project.write("config/settings.txt", f"token = {TOKEN}\n")
    project.commit_all("oops")
    (project.path / "config/settings.txt").unlink()
    head = project.commit_all("clean up")
    code, report = gate(capsys, "--stage", "merge", "--base", base, "--head", head)
    assert (code, rules(report)) == (1, ["secrets.detected"])
    assert report["diagnostics"][0]["message"].startswith("history blob ")
    assert TOKEN not in json.dumps(report)
    assert main(["check", "--rev", head]) == 0


def test_a_shallow_clone_fails_rather_than_skipping_history(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, base, head, tested = pull_request(project)
    clone = project.path.parent / "clone"
    project.git("clone", "-q", "--depth", "2", f"file://{project.path}", str(clone))
    monkeypatch.chdir(clone)
    code, report = gate(
        capsys, "--stage", "merge", "--base", base, "--head", head, "--tested", tested
    )
    assert (code, rules(report)) == (3, ["git.revision-unresolvable"])
    assert "no merge base in this clone" in report["diagnostics"][0]["message"]
    code, report = gate(capsys, "--stage", "merge", "--base", base, "--head", tested, "--queue")
    assert (code, rules(report)) == (3, ["git.revision-unresolvable"])
    assert f"cut at shallow commit {head}" in report["diagnostics"][0]["message"]


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        (("--stage", "merge"), "the merge stage needs --base and --head"),
        (("--stage", "merge", "--base", "HEAD"), "--base and --head go together"),
        (("--stage", "commit", "--tested", "HEAD"), "--tested and --queue belong to the merge"),
        (("--stage", "commit", "--queue"), "--tested and --queue belong to the merge stage"),
        (("--stage", "merge", "--base", "HEAD", "--head", "HEAD", "--index", "i"), "--index"),
        (
            ("--stage", "merge", "--base", "HEAD", "--head", "HEAD", "--queue", "--tested", "HEAD"),
            "omit --tested",
        ),
        (("--stage", "commit", "--base", "HEAD", "--head", "HEAD", "--index", "i"), "exclude"),
    ],
)
def test_revisions_that_name_no_single_candidate_are_usage_errors(
    project: GitRepo,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
    problem: str,
) -> None:
    code, report = gate(capsys, *arguments)
    assert (code, rules(report), report["candidate"]["tree"]) == (2, ["gate.arguments"], None)
    assert problem in report["diagnostics"][0]["message"]


def test_the_commit_stage_checks_the_index_or_a_new_branch(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("src/app/staged.py", "x = 1  # noqa\n")
    project.git("add", "-A")
    code, report = gate(capsys, "--stage", "commit")
    assert (code, rules(report)) == (1, ["suppressions.inline"])
    index = project.path / ".git" / "index"
    project.git("reset", "-q")
    code, report = gate(capsys, "--stage", "commit", "--index", str(index))
    assert (code, rules(report)) == (0, [])
    code, report = gate(capsys, "--stage", "commit", "--base", "0" * 40, "--head", "HEAD")
    assert (code, report["candidate"]["base"]) == (0, "0" * 40)


def test_a_lane_checks_its_part_and_says_so(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("src/app/staged.py", "x = 1  # noqa\n")
    project.git("add", "-A")
    code, report = gate(capsys, "--stage", "commit", "--lane", "browser")
    assert (code, rules(report), report["tested"]) == (0, ["gate.lane"], [])
    assert report["candidate"]["lane"] == "browser"
    assert report["diagnostics"][0]["message"] == (
        "lane browser: no active component runs in a browser, so nothing to check"
    )
    code, report = gate(capsys, "--stage", "commit", "--lane", "main")
    assert (code, sorted(rules(report)), report["tested"]) == (
        1,
        ["gate.lane", "suppressions.inline"],
        ["app"],
    )


def test_an_unknown_lane_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["gate", "--stage", "commit", "--lane", "everything"])
    assert raised.value.code == 2
    assert "invalid choice: 'everything'" in capsys.readouterr().err


def test_reports_are_written_once_into_a_fresh_directory(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reports = tmp_path / "reports"
    code, report = gate(
        capsys, "--stage", "commit", "--base", "HEAD", "--head", "HEAD", "--reports", str(reports)
    )
    assert code == 0
    assert json.loads((reports / "report.json").read_text()) == report
    written = sorted(
        str(path.relative_to(reports)) for path in reports.rglob("*") if path.is_file()
    )
    assert written == [
        "coverage/app/unit.cobertura.xml",
        "coverage/app/unit.lcov",
        "junit/app/unit.xml",
        "native/app/unit/coverage.json",
        "native/app/unit/junit.xml",
        "report.json",
    ]
    before = (reports / "report.json").read_text()
    code, report = gate(capsys, "--stage", "commit", "--reports", str(reports))
    assert (code, rules(report)) == (2, ["gate.arguments"])
    assert "must be new or empty" in report["diagnostics"][0]["message"]
    assert (reports / "report.json").read_text() == before


def test_a_baseline_reports_regressions_without_failing(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.json"
    code, report = gate(capsys, "--stage", "commit", "--baseline", str(baseline))
    assert (code, rules(report)) == (0, ["coverage.baseline"])
    entry = {"component": "app", "line": {"covered": 1, "total": 1}, "branch": None}
    baseline.write_text(json.dumps({"coverage": [entry]}))
    code, report = gate(capsys, "--stage", "commit", "--baseline", str(baseline))
    assert (code, rules(report)) == (0, [])
