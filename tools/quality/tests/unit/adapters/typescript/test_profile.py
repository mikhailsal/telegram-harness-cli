import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.environment import Environments
from quality.adapters.typescript.profile import RULES, TypeScriptProfile
from quality.diagnostics import Diagnostic
from quality.metrics import Measurements
from quality.testing import StaticRequest, Suite, SuiteRequest
from quality.testkit.nodefakes import (
    PINS,
    FakeNode,
    install_tools,
    script,
    typescript_component,
    write_component,
)

APP = typescript_component()
FILES = ("app/src/a.ts", "app/src/legacy.js", "app/tests/a.test.ts")
CONFIGS = ("app/eslint.config.js",)
RECORD = 'echo "$@" >> "$(dirname "$0")/calls"\n'
CLEAN = {
    "prettier": RECORD + "exit 0\n",
    "eslint": RECORD + "echo '[]'\n",
    "vitest": (
        'echo "<testsuites/>" > "$(dirname "$3")/junit.xml"\n'
        """echo '{"inverted": [], "retried": [], "unhandled": 0}' """
        '> "$(dirname "$3")/annotations.json"\n'
    ),
}


@dataclass(frozen=True, slots=True)
class Setup:
    profile: TypeScriptProfile
    fake: FakeNode
    snapshot: Path
    work: Path

    def static(
        self, files: tuple[str, ...] = FILES, configs: tuple[str, ...] = CONFIGS
    ) -> StaticRequest:
        return StaticRequest(APP, self.snapshot, self.work, files, configs)

    def calls(self, name: str) -> list[str]:
        path = self.snapshot / "app" / "node_modules" / name / "calls"
        return path.read_text().splitlines() if path.exists() else []


def helper_script(tmp_path: Path, directory: Path) -> Path:
    """Answer each helper command with a canned response about `src/a.ts` and its test."""
    files = [str(directory / "src/a.ts"), str(directory / "tests/a.test.ts")]
    typed = {"configured": True, "ignored": False, "typed": True, "parser": "typescript-eslint"}
    answers: dict[str, dict[str, object]] = {
        "eslint-config": {"files": [{**typed, "file": name} for name in files]},
        "types": {
            "projects": [
                {"config": str(directory / "tsconfig.json"), "files": files, "problems": []}
            ],
            "diagnostics": [],
        },
        "structure": {
            "files": [
                {"file": name, "functions": [], "imports": [], "error": None} for name in files
            ]
        },
    }
    for command, answer in answers.items():
        (tmp_path / f"{command}.json").write_text(json.dumps({"protocol": 1, **answer}))
    return script(tmp_path / "main.ts", f"cat '{tmp_path}'/\"$1\".json\n")


@pytest.fixture
def setup(fake_node: FakeNode, snapshot: Path, tmp_path: Path) -> Setup:
    directory = write_component(snapshot / "app")
    install_tools(directory, CLEAN)
    environments = Environments(lambda: fake_node.node.executable, fake_node.node.cache)
    helper = helper_script(tmp_path, directory)
    profile = TypeScriptProfile(environments, PINS, helper=helper)
    work = tmp_path / "work"
    assert profile.prepare(APP, snapshot, work) == []
    return Setup(profile, fake_node, snapshot, work)


def rules(found: list[Diagnostic]) -> list[str]:
    return [d.rule for d in found]


def test_the_profile_reports_its_rules(setup: Setup) -> None:
    assert setup.profile.rules == RULES
    assert {rule.id for rule in RULES} == {
        "typescript.accessibility",
        "typescript.accessibility-scan",
        "typescript.boundaries",
        "typescript.browser-console",
        "typescript.browser-libraries",
        "typescript.browser-ready",
        "typescript.browser-resource",
        "typescript.browser-version",
        "typescript.build",
        "typescript.build-output",
        "typescript.config",
        "typescript.entries",
        "typescript.format",
        "typescript.lint",
        "typescript.lockfile",
        "typescript.playwright",
        "typescript.browser-served",
        "typescript.styles",
        "typescript.types",
    }


def test_preparing_installs_the_lock_once(setup: Setup) -> None:
    assert setup.profile.prepare(APP, setup.snapshot, setup.work) == []
    assert [Path(call[0]) for call in setup.fake.calls()] == [setup.snapshot / "app"]


def test_a_rejected_configuration_installs_nothing(
    fake_node: FakeNode, snapshot: Path, tmp_path: Path
) -> None:
    (snapshot / "app").mkdir()
    environments = Environments(lambda: fake_node.node.executable, fake_node.node.cache)
    profile = TypeScriptProfile(environments, PINS)
    found = profile.prepare(APP, snapshot, tmp_path / "work")
    assert set(rules(found)) == {"typescript.config"}
    assert fake_node.calls() == []
    with pytest.raises(ToolFailedError, match="'app' was not prepared"):
        profile.static(StaticRequest(APP, snapshot, tmp_path / "work", FILES))


def test_the_static_gates_run_on_typescript_and_configuration_files(setup: Setup) -> None:
    found = setup.profile.static(setup.static())
    assert rules(found) == ["typescript.config"]
    assert found[0].location is not None
    assert found[0].location.path == "app/src/legacy.js"
    [formatted] = setup.calls("prettier")
    assert formatted.endswith(" ".join(str(setup.snapshot / p) for p in (*FILES[::2], *CONFIGS)))
    [linted] = setup.calls("eslint")
    assert linted.endswith(" ".join(str(setup.snapshot / p) for p in FILES[::2]))


def test_a_failing_tool_does_not_stop_the_others(setup: Setup) -> None:
    eslint = setup.snapshot / "app" / "node_modules" / "eslint" / "cli.sh"
    eslint.write_text("echo 'crashed' >&2\nexit 2\n")
    found = setup.profile.static(setup.static())
    assert rules(found) == ["typescript.config", "tools.failed"]
    assert len(setup.calls("prettier")) == 1


def test_configuration_scripts_alone_are_only_formatted(setup: Setup) -> None:
    assert setup.profile.static(setup.static(files=())) == []
    assert len(setup.calls("prettier")) == 1
    assert setup.calls("eslint") == []
    assert setup.profile.static(setup.static(files=(), configs=())) == []
    assert len(setup.calls("prettier")) == 1


def test_the_helper_measures_the_typescript_files(setup: Setup) -> None:
    measured = setup.profile.measure(setup.static())
    assert [metrics.path for metrics in measured.files] == ["app/src/a.ts", "app/tests/a.test.ts"]
    assert setup.profile.measure(setup.static(files=("app/src/legacy.js",))) == Measurements(())


def test_vitest_runs_the_suite_and_lcov_is_read_from_the_component(setup: Setup) -> None:
    output = setup.work / "app-unit"
    output.mkdir()
    suite = SuiteRequest(APP, Suite.UNIT, ("tests",), setup.snapshot, output, 0.0, setup.work)
    run = setup.profile.run(suite)
    assert run.results == output / "junit.xml"
    assert setup.profile.expected("app/src/a.ts")
    assert not setup.profile.expected("app/src/a.d.ts")
    lcov = b"SF:src/a.ts\nDA:1,1\nDA:2,0\nBRDA:1,0,0,1\nBRDA:1,0,1,0\nend_of_record\n"
    report = setup.profile.coverage(lcov, suite)
    assert report.branches_measured
    assert report.files["app/src/a.ts"].covered == frozenset({1})
