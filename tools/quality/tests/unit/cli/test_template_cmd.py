from pathlib import Path

import pytest

from quality.cli.main import main
from quality.template import BEGIN, END
from quality.testkit.gitrepo import IDENTITY, GitRepo
from quality.testkit.policies import policy_toml

TEMPLATE = f'# {BEGIN}\n[template]\nexclude = ["PLAN.md"]\nfenced = ["quality.toml"]\n# {END}\n'


@pytest.fixture
def project(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo.write("quality.toml", policy_toml(extra=TEMPLATE))
    repo.write("PLAN.md", "# Plan\n")
    repo.write("src/app/__init__.py", "")
    repo.commit_all("base")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in IDENTITY.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def test_export_writes_the_template_and_names_the_next_steps(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "copy"
    assert main(["template", "export", str(target)]) == 0
    root = GitRepo(target, project.home).head()
    assert capsys.readouterr().out.splitlines() == [
        f"exported 2 files of HEAD to {target}; left out 1, rewrote 1",
        f"committed them as {root} on main",
        f"next: cd {target}, ./quality bootstrap, then add your components",
    ]
    assert sorted(p.name for p in target.iterdir()) == [".git", "quality.toml", "src"]


def test_an_older_commit_can_be_exported(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("src/app/later.py", "")
    project.commit_all("later")
    assert main(["template", "export", "--rev", "HEAD~1", str(tmp_path / "copy")]) == 0
    assert "exported 2 files of HEAD~1" in capsys.readouterr().out
    assert not (tmp_path / "copy" / "src" / "app" / "later.py").exists()


def test_an_invalid_template_exits_with_status_2(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file").write_text("")
    assert main(["template", "export", str(tmp_path / "taken")]) == 2
    assert "(template.invalid)" in capsys.readouterr().out


def test_an_unknown_revision_is_reported(
    project: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["template", "export", "--rev", "no-such-rev", str(tmp_path / "copy")]) != 0
    assert "no-such-rev" in capsys.readouterr().out
    assert not (tmp_path / "copy").exists()


def test_a_missing_identity_exits_with_status_3(
    project: GitRepo,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in IDENTITY:
        monkeypatch.delenv(name)
    assert main(["template", "export", str(tmp_path / "copy")]) == 3
    assert "git commit failed" in capsys.readouterr().out
    assert not (tmp_path / "copy").exists()
