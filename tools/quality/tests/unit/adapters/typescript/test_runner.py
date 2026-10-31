import json
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.runner import (
    coverage_settings,
    expected,
    read_annotations,
    reporter,
    run_vitest,
    user_config,
    wrapper,
)
from quality.boundedio import ReportError
from quality.policy import Component
from quality.testing import Suite, SuiteRequest
from quality.testkit.nodefakes import browser_component, script, typescript_component
from quality.testkit.suites import direct

# The fake Vitest records its call and writes JUnit results and the annotations of the
# gate's reporter beside the wrapper it was given.
VITEST = """\
echo "$@" > "$HOME/argv"
env > "$HOME/env"
echo '<testsuites tests="0"/>' > "$(dirname "$3")/junit.xml"
echo '{"inverted": ["a::x"], "retried": [], "unhandled": 2}' > "$(dirname "$3")/annotations.json"
exit 1
"""


def request(
    tmp_path: Path, suite: Suite = Suite.UNIT, component: Component | None = None
) -> SuiteRequest:
    component = component or typescript_component()
    work = tmp_path / "work"
    output = work / f"{component.id}-{suite}"
    output.mkdir(parents=True)
    (tmp_path / "snapshot" / component.root).mkdir(parents=True, exist_ok=True)
    return SuiteRequest(component, suite, ("tests",), tmp_path / "snapshot", output, 0.0, work)


def settings_of(text: str) -> dict[str, object]:
    start = text.index("const settings = ") + len("const settings = ")
    return json.loads(text[start : text.index(";\nexport default")])


def test_the_user_configuration_is_found_in_vitest_order(tmp_path: Path) -> None:
    assert user_config(tmp_path) is None
    (tmp_path / "vite.config.ts").write_text("")
    assert user_config(tmp_path) == tmp_path / "vite.config.ts"
    (tmp_path / "vitest.config.mjs").write_text("")
    assert user_config(tmp_path) == tmp_path / "vitest.config.mjs"


def test_unit_suites_measure_the_source_roots(tmp_path: Path) -> None:
    unit = request(tmp_path)
    assert coverage_settings(unit) == {
        "enabled": True,
        "provider": "v8",
        "reporter": [["lcovonly", {"file": "lcov.info"}]],
        "reportsDirectory": str(unit.output / "coverage"),
        "include": ["src/**/*.{ts,tsx,mts,cts}"],
        "exclude": ["**/*.d.{ts,mts,cts}"],
        "clean": True,
    }
    assert coverage_settings(request(tmp_path, Suite.INTEGRATION)) == {"enabled": False}


def test_the_wrapper_overrides_what_decides_the_verdict(tmp_path: Path) -> None:
    unit = request(tmp_path)
    text = wrapper(unit)
    assert text.startswith("const loaded = {};\n")
    settings = settings_of(text)
    assert settings["include"] == ["tests/**/*.{test,spec}.?(c|m)[jt]s?(x)"]
    assert settings["includeSource"] == []
    assert (settings["allowOnly"], settings["passWithNoTests"], settings["watch"]) == (
        False,
        False,
        False,
    )
    assert settings["outputFile"] == {"junit": str(unit.output / "junit.xml")}
    assert settings["retry"] == 0
    assert settings["reporters"] == ["default", "junit", str(unit.output / "quality-reporter.mjs")]
    assert settings["coverage"] == coverage_settings(unit)
    assert "tsconfigPaths: base.resolve?.tsconfigPaths ?? true" in text
    assert "projects: undefined,\n      testNamePattern: undefined,\n      ...settings," in text
    assert "expect: { ...base.test?.expect, ...settings.expect }" in text
    assert "environment" not in settings
    assert "expect" not in settings
    config = unit.directory / "vitest.config.ts"
    config.write_text("export default {};\n")
    assert wrapper(unit).startswith(f"import * as loaded from {json.dumps(str(config))};\n")


@pytest.mark.parametrize("suite", [Suite.UNIT, Suite.INTEGRATION, Suite.CONTRACT, Suite.ACCEPTANCE])
def test_browser_suites_run_in_jsdom_and_require_assertions(tmp_path: Path, suite: Suite) -> None:
    settings = settings_of(wrapper(request(tmp_path, suite, browser_component())))
    assert settings["environment"] == "jsdom"
    assert settings["expect"] == {"requireAssertions": True}


