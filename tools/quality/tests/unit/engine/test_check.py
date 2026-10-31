from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.python.config import CONFIG
from quality.adapters.python.ruff import FORMAT
from quality.adapters.python.ruff import RULES as FORMAT_RULES
from quality.catalog import build_registry
from quality.cli.main import command_table
from quality.diagnostics import Diagnostic, Kind, Severity
from quality.engine import Report, Settings, Tools, check_candidate
from quality.engine.check import UNAVAILABLE
from quality.engine.profiles import ProfileAdapter
from quality.gitio import revision, staged
from quality.policy import Profile
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.suites import ScriptedRunner

TODAY = date(2026, 10, 20)
README = "# Project\n\nSee [agent instructions](AGENTS.md).\n"
AGENTS = "# Agents\n\nRun `quality check` before committing.\n"


def scripted() -> ScriptedRunner:
    return ScriptedRunner({}, cover=True)


def settings(tools: Tools | None = None, adapter: ProfileAdapter | None = None) -> Settings:
    return Settings(
        tools=tools or pinned_tools(),
        commands=command_table(),
        rules={rule.id: rule for rule in build_registry()},
        today=TODAY,
        profiles={Profile.PYTHON: adapter or scripted()},
    )


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", README)
    repo.write("AGENTS.md", AGENTS)
    repo.write("src/app/__init__.py", "")
    repo.write("src/app/core.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
    repo.write("tests/test_core.py", "def test_add():\n    assert 1 + 1 == 2\n")
    repo.git("add", "-A")
    return repo


def check(repo: GitRepo, tools: Tools | None = None) -> Report:
    git = repo.repository()
    return check_candidate(git, staged(git), settings(tools))


def rules_of(report: Report) -> list[str]:
    return [diagnostic.rule for diagnostic in report.diagnostics]


def only(report: Report, rule: str) -> Diagnostic:
    found = [diagnostic for diagnostic in report.diagnostics if diagnostic.rule == rule]
    assert len(found) == 1, report.diagnostics
    return found[0]


def test_a_clean_project_passes(project: GitRepo) -> None:
    report = check(project)
    assert (report.diagnostics, report.files, report.excepted) == ((), 6, 0)
    assert report.tested == ("app",)
    (summary,) = report.coverage
    assert (summary.component, summary.line.covered, summary.line.total) == ("app", 4, 4)


def test_a_revision_is_checked(project: GitRepo) -> None:
    project.git("commit", "-q", "-m", "first")
    project.write("src/app/core.py", "x = (\n")
    git = project.repository()
    report = check_candidate(git, revision(git, "HEAD"), settings())
    assert report.diagnostics == ()


def test_findings_from_every_gate_are_reported(project: GitRepo) -> None:
    body = "".join(f"    if a == {n}:\n        return {n}\n" for n in range(11))
    project.write("src/app/branches.py", f"def pick(a: int) -> int:\n{body}    return -1\n")
    project.write("src/app/broken.py", "def broken(:\n")
    project.write("docs/guide.md", "# Guide\n\n\n\nSee [missing](missing.md).\n")
    project.write("settings.json", '{"a": 1, "a": 2}\n')
    token = "ghp_" + "8x9QeK2mVb7TzLwR4nJp6sYd3cHf1gAa0uXo"
    project.write("notes.txt", f"token = {token}\n")
    project.write("data/big.bin", b"\0" * 1_048_577)
    project.git("add", "-A")
    report = check(project)
    assert sorted(set(rules_of(report))) == [
        "config.syntax",
        "discovery.unclassified",
        "docs.broken-link",
        "docs.unindexed",
        "files.blob-size",
        "markdown.lint",
        "secrets.detected",
        "structure.complexity",
        "structure.unparseable",
    ]
    assert token not in repr(report)
    assert only(report, "structure.complexity").data == {"symbol": "pick"}


def test_registered_exceptions_remove_findings(project: GitRepo) -> None:
    exception = """
[[exceptions]]
rule = "suppressions.inline"
path = "src/app/core.py"
symbol = "noqa: E501 - long URL"
reason = "documented"
owner = "team"
expires = 2026-12-31
"""
    project.write("quality.toml", policy_toml(extra=exception))
    project.write("src/app/core.py", "X = 1  # noqa: E501 - long URL\n")
    project.git("add", "-A")
    report = check(project)
    assert report.diagnostics == ()
    assert report.excepted == 1


def test_exceptions_for_rules_that_did_not_run_are_not_unused(project: GitRepo) -> None:
    exception = """
[[exceptions]]
rule = "structure.complexity"
path = "web/src/a.ts"
reason = "pending"
owner = "team"
expires = 2026-12-31
"""
    spec = ComponentSpec(id="web", root="web", profile="typescript", source=("src",))
    project.write("quality.toml", policy_toml(spec, extra=exception))
    project.write("web/package.json", '{"name": "web", "private": true}\n')
    project.write("web/src/a.ts", "export const a = 1;\n")
    project.git("rm", "-q", "-r", "--cached", "src", "tests")
    project.git("add", "-A", "quality.toml", "web")
    report = check(project)
    unavailable = only(report, "profiles.unavailable")
    assert unavailable.kind is Kind.ENVIRONMENT
    assert unavailable.message == (
        "'web' uses the typescript profile; this build has no adapter for it"
    )
    assert "exceptions.unused" not in rules_of(report)


def test_policy_problems_stop_the_check(project: GitRepo) -> None:
    project.git("rm", "-q", "--cached", "quality.toml")
    assert rules_of(check(project)) == ["policy.missing"]
    unknown = """
[[exceptions]]
rule = "no.such-rule"
path = "a.py"
reason = "r"
owner = "o"
expires = 2026-12-31
"""
    project.write("quality.toml", policy_toml(extra=unknown))
    project.git("add", "quality.toml")
    assert rules_of(check(project)) == ["policy.reference"]


def test_tool_failures_are_environment_errors(project: GitRepo, tmp_path: Path) -> None:
    failing = tmp_path / "failing"
    failing.write_text("#!/bin/sh\nexit 2\n")
    failing.chmod(0o755)
    tools = pinned_tools()
    report = check(project, Tools(failing, failing, tools.gitleaks_rules, tools.actionlint))
    assert rules_of(report) == ["tools.failed", "tools.failed"]
    assert {diagnostic.kind for diagnostic in report.diagnostics} == {Kind.ENVIRONMENT}


def test_declared_architecture_is_enforced(project: GitRepo) -> None:
    architecture = """
[components.architecture]
layers = ["api", "core"]
modules = [
    { name = "api", paths = ["src/app/api.py"] },
    { name = "core", paths = ["src/app/core.py"] },
]
"""
    project.write("quality.toml", policy_toml(ComponentSpec(extra=architecture)))
    project.write("src/app/api.py", "from app import core\n")
    project.write("src/app/core.py", "from app import api\n")
    project.git("add", "-A")
    report = check(project)
    assert rules_of(report) == ["architecture.forbidden-dependency", "architecture.cycle"]
    upward = only(report, "architecture.forbidden-dependency")
    assert upward.location is not None
    assert (upward.location.path, upward.location.line) == ("src/app/core.py", 1)


def test_links_and_oversized_files_are_not_read(project: GitRepo) -> None:
    (project.path / "link.md").symlink_to("README.md")
    project.write("src/app/big.py", "#" * 1_048_577)
    project.git("add", "-A")
    report = check(project)
    assert rules_of(report) == ["files.blob-size"]


def test_agent_instructions_are_required(project: GitRepo) -> None:
    project.git("rm", "-q", "--cached", "README.md", "AGENTS.md")
    report = check(project)
    assert rules_of(report) == ["docs.agents-missing"]


def test_static_findings_cover_the_component_files(project: GitRepo) -> None:
    finding = FORMAT.diagnostic("would reformat", component="app")
    adapter = ScriptedRunner({}, cover=True, findings={"app": [finding]})
    git = project.repository()
    report = check_candidate(git, staged(git), settings(adapter=adapter))
    assert report.diagnostics == (finding,)
    assert adapter.checked == [
        ("app", ("src/app/__init__.py", "src/app/core.py", "tests/test_core.py"))
    ]


def test_configuration_errors_stop_the_component(project: GitRepo) -> None:
    exception = """
[[exceptions]]
rule = "python.format"
path = "src/app/core.py"
reason = "pending"
owner = "team"
expires = 2026-12-31
"""
    project.write("quality.toml", policy_toml(extra=exception))
    project.git("add", "-A")
    problem = CONFIG.diagnostic("pyproject.toml has no [project] table", component="app")
    adapter = ScriptedRunner({}, rules=FORMAT_RULES, problems={"app": [problem]})
    git = project.repository()
    report = check_candidate(git, staged(git), settings(adapter=adapter))
    assert report.diagnostics == (problem,)
    assert (adapter.checked, adapter.requests, report.tested) == ([], [], ())


def test_warnings_do_not_stop_the_component(project: GitRepo) -> None:
    warning = CONFIG.diagnostic("w", component="app", severity=Severity.WARNING)
    adapter = ScriptedRunner({}, cover=True, problems={"app": [warning]})
    git = project.repository()
    report = check_candidate(git, staged(git), settings(adapter=adapter))
    assert report.diagnostics == (warning,)
    assert report.tested == ("app",)


def test_an_environment_that_cannot_be_prepared_fails(project: GitRepo) -> None:
    broken = native.failure("uv sync", "no network")
    adapter = ScriptedRunner({}, broken={"app": broken.diagnostic})
    git = project.repository()
    report = check_candidate(git, staged(git), settings(adapter=adapter))
    assert rules_of(report) == ["tools.failed"]
    assert report.diagnostics[0].message == "uv sync: no network"


def test_only_affected_components_are_prepared_unless_all(project: GitRepo) -> None:
    specs = (ComponentSpec(id="a", root="a"), ComponentSpec(id="b", root="b"))
    project.write("quality.toml", policy_toml(*specs))
    for name in ("a", "b"):
        project.write(f"{name}/src/{name}/__init__.py", "")
        project.write(f"{name}/tests/test_{name}.py", "def test_ok():\n    assert True\n")
    project.git("rm", "-q", "-r", "--cached", "src", "tests")
    project.git("add", "-A", "quality.toml", "a", "b")
    project.git("commit", "-q", "-m", "first")
    project.write("a/src/a/__init__.py", "X = 1\n")
    project.git("add", "-A", "a")
    git = project.repository()
    adapter = scripted()
    assert check_candidate(git, staged(git), settings(adapter=adapter)).tested == ("a",)
    assert adapter.prepared == ["a"]
    every = replace(settings(adapter=scripted()), everything=True)
    assert check_candidate(git, staged(git), every).tested == ("a", "b")


def test_without_profile_adapters_every_component_is_unavailable(project: GitRepo) -> None:
    bare = Settings(
        tools=pinned_tools(),
        commands=command_table(),
        rules={rule.id: rule for rule in build_registry()},
        today=TODAY,
    )
    git = project.repository()
    report = check_candidate(git, staged(git), bare)
    found = only(report, UNAVAILABLE.id)
    message = "'app' uses the python profile; this build has no adapter for it"
    assert (found.component, found.message) == ("app", message)
    assert report.tested == ()
