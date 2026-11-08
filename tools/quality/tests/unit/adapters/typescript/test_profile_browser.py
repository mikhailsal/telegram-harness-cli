import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import profile as profile_module
from quality.adapters.typescript.browser import ACCESSIBILITY, HOOKS, TEST_RULES
from quality.adapters.typescript.endtoend import BrowserTools
from quality.adapters.typescript.environment import Environments
from quality.adapters.typescript.profile import TypeScriptProfile
from quality.diagnostics import Diagnostic, Kind, Location
from quality.testing import StaticRequest, Suite, SuiteRequest, SuiteRun
from quality.testkit.browserfakes import (
    ALL_PINS,
    FakeChromium,
    browsers_json,
    write_browser_component,
)
from quality.testkit.nodefakes import (
    FakeNode,
    browser_component,
    install_package,
    install_tools,
    script,
    write_component,
)

WEB = browser_component(extra='[components.browser]\nserver_modules = ["src/server/**"]')
FILES = ("web/src/App.tsx", "web/src/server/db.ts", "web/src/use.ts", "web/tests/App.test.tsx")
CONFIGS = ("web/vite.config.ts",)
RESOURCES = (
    "web/index.html",
    "web/package.json",
    "web/public/icon.svg",
    "web/src/App.css",
    "web/src/legacy.scss",
)
RECORD = 'echo "$@" >> "$(dirname "$0")/calls"\n'
INDEX = '<html lang="en"><link rel="icon" href="/icon.svg"><script src="/src/missing.tsx">\n'
# Stylelint writes its JSON report to the file after `--output-file`.
STYLELINT = (
    RECORD
    + 'while [ "$1" != "--output-file" ]; do shift; done\n'
    + 'printf \'[{"source": "%s/src/App.css", "warnings": [{"line": 1, "column": 2, '
    + '"rule": "color-named", "text": "Unexpected named color (color-named)"}]}]\' "$PWD" > "$2"\n'
    + "exit 2\n"
)
VITEST = (
    'echo "<testsuites/>" > "$(dirname "$3")/junit.xml"\n'
    """echo '{"inverted": [], "retried": [], "unhandled": 0}' """
    '> "$(dirname "$3")/annotations.json"\n'
)
TOOLS = {
    "prettier": RECORD + "exit 0\n",
    "eslint": RECORD + "echo '[]'\n",
    "stylelint": STYLELINT,
    "vitest": VITEST,
}


@dataclass(frozen=True, slots=True)
class Setup:
    profile: TypeScriptProfile
    snapshot: Path
    work: Path
    answers: Path

    def static(
        self, resources: tuple[str, ...] = RESOURCES, files: tuple[str, ...] = FILES
    ) -> list[Diagnostic]:
        request = StaticRequest(WEB, self.snapshot, self.work, files, CONFIGS, resources)
        return self.profile.static(request)

    def calls(self, name: str) -> list[str]:
        path = self.snapshot / "web" / "node_modules" / name / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def request(self, command: str) -> dict[str, object]:
        return json.loads((self.answers / f"{command}.request.json").read_text())


def helper_answers(directory: Path, answers: Path) -> Path:
    files = [str(directory / path.removeprefix("web/")) for path in FILES]
    typed = {"configured": True, "ignored": False, "typed": True, "parser": "typescript-eslint"}
    enabled = [{"rule": rule, "severity": 2} for rule in (*HOOKS, *ACCESSIBILITY, *TEST_RULES)]
    builtin = {"specifier": "node:fs", "line": 3, "resolved": None, "builtin": True}
    responses: dict[str, dict[str, object]] = {
        "eslint-config": {
            "files": [
                {**typed, "file": name, "rules": enabled if "use" not in name else enabled[1:]}
                for name in files
            ]
        },
        "types": {
            "projects": [
                {"config": str(directory / "tsconfig.json"), "files": files, "problems": []}
            ],
            "diagnostics": [],
        },
        "imports": {
            "files": [
                {"file": files[0], "imports": [{**builtin, "typeOnly": False}], "error": None}
            ]
        },
    }
    for command, response in responses.items():
        (answers / f"{command}.json").write_text(json.dumps({"protocol": 1, **response}))
    body = f'cp "$2" \'{answers}\'/"$1".request.json\ncat \'{answers}\'/"$1".json\n'
    return script(answers / "main.ts", body)


@pytest.fixture
def setup(fake_node: FakeNode, snapshot: Path, tmp_path: Path) -> Setup:
    directory = write_browser_component(snapshot / "web")
    install_tools(directory, TOOLS, ALL_PINS)
    (directory / "index.html").write_text(INDEX)
    environments = Environments(lambda: fake_node.node.executable, fake_node.node.cache)
    answers = tmp_path / "answers"
    answers.mkdir()
    helper = helper_answers(directory, answers)
    profile = TypeScriptProfile(environments, ALL_PINS, helper=helper)
    work = tmp_path / "work"
    assert profile.prepare(WEB, snapshot, work) == []
    return Setup(profile, snapshot, work, answers)


def test_the_browser_gates_run(setup: Setup) -> None:
    found = setup.static()
    assert [(d.rule, d.location.path if d.location else None) for d in found] == [
        ("typescript.lint", "web/src/use.ts"),
        ("typescript.styles", "web/src/legacy.scss"),
        ("typescript.entries", "web/index.html"),
        ("typescript.boundaries", "web/src/App.tsx"),
        ("typescript.styles", "web/src/App.css"),
    ]
    assert found[0].message == (
        "the browser target requires react-hooks/rules-of-hooks, which ESLint does not enable here"
    )
    assert found[1].message.startswith("Sass is not supported in a browser component")
    assert found[2].message == "'/src/missing.tsx' does not resolve to a file of the component"
    assert found[2].location == Location("web/index.html", 1)
    assert found[3].message == "browser code imports the Node builtin 'node:fs'"
    assert found[3].location == Location("web/src/App.tsx", 3)
    assert found[4].message == "Unexpected named color (color-named)"


