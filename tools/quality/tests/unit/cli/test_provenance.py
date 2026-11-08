import argparse
import ast
import json
import sys
from pathlib import Path

import pytest

from quality.cli.main import main
from quality.cli.provenance import RUNNING_CORE, judge
from quality.engine.provenance import BOOT
from quality.testkit.gitrepo import GitRepo

MAIN = """\
import sys


def main() -> int:
    print("candidate core", sys.argv[1:])
    return 7
"""


def arguments(*, rev: str | None = None, as_json: bool = False) -> argparse.Namespace:
    return argparse.Namespace(command="check", rev=rev, json=as_json)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return a repository whose committed core prints its arguments and exits with 7."""
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("tools/quality/src/quality/__init__.py", "")
    repo.write("tools/quality/src/quality/cli/__init__.py", "")
    repo.write("tools/quality/src/quality/cli/main.py", MAIN)
    repo.write("tools/quality/uv.lock", (RUNNING_CORE / "uv.lock").read_bytes())
    repo.commit_all("core")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def test_unresolvable_candidates_are_left_to_the_handler(project: GitRepo) -> None:
    assert judge(arguments(rev="no-such-revision"), ["check"]) is None


def test_candidates_without_a_core_are_judged_by_this_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = GitRepo.create(tmp_path / "plain")
    repo.write("README.md", "# Plain\n")
    repo.commit_all("plain")
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    assert judge(arguments(rev="HEAD"), ["check"]) is None


def test_the_candidate_core_judges_itself(project: GitRepo) -> None:
    assert judge(arguments(), ["check"], project.path / "tools" / "quality") is None


def test_a_lock_that_differs_is_an_environment_error(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "running"
    root.mkdir()
    (root / "uv.lock").write_text("another lock\n")
    assert judge(arguments(), ["check"], root) == 3
    assert "(core.environment)" in capsys.readouterr().out
    assert judge(arguments(as_json=True), ["check", "--json"], root) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "check"
    assert [d["rule"] for d in report["diagnostics"]] == ["core.environment"]


def test_a_core_in_a_snapshot_never_hands_the_command_on(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "quality-snapshot-x1" / "candidate"
    root.mkdir(parents=True)
    (root / "uv.lock").write_bytes((RUNNING_CORE / "uv.lock").read_bytes())
    assert judge(arguments(), ["check"], root) == 3
    out = capsys.readouterr().out
    assert f"index {project.path / '.git' / 'index'} changed while its own core" in out
    assert "(git.candidate-changed)" in out


def test_unstaged_core_edits_hand_the_command_to_the_candidate_core(
    project: GitRepo, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project.path / "tools" / "quality"
    (root / "src/quality/cli/main.py").write_text("def main() -> int:\n    return 0\n")
    assert judge(arguments(), ["check", "--staged"], root) == 7
    assert capfd.readouterr().out == "candidate core ['check', '--staged']\n"


@pytest.mark.parametrize("argv", [["check", "--json"], ["test"], ["self-test", "--rev", "HEAD"]])
def test_verdict_commands_run_with_the_candidate_core(
    project: GitRepo, argv: list[str], capfd: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 7
    assert capfd.readouterr().out == f"candidate core {argv}\n"


def test_the_command_line_is_handed_on_unchanged(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["quality", "check", "--all"])
    assert main() == 7
    assert capfd.readouterr().out == "candidate core ['check', '--all']\n"


def test_the_launcher_starts_cores_the_same_way() -> None:
    source = (RUNNING_CORE / "launcher" / "src" / "quality_launcher.py").read_text()
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["BOOT"]) == BOOT