@pytest.mark.parametrize(
    ("path", "measured"),
    [
        ("app/src/a.ts", True),
        ("app/src/b.tsx", True),
        ("app/src/c.cts", True),
        ("app/src/d.d.ts", False),
        ("app/src/e.d.mts", False),
        ("app/src/f.js", False),
    ],
)
def test_declarations_and_javascript_are_not_expected(path: str, measured: bool) -> None:
    assert expected(path) is measured


def test_a_run_reports_its_results_and_coverage(node: Node, tmp_path: Path) -> None:
    unit = request(tmp_path)
    vitest = script(tmp_path / "vitest.sh", VITEST)
    run = run_vitest(node, vitest, unit)
    assert (run.returncode, run.results) == (1, unit.output / "junit.xml")
    assert run.coverage == unit.output / "coverage" / "lcov.info"
    assert (run.inverted, run.retried, run.unhandled) == (frozenset({"a::x"}), frozenset(), 2)
    assert (unit.output / "quality-reporter.mjs").read_text() == reporter(unit)
    home = unit.output.parent / f"{unit.output.name}-home"
    argv = (home / "argv").read_text().split()
    config = str(unit.output / "vitest.quality.mjs")
    assert argv == ["run", "--config", config, "--root", str(unit.directory)]
    env = (home / "env").read_text().splitlines()
    assert {"CI=1", f"TMPDIR={home / 'tmp'}", f"XDG_CACHE_HOME={home / '.cache'}"} <= set(env)
    assert (unit.output / "vitest.quality.mjs").read_text() == wrapper(unit)
    integration = request(tmp_path / "other", Suite.INTEGRATION)
    assert run_vitest(node, vitest, integration).coverage is None


def test_a_run_without_results_could_not_run(node: Node, tmp_path: Path) -> None:
    vitest = script(tmp_path / "vitest.sh", "echo 'startup error' >&2\nexit 1\n")
    with pytest.raises(ToolFailedError, match="exit status 1 without test results: startup"):
        run_vitest(node, vitest, request(tmp_path))


def test_a_node_that_cannot_start_fails_the_run(tmp_path: Path) -> None:
    node = Node(tmp_path / "missing" / "node", tmp_path / "cache")
    with pytest.raises(ToolFailedError, match=r"vitest: .*executable not found"):
        run_vitest(node, tmp_path / "vitest.sh", request(tmp_path))


def test_a_unit_run_says_why_it_had_the_network(
    node: Node, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "contained", direct("no namespaces"))
    vitest = script(tmp_path / "vitest.sh", VITEST)
    assert run_vitest(node, vitest, request(tmp_path)).unisolated == "no namespaces"
    integration = request(tmp_path / "other", Suite.INTEGRATION)
    assert run_vitest(node, vitest, integration).unisolated is None


def test_the_reporter_writes_its_annotations_into_the_output_directory(tmp_path: Path) -> None:
    unit = request(tmp_path)
    text = reporter(unit)
    assert text.startswith(f"const output = {json.dumps(str(unit.output / 'annotations.json'))};\n")
    assert "test.options.fails" in text
    assert "retryCount" in text
    assert "unhandled: errors.length" in text


def test_annotations_are_read_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "annotations.json"
    path.write_text('{"inverted": ["a"], "retried": ["b", "c"], "unhandled": 0}')
    assert read_annotations(path) == (frozenset({"a"}), frozenset({"b", "c"}), 0)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("[]", "must hold a JSON object"),
        ('{"inverted": [], "retried": []}', "'unhandled' must be a non-negative integer"),
        ('{"inverted": [], "retried": [], "unhandled": -1}', "'unhandled' must be a non-negative"),
        (
            '{"inverted": [], "retried": [], "unhandled": true}',
            "'unhandled' must be a non-negative",
        ),
        ('{"inverted": "a", "retried": [], "unhandled": 0}', "'inverted' must be a list"),
        ('{"inverted": [], "retried": [1], "unhandled": 0}', "'retried' must be a list"),
    ],
)
def test_invalid_annotations_are_rejected(tmp_path: Path, text: str, problem: str) -> None:
    path = tmp_path / "annotations.json"
    path.write_text(text)
    with pytest.raises(ReportError, match=problem):
        read_annotations(path)


def test_a_run_whose_reporter_wrote_nothing_has_no_evidence(node: Node, tmp_path: Path) -> None:
    body = 'echo "<testsuites/>" > "$(dirname "$3")/junit.xml"\n'
    vitest = script(tmp_path / "vitest.sh", body)
    with pytest.raises(ReportError, match=r"annotations\.json"):
        run_vitest(node, vitest, request(tmp_path))
