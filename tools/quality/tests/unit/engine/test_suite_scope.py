from collections.abc import Callable
from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.engine import SuiteReport, SuiteSettings, run_suites
from quality.engine.scope import added_files, changed_paths
from quality.gitio import FileChange, staged
from quality.policy import Profile
from quality.testing import Outcome, Suite
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.reports import FileSpec
from quality.testkit.suites import Script, ScriptedRunner

RULES = {rule.id: rule for rule in build_registry()}
COVERED = FileSpec.first(2, 2, (1, 1))
SPECS = (
    ComponentSpec(id="a", root="a", source=("src",), unit=("tests",)),
    ComponentSpec(id="b", root="b", source=("src",), unit=("tests",)),
)
FULL = {
    ("a", Suite.UNIT): Script(files={"src/a.py": COVERED}),
    ("b", Suite.UNIT): Script(files={"src/b.py": COVERED}),
}


def layout(repo: GitRepo, *specs: ComponentSpec) -> None:
    repo.write("quality.toml", policy_toml(*(specs or SPECS)))
    repo.write("README.md", "# Project\n")
    for name in ("a", "b"):
        repo.write(f"{name}/src/{name}.py", "def f():\n    return 1\n")
        repo.write(f"{name}/tests/test_{name}.py", "def test_f():\n    assert True\n")


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    layout(repo)
    repo.commit_all("base")
    return repo


def run(
    repo: GitRepo,
    tests: ScriptedRunner,
    *,
    everything: bool = False,
    suites: tuple[Suite, ...] = (Suite.UNIT,),
) -> SuiteReport:
    repo.git("add", "-A")
    git = repo.repository()
    settings = SuiteSettings({Profile.PYTHON: tests}, RULES, everything=everything, suites=suites)
    return run_suites(git, staged(git), settings)


def rules_of(report: SuiteReport) -> list[tuple[str, str | None]]:
    return [(diagnostic.rule, diagnostic.component) for diagnostic in report.diagnostics]


def test_a_covered_component_cannot_cover_for_its_sibling(project: GitRepo) -> None:
    # b's run reports a's file as covered and omits its own.
    scripts = {**FULL, ("b", Suite.UNIT): Script(files={"../a/src/a.py": COVERED})}
    tests = ScriptedRunner(scripts)
    report = run(project, tests, everything=True)
    assert report.tested == ("a", "b")
    assert rules_of(report) == [
        ("coverage.unaccounted", "b"),
        ("coverage.not-applicable", "b"),
        ("coverage.not-applicable", "b"),
    ]
    assert [summary.component for summary in report.coverage] == ["a", "b"]


def test_integration_coverage_never_satisfies_unit_floors(project: GitRepo) -> None:
    specs = (ComponentSpec(id="a", root="a", unit=("tests",), integration=("it",)), SPECS[1])
    layout(project, *specs)
    project.write("a/it/test_flow.py", "def test_flow():\n    assert True\n")
    scripts = {
        ("a", Suite.UNIT): Script(files={"src/a.py": FileSpec.first(10, 1, (1, 1))}),
        ("a", Suite.INTEGRATION): Script(files={"src/a.py": FileSpec.first(10, 10, (1, 1))}),
    }
    tests = ScriptedRunner(scripts)
    report = run(project, tests, suites=(Suite.UNIT, Suite.INTEGRATION))
    assert ("coverage.line", "a") in rules_of(report)
    assert [(r.component.id, r.suite) for r in tests.requests if r.component.id == "a"] == [
        ("a", Suite.UNIT),
        ("a", Suite.INTEGRATION),
    ]
    assert tests.parsed == [("a", Suite.UNIT)]
    assert tests.requests[1].roots == ("it",)


def test_integration_failures_are_reported_and_optional_suites_may_be_absent(
    project: GitRepo,
) -> None:
    specs = (ComponentSpec(id="a", root="a", unit=("tests",), integration=("it",)), SPECS[1])
    layout(project, *specs)
    project.write("a/it/test_flow.py", "def test_flow():\n    assert False\n")
    scripts = {**FULL, ("a", Suite.INTEGRATION): Script((("test_flow", Outcome.FAILED),))}
    tests = ScriptedRunner(scripts)
    report = run(project, tests, everything=True, suites=(Suite.UNIT, Suite.INTEGRATION))
    assert rules_of(report) == [("tests.failed", "a")]
    assert [(r.component.id, r.suite) for r in tests.requests] == [
        ("a", Suite.UNIT),
        ("a", Suite.INTEGRATION),
        ("b", Suite.UNIT),
    ]


