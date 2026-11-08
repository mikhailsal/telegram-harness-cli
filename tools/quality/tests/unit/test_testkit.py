import threading
from pathlib import Path

import pytest

from quality.process import Completed
from quality.testing import CaseResult, Outcome
from quality.testing.junit import parse_junit
from quality.testkit import scratch
from quality.testkit.gitrepo import GitCommandError, GitRepo
from quality.testkit.policies import ComponentSpec, build_policy, classification_toml
from quality.testkit.reports import junit_xml
from quality.testkit.scenarios import ScenarioSpec, build_manifest, manifest_toml
from quality.testkit.scratch import (
    CORE_SOURCE,
    component,
    create_project,
    embed_core,
    output,
    rules_of,
    shared_caches,
)
from quality.testkit.slots import held


def test_failed_arrangement_commands_fail_the_test(tmp_path: Path) -> None:
    repo = GitRepo.create(tmp_path / "repo")
    with pytest.raises(GitCommandError, match="git no-such-command failed"):
        repo.git("no-such-command")


def test_repositories_use_an_isolated_home(tmp_path: Path) -> None:
    repo = GitRepo.create(tmp_path / "repo")
    assert repo.environ()["HOME"] == str(tmp_path / "repo-home")
    assert repo.run_git("config", "--global", "--list").stdout == b""


def test_build_policy_rejects_invalid_documents() -> None:
    with pytest.raises(AssertionError, match="invalid test policy"):
        build_policy(ComponentSpec(id="Not Valid"))


def test_build_policy_renders_classifications() -> None:
    policy = build_policy(extra=classification_toml("vendor/**", "vendor"))
    assert [(c.pattern, c.category.value) for c in policy.classification] == [
        ("vendor/**", "vendor")
    ]


def test_build_manifest_rejects_invalid_documents() -> None:
    with pytest.raises(AssertionError, match="invalid test manifest"):
        build_manifest(ScenarioSpec(id="Not Valid"))


def test_manifests_render_every_optional_table() -> None:
    text = manifest_toml(
        ScenarioSpec(enhanced="mutation"),
        not_applicable={"library.errors": "the fixture raises nothing"},
        retired={"old": "replaced by public-api"},
    )
    assert 'enhanced = "mutation"\n' in text
    assert '[[not_applicable]]\nrequirement = "library.errors"\n' in text
    assert '[[retired]]\nid = "old"\nreason = "replaced by public-api"\n' in text


def test_fake_junit_reports_parse_back_to_their_outcomes() -> None:
    cases = [(f"t{i}", outcome) for i, outcome in enumerate(Outcome)]
    parsed = parse_junit(junit_xml(cases), "junit.xml")
    assert parsed.cases == tuple(CaseResult(f"tests::{name}", o) for name, o in cases)


def test_scratch_projects_run_the_core_under_test(tmp_path: Path) -> None:
    project = create_project(tmp_path / "project", component("lib"))
    assert (project.path / "lib" / "uv.lock").is_file()
    assert project.repo.git("status", "--porcelain") == ""
    result = project.quality("--help")
    assert result.returncode == 0
    assert "usage: quality" in output(result)
    assert project.environ()["XDG_CACHE_HOME"]


def test_scratch_projects_can_embed_the_core(tmp_path: Path) -> None:
    project = create_project(tmp_path / "project", core=True)
    core = project.path / "tools" / "quality"
    assert (core / "src/quality/cli/main.py").read_bytes() == (
        CORE_SOURCE / "quality/cli/main.py"
    ).read_bytes()
    assert not list(core.rglob("__pycache__"))
    for name in ("uv.lock", "toolchains.toml"):
        assert (core / name).read_bytes() == (CORE_SOURCE.parent / name).read_bytes()
    assert (
        'pattern = "tools/quality/**"\ncategory = "vendor"'
        in (project.path / "quality.toml").read_text()
    )
    assert str(core / "src") in (project.path / "quality").read_text()
    helper = CORE_SOURCE.parent / "helpers/typescript"
    assert (core / "helpers/typescript/src/main.ts").read_bytes() == (
        helper / "src/main.ts"
    ).read_bytes()
    assert (core / "helpers/typescript/package.json").is_file()
    assert sorted(path.name for path in (core / "helpers/typescript").iterdir()) == [
        "package.json",
        "src",
    ]
    assert project.repo.git("status", "--porcelain") == ""
    assert "usage: quality" in output(project.quality("--help"))


