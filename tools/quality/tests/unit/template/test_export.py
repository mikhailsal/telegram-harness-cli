from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.gitio import GitioError
from quality.template import BEGIN, END, ExportError, export_tree
from quality.template.export import STAGING_PREFIX, Exported
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml

RULES = build_registry().ids()
TEMPLATE = f"""\
# {BEGIN}
[template]
exclude = ["PLAN.md", "docs/plan/**"]
fenced = ["quality.toml", "README.md"]
# {END}
"""
README = (
    f"# App\n\n- [Guide](docs/guide.md)\n\n<!-- {BEGIN} -->\n\n"
    f"- [Plan](PLAN.md)\n\n<!-- {END} -->\n"
)
MODE = 'mode = "maintainer"'

type Found = list[tuple[str, str | None, int | None]]


def maintainer_policy(template: str = TEMPLATE) -> str:
    policy = policy_toml(mode="maintainer", extra=template)
    return policy.replace(MODE, f"# {BEGIN}\n{MODE}\n# {END}")


@pytest.fixture
def maintainer(repo: GitRepo) -> GitRepo:
    repo.write("quality.toml", maintainer_policy())
    repo.write("README.md", README)
    repo.write("PLAN.md", "# Plan\n")
    repo.write("docs/plan/P01.md", "# P01\n")
    repo.write("docs/guide.md", "# Guide\n")
    repo.write("src/app/__init__.py", "")
    repo.write("bin/run", "#!/bin/sh\n", executable=True)
    (repo.path / "run").symlink_to("bin/run")
    repo.commit_all("maintainer tree")
    return repo


def export(repo: GitRepo, target: Path, **environ: str) -> Exported:
    repository = repo.repository()
    commit = repository.resolve_commit("HEAD")
    return export_tree(repository, commit, target, RULES, repo.environ(**environ))


def exported_repo(target: Path, maintainer: GitRepo) -> GitRepo:
    return GitRepo(target, maintainer.home)


def failure(repo: GitRepo, target: Path) -> Found:
    with pytest.raises(ExportError) as raised:
        export(repo, target)
    assert not target.exists() or not any(target.iterdir())
    assert not list(target.parent.glob(f"{STAGING_PREFIX}*"))
    found: Found = []
    for diagnostic in raised.value.diagnostics:
        location = diagnostic.location
        path, line = (location.path, location.line) if location else (None, None)
        found.append((diagnostic.message, path, line))
    return found


def files(root: Path) -> list[str]:
    paths = (p.relative_to(root) for p in root.rglob("*") if not p.is_dir())
    return sorted(path.as_posix() for path in paths if path.parts[0] != ".git")


def test_the_committed_tree_is_exported_without_maintainer_content(
    maintainer: GitRepo, tmp_path: Path
) -> None:
    maintainer.write("README.md", "unstaged change\n")
    maintainer.write("untracked.md", "# Untracked\n")
    target = tmp_path / "project"
    exported = export(maintainer, target)
    copy = exported_repo(target, maintainer)
    fenced = ("quality.toml", "README.md")
    assert exported == Exported(target, 6, 2, fenced, "main", copy.head())
    assert files(target) == [
        "README.md",
        "bin/run",
        "docs/guide.md",
        "quality.toml",
        "run",
        "src/app/__init__.py",
    ]
    assert (target / "README.md").read_text() == "# App\n\n- [Guide](docs/guide.md)\n"
    policy = (target / "quality.toml").read_text()
    assert "mode =" not in policy
    assert "[template]" not in policy
    assert (target / "bin/run").stat().st_mode & 0o777 == 0o755
    assert (target / "docs/guide.md").stat().st_mode & 0o777 == 0o644
    assert (target / "run").readlink() == Path("bin/run")
    assert not list(tmp_path.glob(f"{STAGING_PREFIX}*"))


def test_the_export_is_a_repository_whose_root_commit_names_the_template(
    maintainer: GitRepo, tmp_path: Path
) -> None:
    maintainer.write(".gitignore", "build/\n")
    maintainer.write("build/kept.txt", "kept\n")
    maintainer.git("add", "--force", "build/kept.txt")
    source = maintainer.commit_all("track an ignored file")
    target = tmp_path / "project"
    exported = export(maintainer, target, GIT_AUTHOR_NAME="Template User")
    copy = exported_repo(target, maintainer)
    assert copy.git("log", "--format=%H %P|%an|%B").splitlines() == [
        f"{exported.commit} |Template User|Start from the quality template",
        "",
        f"Quality-Template: {source}",
        "",
    ]
    assert copy.git("symbolic-ref", "--short", "HEAD") == "main\n"
    assert copy.git("status", "--porcelain", "--ignored") == ""
    assert sorted(copy.git("ls-files").split()) == files(target)
    assert "build/kept.txt" in files(target)


def test_the_root_commit_is_made_on_the_policy_s_default_branch(
    maintainer: GitRepo, tmp_path: Path
) -> None:
    policy = maintainer_policy().replace(
        "[repository]\n", '[repository]\ndefault_branch = "trunk"\n'
    )
    maintainer.write("quality.toml", policy)
    maintainer.commit_all("rename the default branch")
    exported = export(maintainer, tmp_path / "project")
    assert exported.branch == "trunk"
    copy = exported_repo(tmp_path / "project", maintainer)
    assert copy.git("symbolic-ref", "--short", "HEAD") == "trunk\n"