def test_css_and_entry_points_are_formatted_and_only_browser_sources_are_bounded(
    setup: Setup,
) -> None:
    setup.static()
    [formatted] = setup.calls("prettier")
    tail = (*FILES, *CONFIGS, "web/src/App.css", "web/index.html")
    assert formatted.endswith(" ".join(str(setup.snapshot / path) for path in tail))
    [linted] = setup.calls("stylelint")
    assert linted.endswith(str(setup.snapshot / "web/src/App.css"))
    sources = [str(setup.snapshot / path) for path in ("web/src/App.tsx", "web/src/use.ts")]
    assert setup.request("imports")["files"] == sources
    rules = setup.request("eslint-config")["rules"]
    assert rules == sorted({*HOOKS, *ACCESSIBILITY, *TEST_RULES})


def test_a_missing_entry_point_is_not_formatted(setup: Setup) -> None:
    found = setup.static(resources=RESOURCES[1:])
    [missing] = [d for d in found if d.rule == "typescript.entries"]
    assert missing.message == (
        "the declared entry point web/index.html is not a file of the candidate"
    )
    assert missing.location is None
    [formatted] = setup.calls("prettier")
    assert "index.html" not in formatted


def test_without_sources_and_stylesheets_their_gates_do_not_run(setup: Setup) -> None:
    found = setup.static(resources=RESOURCES[:3], files=FILES[3:])
    assert [d.rule for d in found] == ["typescript.entries"]
    assert setup.calls("stylelint") == []
    assert not (setup.answers / "imports.request.json").exists()


def test_a_browser_component_needs_the_browser_packages(setup: Setup) -> None:
    plain = browser_component(root="plain")
    write_component(setup.snapshot / "plain")
    work = setup.work / "plain"
    found = setup.profile.prepare(plain, setup.snapshot, work)
    assert {d.rule for d in found} == {"typescript.config"}
    assert "the lockfile does not lock react; add react@19.3.1" in [d.message for d in found]
    with pytest.raises(ToolFailedError, match="'web' was not prepared"):
        setup.profile.static(StaticRequest(plain, setup.snapshot, work, ()))


def suite(setup: Setup, kind: Suite) -> SuiteRequest:
    output = setup.work / f"web-{kind}"
    output.mkdir()
    return SuiteRequest(WEB, kind, ("tests",), setup.snapshot, output, 0.0, setup.work)


def test_a_browser_unit_suite_runs_vitest_in_jsdom(setup: Setup) -> None:
    unit = suite(setup, Suite.UNIT)
    assert setup.profile.run(unit).results == unit.output / "junit.xml"
    assert '"environment": "jsdom"' in (unit.output / "vitest.quality.mjs").read_text()


def test_a_browser_end_to_end_suite_needs_a_browser(setup: Setup) -> None:
    with pytest.raises(ToolFailedError, match="this build of the gate has no browser") as raised:
        setup.profile.run(suite(setup, Suite.E2E))
    assert raised.value.diagnostic.kind is Kind.ENVIRONMENT


def browser_profile(setup: Setup, fake: FakeChromium) -> TypeScriptProfile:
    environments, helper = setup.profile.environments, setup.profile.helper
    profile = TypeScriptProfile(environments, ALL_PINS, chromium=fake.chromium([]), helper=helper)
    assert profile.prepare(WEB, setup.snapshot, setup.work) == []
    return profile


def test_a_browser_suite_must_know_its_chromium(setup: Setup, tmp_path: Path) -> None:
    profile = browser_profile(setup, FakeChromium.create(tmp_path / "chromium"))
    with pytest.raises(ToolFailedError, match="cannot tell which Chromium") as raised:
        profile.run(suite(setup, Suite.E2E))
    assert raised.value.diagnostic.rule == "typescript.browser-version"


def test_a_browser_suite_runs_with_the_locked_tools_and_the_pinned_browser(
    setup: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = setup.snapshot / "web"
    (directory / "node_modules/playwright-core").mkdir(parents=True)
    (directory / "node_modules/playwright-core/browsers.json").write_text(browsers_json())
    install_package(directory, "vite", ALL_PINS["vite"], "exit 0\n")
    runner = directory / "node_modules/@playwright/test"
    script(runner / "cli.js", "exit 0\n")
    manifest = {"version": ALL_PINS["@playwright/test"], "bin": {"playwright": "cli.js"}}
    (runner / "package.json").write_text(json.dumps(manifest))
    install_package(directory, "@axe-core/playwright", ALL_PINS["@axe-core/playwright"], "")
    install_package(directory, "axe-core", "4.11.0", "")
    fake = FakeChromium.create(tmp_path / "chromium")
    captured: list[BrowserTools] = []

    def run_browser_suite(tools: BrowserTools, request: SuiteRequest) -> SuiteRun:
        captured.append(tools)
        return SuiteRun(0, request.output / "playwright.json")

    monkeypatch.setattr(profile_module, "run_browser_suite", run_browser_suite)
    browser_profile(setup, fake).run(suite(setup, Suite.E2E))
    (tools,) = captured
    assert tools.vite == (directory / "node_modules/vite/cli.sh").resolve()
    assert tools.playwright == (runner / "cli.js").resolve()
    assert (tools.browser, tools.axe) == (fake.executable, "4.11.0")