def test_a_core_without_the_typescript_helper_embeds_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "core" / "src"
    (source / "quality").mkdir(parents=True)
    (source / "quality" / "__init__.py").write_text("")
    for name in ("toolchains.toml", "uv.lock"):
        (source.parent / name).write_text("")
    monkeypatch.setattr(scratch, "CORE_SOURCE", source)
    assert embed_core(tmp_path / "copy") == tmp_path / "copy" / "src"
    assert not (tmp_path / "copy" / "helpers").exists()


def test_the_dart_helper_is_embedded_without_its_tests_and_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "core" / "src"
    (source / "quality").mkdir(parents=True)
    for name in ("toolchains.toml", "uv.lock"):
        (source.parent / name).write_text("")
    helper = source.parent / "helpers" / "dart"
    for name in ("bin/main.dart", "lib/cli.dart", "test/cli_test.dart", ".dart_tool/x"):
        (helper / name).parent.mkdir(parents=True, exist_ok=True)
        (helper / name).write_text("void main() {}\n")
    for name in ("pubspec.yaml", "pubspec.lock", "analysis_options.yaml"):
        (helper / name).write_text("name: quality_dart_helper\n")
    monkeypatch.setattr(scratch, "CORE_SOURCE", source)
    embed_core(tmp_path / "copy")
    copied = tmp_path / "copy" / "helpers" / "dart"
    assert sorted(path.relative_to(copied).as_posix() for path in copied.rglob("*.*")) == [
        "bin/main.dart",
        "lib/cli.dart",
        "pubspec.lock",
        "pubspec.yaml",
    ]


def test_the_php_helper_is_embedded_without_its_tests_and_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "core" / "src"
    (source / "quality").mkdir(parents=True)
    for name in ("toolchains.toml", "uv.lock"):
        (source.parent / name).write_text("")
    helper = source.parent / "helpers" / "php"
    for name in ("bin/main.php", "src/Cli.php", "tests/CliTest.php", "vendor/autoload.php"):
        (helper / name).parent.mkdir(parents=True, exist_ok=True)
        (helper / name).write_text("<?php\n")
    for name in ("composer.json", "composer.lock"):
        (helper / name).write_text("{}\n")
    monkeypatch.setattr(scratch, "CORE_SOURCE", source)
    embed_core(tmp_path / "copy")
    copied = tmp_path / "copy" / "helpers" / "php"
    assert sorted(path.relative_to(copied).as_posix() for path in copied.rglob("*.*")) == [
        "bin/main.php",
        "composer.json",
        "composer.lock",
        "src/Cli.php",
    ]
    assert not (tmp_path / "copy" / "helpers" / "dart").exists()


def test_scratch_commits_stage_the_named_paths_or_everything(tmp_path: Path) -> None:
    project = create_project(tmp_path / "project")
    first = project.head()
    project.write("notes.txt", "one\n")
    project.write("other.txt", "two\n")
    assert project.commit("notes", "notes.txt").ok
    assert project.repo.git("status", "--porcelain") == "?? other.txt\n"
    assert project.commit("the rest").ok
    assert project.head() != first
    assert project.index_tree() == project.repo.git("rev-parse", "HEAD^{tree}").strip()
    assert project.hook("pre-commit") == project.path / ".git" / "hooks" / "pre-commit"


def test_rules_are_read_from_rendered_findings() -> None:
    text = b"a.py:1: error: bad (python.lint) [app]\nerror: low (coverage.line)\n(x) not (a rule)\n"
    result = Completed(("quality",), 1, text, b"error: env (git.failed)\n", 0.0)
    assert rules_of(result) == {"python.lint", "coverage.line", "git.failed"}


def test_scratch_users_share_the_caller_caches() -> None:
    assert shared_caches({"HOME": "/home/u"}) == {"XDG_CACHE_HOME": "/home/u/.cache"}
    environ = {"HOME": "/home/u", "XDG_CACHE_HOME": "/c", "UV_CACHE_DIR": "/uv"}
    assert shared_caches(environ) == {"XDG_CACHE_HOME": "/c", "UV_CACHE_DIR": "/uv"}


def test_slots_admit_at_most_their_count_at_once(tmp_path: Path) -> None:
    entered = threading.Event()
    slots: list[int] = []

    def third() -> None:
        with held(tmp_path / "slots", 2, poll=0.01) as index:
            slots.append(index)
            entered.set()

    with held(tmp_path / "slots", 2) as first:
        with held(tmp_path / "slots", 2) as second:
            waiting = threading.Thread(target=third)
            waiting.start()
            assert not entered.wait(0.2)
        assert entered.wait(10)
        waiting.join()
    assert [first, second, *slots] == [0, 1, 1]
