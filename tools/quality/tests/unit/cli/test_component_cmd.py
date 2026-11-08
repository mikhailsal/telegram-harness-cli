from pathlib import Path

import pytest

from quality.cli import component_cmd
from quality.cli.main import main
from quality.policy import load_policy
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml

POLICY = policy_toml(ComponentSpec(root="app"))


@pytest.fixture
def project(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo.write("quality.toml", POLICY)
    repo.write("app/pyproject.toml", '[project]\nname = "app"\n')
    repo.write("app/src/app/__init__.py", "")
    repo.write("app/tests/test_app.py", "")
    repo.commit_all("base")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def native(repo: GitRepo, root: str, manifest: str, *directories: str) -> None:
    repo.write(f"{root}/{manifest}", "{}\n" if manifest.endswith(".json") else "name: x\n")
    for directory in directories:
        repo.write(f"{root}/{directory}/.keep", "")


def added(repo: GitRepo) -> list[tuple[str, str, str, list[str], list[str]]]:
    policy = load_policy(repo.path).policy
    assert policy is not None
    return [
        (c.id, c.profile, c.root, c.source, c.tests.unit)
        for c in policy.components
        if c.id != "app"
    ]


@pytest.mark.parametrize(
    ("manifest", "directories", "expected"),
    [
        ("pyproject.toml", ("src", "tests"), ("python", ["src"], ["tests"])),
        ("package.json", ("src", "tests"), ("typescript", ["src"], ["tests"])),
        ("pubspec.yaml", ("lib", "test"), ("dart", ["lib"], ["test"])),
        ("pubspec.yaml", ("lib", "bin", "test"), ("dart", ["lib", "bin"], ["test"])),
        ("composer.json", ("src", "tests"), ("php", ["src"], ["tests"])),
    ],
)
def test_the_profile_and_directories_follow_from_the_native_project(
    project: GitRepo,
    capsys: pytest.CaptureFixture[str],
    manifest: str,
    directories: tuple[str, ...],
    expected: tuple[str, list[str], list[str]],
) -> None:
    native(project, "services/web", manifest, *directories)
    assert main(["component", "add", "services/web", "--capability", "cli"]) == 0
    profile, source, unit = expected
    assert added(project) == [("web", profile, "services/web", source, unit)]
    assert (project.path / "services/web/.python-version").exists() == (profile == "python")
    out = capsys.readouterr().out
    assert out.startswith('added to quality.toml:\n\n[[components]]\nid = "web"\n')
    assert out.endswith(
        "next: stage the component and quality.toml, run ./quality bootstrap, then commit\n"
    )
    assert project.git("diff", "--cached", "--name-only").strip() == ""


def test_options_name_what_the_conventions_cannot(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project.write("services/tool/cli/main.py", "")
    project.write("services/tool/checks/check_main.py", "")
    monkeypatch.chdir(project.path / "services")
    arguments = ["tool", "--id", "cli-tool", "--profile", "python", "--source", "cli"]
    reason = ["--unit", "checks", "--no-boundary-reason", "prints only"]
    assert main(["component", "add", *arguments, *reason]) == 0
    assert added(project) == [("cli-tool", "python", "services/tool", ["cli"], ["checks"])]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["../outside"], "'../outside' is not a directory of this repository"),
        (["quality.toml"], "'quality.toml' is not a directory of this repository"),
        (["empty"], "cannot tell the profile from the manifests"),
        (["both"], "cannot tell the profile from the manifests"),
        (["bare"], "none of src exists in the component"),
        (["web", "--unit", "tests"], "none of src exists in the component"),
        (["listed"], "declare --capability, or explain with --no-boundary-reason"),
    ],
)
def test_arguments_that_cannot_describe_a_component_exit_with_status_2(
    project: GitRepo, capsys: pytest.CaptureFixture[str], arguments: list[str], message: str
) -> None:
    (project.path.parent / "outside").mkdir(exist_ok=True)
    (project.path / "empty").mkdir()
    native(project, "both", "package.json", "src", "tests")
    native(project, "both", "composer.json")
    native(project, "bare", "composer.json")
    native(project, "web", "package.json")
    native(project, "listed", "package.json", "src", "tests")
    reason = [] if arguments == ["listed"] else ["--no-boundary-reason", "pure"]
    assert main(["component", "add", *arguments, *reason]) == 2
    assert capsys.readouterr().err.startswith(f"component add: {message}")
    assert (project.path / "quality.toml").read_text() == POLICY


