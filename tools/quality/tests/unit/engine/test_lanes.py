from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.ci import Lane
from quality.cli.main import command_table
from quality.diagnostics import Severity
from quality.engine import Report, Settings, check_candidate
from quality.engine.common import COMMON_RULES, MEASURED_RULES
from quality.engine.ran import ran_rules
from quality.gitio import staged
from quality.policy import Profile
from quality.testing import Suite
from quality.testkit.browserscenarios import manifest
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, browser_spec, build_policy, policy_toml
from quality.testkit.suites import FULL, Script, ScriptedRunner

API = ComponentSpec(id="api", root="api")
WEB = replace(browser_spec(), e2e=("e2e",), scenarios="scenarios.toml")
FILES = {
    "README.md": "# Project\n\nSee [agent instructions](AGENTS.md).\n",
    "AGENTS.md": "# Agents\n\nRun `quality gate` in CI.\n",
    "api/src/api/__init__.py": "",
    # An inline suppression, which only the common gates report.
    "api/src/api/core.py": "x = 1  # noqa\n",
    "api/tests/test_core.py": "def test_ok():\n    pass\n",
    "web/scenarios.toml": manifest("web"),
    "web/src/main.ts": "export const one = 1;\nexport const two = 2;\n",
    "web/tests/main.test.ts": 'import { one } from "../src/main";\n',
    "web/e2e/counter.spec.ts": 'import { test } from "@playwright/test";\n',
}


def settings(lane: Lane | None) -> Settings:
    web = {("web", Suite.UNIT): Script(files={"src/main.ts": FULL})}
    return Settings(
        tools=pinned_tools(),
        commands=command_table(),
        rules={rule.id: rule for rule in build_registry()},
        today=date(2026, 10, 22),
        profiles={
            Profile.PYTHON: ScriptedRunner({}, cover=True),
            Profile.TYPESCRIPT: ScriptedRunner(web),
        },
        everything=True,
        lane=lane,
    )


def project(tmp_path: Path, *components: ComponentSpec) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(*components))
    for path, text in FILES.items():
        if path.split("/")[0] in {spec.root for spec in components} | {"README.md", "AGENTS.md"}:
            repo.write(path, text)
    repo.git("add", "-A")
    return repo


def check(repo: GitRepo, lane: Lane | None) -> Report:
    git = repo.repository()
    return check_candidate(git, staged(git), settings(lane))


def summary(report: Report) -> tuple[tuple[str, ...], list[str], list[str]]:
    """Return the tested components, the rules of the problems found, and the lane's note."""
    rules = sorted({d.rule for d in report.diagnostics if d.severity is not Severity.INFO})
    notes = [d.message for d in report.diagnostics if d.rule == "gate.lane"]
    return report.tested, rules, notes


@pytest.fixture
def mixed(tmp_path: Path) -> GitRepo:
    return project(tmp_path, API, WEB)


def test_the_complete_gate_checks_every_component_once(mixed: GitRepo) -> None:
    assert summary(check(mixed, None)) == (("api", "web"), ["suppressions.inline"], [])


def test_the_main_lane_leaves_the_browser_components_to_the_browser_lane(mixed: GitRepo) -> None:
    assert summary(check(mixed, Lane.MAIN)) == (
        ("api",),
        ["suppressions.inline"],
        ["lane main: the browser lane checks web"],
    )


def test_the_browser_lane_checks_only_the_browser_components(mixed: GitRepo) -> None:
    report = check(mixed, Lane.BROWSER)
    assert summary(report) == (
        ("web",),
        [],
        ["lane browser: checks web; the main lane checks everything else"],
    )
    assert report.files == 4
    assert [summary.component for summary in report.coverage] == ["web"]


def test_without_a_browser_component_the_browser_lane_has_nothing_to_check(
    tmp_path: Path,
) -> None:
    repo = project(tmp_path, API)
    assert summary(check(repo, Lane.BROWSER)) == (
        (),
        [],
        ["lane browser: no active component runs in a browser, so nothing to check"],
    )
    assert summary(check(repo, Lane.MAIN))[2] == ["lane main: the browser lane checks no component"]


def test_exceptions_are_judged_only_where_their_rule_ran() -> None:
    policy = build_policy(API, WEB, ComponentSpec(id="tools", root="tools"))
    api, web, tools = policy.components
    profiles = settings(None).profiles
    common, measured = COMMON_RULES[0].id, MEASURED_RULES[0].id
    browser = ran_rules((api, web, tools), (web,), profiles, common=False)
    assert not browser.covers(common, "api/src/api/core.py")
    assert not browser.covers(measured, "api/src/api/core.py")
    assert not browser.covers(measured, "scripts/build.py")
    assert browser.covers(measured, "web/src/main.ts")
    main = ran_rules((api, web, tools), (api, tools), profiles)
    assert main.covers(common, "web/src/main.ts")
    assert not main.covers(measured, "web/src/main.ts")
    assert main.covers(measured, "api/src/api/core.py")
