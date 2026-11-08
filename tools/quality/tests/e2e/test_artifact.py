"""The template as a user receives it: a project exported from a fresh clone.

The candidate's files are copied without installed environments or packages
into a new repository, the template clone. Its `./quality template export`
writes the project, which the tests drive through the root launcher symlink;
the launcher prepares the pinned core from the shared caches.
"""

import shutil
from pathlib import Path

import pytest

from quality.template import BEGIN, END
from quality.testkit.gitrepo import GitRepo
from quality.testkit.scratch import Project, output

TEMPLATE = Path(__file__).resolve().parents[4]
INSTALLED = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "node_modules", ".dart_tool", "vendor"
)
# What only the template's own repository holds (decision 0016).
LEFT_OUT = ("PLAN.md", "docs/plan", "tools/quality/tests/acceptance")
MAINTAINER_POLICY = ('mode = "maintainer"', "[template]", "acceptance = ")
REWRITTEN = (
    "quality.toml",
    "tools/quality/scenarios.toml",
    "README.md",
    "AGENTS.md",
    "docs/README.md",
    "tools/quality/README.md",
)


@pytest.fixture(scope="module")
def clone(tmp_path_factory: pytest.TempPathFactory) -> Project:
    repo = GitRepo.create(tmp_path_factory.mktemp("clone") / "template")
    shutil.copytree(TEMPLATE, repo.path, symlinks=True, ignore=INSTALLED, dirs_exist_ok=True)
    repo.commit_all("use the template")
    return Project(repo)


@pytest.fixture(scope="module")
def template(clone: Project) -> Project:
    target = clone.path.parent / "project"
    exported = clone.quality("template", "export", str(target))
    assert exported.returncode == 0, output(exported)
    return Project(GitRepo(target, clone.repo.home))


def test_the_exported_template_starts_and_answers(template: Project) -> None:
    version = template.quality("--version")
    assert version.returncode == 0, output(version)
    assert version.stdout_text().startswith("quality ")
    explained = template.quality("explain", "tests.failed")
    assert explained.returncode == 0, output(explained)
    assert explained.stdout_text().startswith("tests.failed: Tests failed")


def test_the_exported_template_validates_its_policy(template: Project) -> None:
    validated = template.quality("policy", "validate")
    assert validated.returncode == 0, output(validated)


def test_the_exported_template_lists_its_scenarios(template: Project) -> None:
    listed = template.quality("scenarios")
    assert listed.returncode == 0, output(listed)
    assert "quality-core" in listed.stdout_text()


def test_a_usage_error_exits_with_status_2(template: Project) -> None:
    unknown = template.quality("explain", "no.such-rule")
    assert unknown.returncode == 2, output(unknown)


def test_the_export_leaves_out_the_maintainer_content(template: Project) -> None:
    for path in LEFT_OUT:
        assert not (template.path / path).exists(), path
    policy = (template.path / "quality.toml").read_text(encoding="utf-8")
    for text in MAINTAINER_POLICY:
        assert text not in policy
    scenarios = (template.path / "tools/quality/scenarios.toml").read_text(encoding="utf-8")
    assert 'level = "acceptance"' not in scenarios
    for path in REWRITTEN:
        text = (template.path / path).read_text(encoding="utf-8")
        assert BEGIN not in text, path
        assert END not in text, path


def test_the_export_is_a_repository_that_names_its_template(
    clone: Project, template: Project
) -> None:
    root = template.repo.git("log", "--format=%P|%s|%(trailers:key=Quality-Template)")
    assert root == f"|Start from the quality template|Quality-Template: {clone.head()}\n\n"
    assert template.repo.git("status", "--porcelain") == ""