def test_a_repository_without_a_policy_is_refused(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    (project.path / "quality.toml").unlink()
    assert main(["component", "add", "app", "--capability", "cli"]) == 2
    assert "there is no quality.toml at the repository root" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "finding"),
    [
        (["web", "--capability", "teleport"], "(policy.invalid)"),
        (["web", "--id", "app", "--capability", "cli"], "duplicate component id 'app'"),
        (["app", "--id", "again", "--capability", "cli"], "several components share the root"),
    ],
)
def test_a_component_the_policy_rejects_is_not_written(
    project: GitRepo, capsys: pytest.CaptureFixture[str], arguments: list[str], finding: str
) -> None:
    native(project, "web", "package.json", "src", "tests")
    assert main(["component", "add", *arguments]) == 2
    assert finding in capsys.readouterr().out
    assert (project.path / "quality.toml").read_text() == POLICY


def test_outside_a_repository_the_git_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    (tmp_path / "loose").mkdir()
    monkeypatch.chdir(tmp_path / "loose")
    assert main(["component", "add", ".", "--capability", "cli"]) == 3
    assert "(git." in capsys.readouterr().out


def test_python_registration_supplies_the_core_pin_without_staging(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = project.write("core-python", "3.12.7\n")
    monkeypatch.setattr(component_cmd, "CORE_PYTHON", pin)
    native(project, "pricing", "pyproject.toml", "src", "tests")
    assert main(["component", "add", "pricing", "--capability", "cli"]) == 0
    assert (project.path / "pricing/.python-version").read_text() == "3.12.7\n"
    assert project.git("diff", "--cached", "--name-only").strip() == ""


def test_python_registration_preserves_an_existing_pin(project: GitRepo) -> None:
    native(project, "pricing", "pyproject.toml", "src", "tests")
    pin = project.write("pricing/.python-version", "3.13.9\n")
    assert main(["component", "add", "pricing", "--capability", "cli"]) == 0
    assert pin.read_bytes() == b"3.13.9\n"


def test_rejected_registration_does_not_create_a_python_pin(project: GitRepo) -> None:
    native(project, "pricing", "pyproject.toml", "src", "tests")
    assert main(["component", "add", "pricing", "--id", "app", "--capability", "cli"]) == 2
    assert not (project.path / "pricing/.python-version").exists()
    assert (project.path / "quality.toml").read_text() == POLICY


def test_an_unreadable_core_pin_leaves_the_policy_unchanged(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(component_cmd, "CORE_PYTHON", project.path / "missing-pin")
    native(project, "pricing", "pyproject.toml", "src", "tests")
    assert main(["component", "add", "pricing", "--capability", "cli"]) == 2
    assert "cannot create" in capsys.readouterr().err
    assert not (project.path / "pricing/.python-version").exists()
    assert (project.path / "quality.toml").read_text() == POLICY


def test_python_registration_preserves_a_dangling_pin_symlink(project: GitRepo) -> None:
    native(project, "pricing", "pyproject.toml", "src", "tests")
    outside = project.path.parent / "outside-python-version"
    pin = project.path / "pricing/.python-version"
    pin.symlink_to(outside)
    assert not outside.exists()
    assert main(["component", "add", "pricing", "--capability", "cli"]) == 0
    assert pin.is_symlink()
    assert pin.readlink() == outside
    assert not outside.exists()
