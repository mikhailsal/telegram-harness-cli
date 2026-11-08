"""A staged repository whose one component declares the cli capability, for engine tests.

Its manifest maps an e2e smoke test, required at the commit stage, and an
integration flow, required at the merge stage. The scripted runner passes
both unless a test gives a suite other cases.
"""

from __future__ import annotations

from pathlib import Path

from quality.testing import Outcome, Suite
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.scenarios import ScenarioSpec, manifest_toml
from quality.testkit.suites import Cases, Script, ScriptedRunner

SPEC = ComponentSpec(
    capabilities=("cli",), integration=("integration",), e2e=("e2e",), scenarios="scenarios.toml"
)
SMOKE = ScenarioSpec("smoke", ("tests::smoke",), "cli", "e2e", requirements=("cli.smoke",))
FLOWS = ScenarioSpec(
    "flows", ("tests::flow",), "cli", "integration", stage="merge", requirements=("cli.flows",)
)


def scenario_repo(path: Path) -> GitRepo:
    """Create the repository at `path` with every file staged and nothing committed."""
    repo = GitRepo.create(path)
    repo.write("quality.toml", policy_toml(SPEC))
    repo.write("scenarios.toml", manifest_toml(SMOKE, FLOWS))
    repo.write("README.md", "# Project\n\nSee [agent instructions](AGENTS.md).\n")
    repo.write("AGENTS.md", "# Agents\n\nRun `quality check` before committing.\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_core.py", "def test_ok():\n    assert True\n")
    repo.write("integration/test_flow.py", "def test_flow():\n    assert True\n")
    repo.write("e2e/test_smoke.py", "def test_smoke():\n    assert True\n")
    repo.git("add", "-A")
    return repo


def scenario_runner(**cases: Cases) -> ScriptedRunner:
    """Script the `e2e` and `integration` suites; the unit suite covers every source."""
    passed = Outcome.PASSED
    scripts = {
        ("app", Suite.E2E): Script(cases=cases.get("e2e", (("smoke", passed),))),
        ("app", Suite.INTEGRATION): Script(cases=cases.get("integration", (("flow", passed),))),
    }
    return ScriptedRunner(scripts, cover=True)