def test_uncovered_changed_lines_fail_although_totals_pass(project: GitRepo) -> None:
    body = "".join(f"x{n} = {n}\n" for n in range(1, 21))
    project.write("a/src/a.py", body)
    project.commit_all("twenty lines")
    project.write(
        "a/src/a.py", body.replace("x19 = 19", "x19 = 190").replace("x20 = 20", "x20 = 200")
    )
    tests = ScriptedRunner(
        {("a", Suite.UNIT): Script(files={"src/a.py": FileSpec.first(20, 18, (1, 1))})}
    )
    report = run(project, tests)
    assert rules_of(report) == [("coverage.changed-lines", "a")]
    assert report.diagnostics[0].message.endswith("uncovered: a/src/a.py:19, a/src/a.py:20")
    assert [(s.line.covered, s.line.total) for s in report.coverage] == [(18, 20)]


def test_a_new_uncovered_file_fails(project: GitRepo) -> None:
    project.write("a/src/extra.py", "y = 1\n")
    files = {"src/a.py": FileSpec.first(20, 20, (1, 1)), "src/extra.py": FileSpec.first(1, 0)}
    report = run(project, ScriptedRunner({("a", Suite.UNIT): Script(files=files)}))
    assert ("coverage.new-file", "a") in rules_of(report)
    assert ("coverage.line", "a") not in rules_of(report)


type Edit = Callable[[GitRepo], object]


def write(path: str, content: str) -> Edit:
    def apply(repo: GitRepo) -> object:
        return repo.write(path, content)

    return apply


def delete(path: str) -> Edit:
    def apply(repo: GitRepo) -> object:
        return (repo.path / path).unlink()

    return apply


def move(old: str, new: str) -> Edit:
    def apply(repo: GitRepo) -> object:
        return repo.git("mv", old, new)

    return apply


@pytest.mark.parametrize(
    ("edit", "tested"),
    [
        (write("README.md", "# Project, again\n"), ()),
        (write("a/src/a.py", "def f():\n    return 2\n"), ("a",)),
        (delete("b/tests/test_b.py"), ("b",)),
        (write("b/pyproject.toml", "[project]\nname = 'b'\n"), ("b",)),
        (write("uv.lock", "version = 1\n"), ("a", "b")),
        (write("quality.toml", policy_toml(*SPECS) + "\n"), ("a", "b")),
        (move("a/src/a.py", "b/src/moved.py"), ("a", "b")),
    ],
)
def test_only_affected_components_are_tested(
    project: GitRepo, edit: Edit, tested: tuple[str, ...]
) -> None:
    edit(project)
    tests = ScriptedRunner(FULL)
    report = run(project, tests)
    assert report.tested == tested
    assert sorted({r.component.id for r in tests.requests}) == list(tested)


def test_reverse_dependencies_are_tested(project: GitRepo) -> None:
    dependent = ComponentSpec(id="b", root="b", extra='depends_on = ["a"]')
    project.write("quality.toml", policy_toml(SPECS[0], dependent))
    project.commit_all("b depends on a")
    project.write("a/src/a.py", "def f():\n    return 3\n")
    assert run(project, ScriptedRunner(FULL)).tested == ("a", "b")


def test_everything_tests_every_active_component_without_changes(project: GitRepo) -> None:
    report = run(project, ScriptedRunner(FULL), everything=True)
    assert (report.tested, report.diagnostics) == (("a", "b"), ())


def test_an_invalid_policy_stops_before_any_suite(project: GitRepo) -> None:
    project.write("quality.toml", "schema_version = 2\n")
    tests = ScriptedRunner(FULL)
    report = run(project, tests, everything=True)
    assert report.tested == ()
    assert all(rule.startswith("policy.") for rule, _ in rules_of(report))
    assert tests.requests == []


def change(
    status: str,
    path: str,
    *,
    old_path: str | None = None,
    old_mode: str = "100644",
    new_mode: str = "100644",
) -> FileChange:
    return FileChange(status, path, old_path, old_mode, new_mode, "0" * 40, "1" * 40)


def test_changed_paths_include_both_sides_of_a_rename() -> None:
    changes = [change("M", "a/x.py"), change("R", "b/y.py", old_path="a/y.py")]
    assert changed_paths(changes) == ["a/x.py", "b/y.py", "a/y.py"]


def test_added_files_are_new_files_and_replaced_non_files() -> None:
    changes = [
        change("A", "new.py"),
        change("M", "edited.py"),
        change("T", "was-link.py", old_mode="120000"),
        change("T", "now-link.py", new_mode="120000"),
        change("D", "gone.py", new_mode="000000"),
        change("R", "renamed.py", old_path="old.py"),
    ]
    assert added_files(changes) == frozenset({"new.py", "was-link.py"})