def test_without_an_identity_nothing_is_exported(maintainer: GitRepo, tmp_path: Path) -> None:
    target = tmp_path / "project"
    with pytest.raises(GitioError, match="git commit failed"):
        export(maintainer, target, GIT_AUTHOR_NAME="", GIT_COMMITTER_NAME="")
    assert not target.exists()
    assert not list(tmp_path.glob(f"{STAGING_PREFIX}*"))


def test_an_empty_directory_is_a_valid_target(maintainer: GitRepo, tmp_path: Path) -> None:
    target = tmp_path / "empty"
    target.mkdir()
    assert export(maintainer, target).files == 6
    assert (target / "quality.toml").is_file()


def test_an_unusable_target_is_refused(maintainer: GitRepo, tmp_path: Path) -> None:
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "file").write_text("mine\n")
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (tmp_path / "elsewhere").mkdir()
    for target, message in (
        (taken, f"'{taken}' exists and is not an empty directory"),
        (linked, f"'{linked}' exists and is not an empty directory"),
        (tmp_path / "missing" / "project", f"the parent of '{tmp_path / 'missing' / 'project'}'"),
    ):
        with pytest.raises(ExportError) as raised:
            export(maintainer, target)
        assert raised.value.diagnostics[0].message.startswith(message)
        assert raised.value.diagnostics[0].rule == "template.invalid"
    assert (taken / "file").read_text() == "mine\n"


def test_a_submodule_cannot_be_exported(maintainer: GitRepo, tmp_path: Path) -> None:
    maintainer.git("update-index", "--add", "--cacheinfo", f"160000,{maintainer.head()},vendored")
    maintainer.git("commit", "-q", "-m", "add a submodule")
    message = "a submodule cannot be exported; its files are not in this tree"
    assert failure(maintainer, tmp_path / "project") == [(message, "vendored", None)]


def test_a_fenced_file_must_be_exported(maintainer: GitRepo, tmp_path: Path) -> None:
    template = TEMPLATE.replace('"README.md"]', '"README.md", "PLAN.md"]')
    maintainer.write("quality.toml", maintainer_policy(template))
    maintainer.commit_all("fence an excluded file")
    message = "is declared in [template].fenced but is not an exported file"
    assert failure(maintainer, tmp_path / "project") == [(message, "PLAN.md", None)]


def test_a_broken_fence_names_its_line(maintainer: GitRepo, tmp_path: Path) -> None:
    maintainer.write("README.md", f"# App\n\n<!-- {END} -->\n")
    maintainer.commit_all("break the fence")
    found = failure(maintainer, tmp_path / "project")
    assert found == [("closes a fence that is not open", "README.md", 3)]


def test_a_fenced_file_must_be_text(maintainer: GitRepo, tmp_path: Path) -> None:
    maintainer.write("README.md", b"\xff\xfe binary")
    maintainer.commit_all("binary readme")
    found = failure(maintainer, tmp_path / "project")
    assert found == [("a fenced file must be UTF-8 text", "README.md", None)]


def test_a_link_to_a_left_out_path_is_refused(maintainer: GitRepo, tmp_path: Path) -> None:
    (maintainer.path / "plan").symlink_to("PLAN.md")
    maintainer.commit_all("link the plan")
    found = failure(maintainer, tmp_path / "project")
    assert found == [("links to a path the export leaves out", "plan", None)]


def test_an_invalid_source_policy_stops_the_export(maintainer: GitRepo, tmp_path: Path) -> None:
    maintainer.write("quality.toml", "schema_version = 1\n")
    maintainer.commit_all("break the policy")
    found = failure(maintainer, tmp_path / "project")
    assert found
    assert {path for _, path, _ in found} == {"quality.toml"}


def test_a_source_policy_that_fails_cross_validation_stops_the_export(
    maintainer: GitRepo, tmp_path: Path
) -> None:
    exception = (
        '[[exceptions]]\nrule = "no.such-rule"\npath = "src/app/__init__.py"\n'
        'reason = "r"\nowner = "o"\nreview = 2999-01-01\n'
    )
    maintainer.write("quality.toml", maintainer_policy() + exception)
    maintainer.commit_all("except an unknown rule")
    found = failure(maintainer, tmp_path / "project")
    assert len(found) == 1
    assert "no.such-rule" in found[0][0]


def test_an_exported_policy_that_does_not_validate_stops_the_export(
    maintainer: GitRepo, tmp_path: Path
) -> None:
    policy = maintainer_policy().replace("[coverage]", f"# {BEGIN}\n[coverage]", 1)
    policy = policy.replace(
        "new_file_must_be_covered = true\n", f"new_file_must_be_covered = true\n# {END}\n"
    )
    maintainer.write("quality.toml", policy)
    maintainer.commit_all("fence a required table")
    found = failure(maintainer, tmp_path / "project")
    assert found
    assert {path for _, path, _ in found} == {"quality.toml"}
