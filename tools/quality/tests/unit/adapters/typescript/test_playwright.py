import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.playwright import (
    REJECTED,
    REPORTER_BODY,
    WRAPPER_BODY,
    reporter,
    run_playwright,
    settings,
    user_config,
    wrapper,
)
from quality.policy import Suite
from quality.testing import SuiteRequest
from quality.testkit.nodefakes import browser_component, script

VIEWPORTS = """\
[components.browser.viewports]
phone = { width = 390, height = 844 }
desktop = { width = 1440, height = 900 }
"""
# The fake Playwright records its call, prints to both streams, and writes the
# results beside the configuration unless `$HOME/no-results` exists.
PLAYWRIGHT = """\
run="$(dirname "$3")"
echo "$@" > "$HOME/argv"
pwd > "$HOME/cwd"
env > "$HOME/env"
echo "1 passed"
echo "a warning" >&2
[ -e "$HOME/no-results" ] || echo '{}' > "$run/playwright.json"
exit "$(cat "$HOME/status" 2>/dev/null || echo 0)"
"""


@pytest.fixture
def request_(tmp_path: Path) -> SuiteRequest:
    snapshot, output = tmp_path / "snapshot", tmp_path / "work/web-e2e"
    (snapshot / "web").mkdir(parents=True)
    output.mkdir(parents=True)
    component = browser_component(extra=VIEWPORTS)
    return SuiteRequest(
        component, Suite.E2E, ("e2e",), snapshot, output, 0.0, tmp_path / "work", "tree"
    )


def test_a_run_covers_the_suite_roots_on_the_gate_server_in_every_viewport(
    request_: SuiteRequest,
) -> None:
    values = settings(request_, "http://127.0.0.1:4000/", Path("/browsers/shell"))
    directory, output = request_.directory, request_.output
    assert values == {
        "directory": str(directory),
        "config": str(directory / "playwright.config.ts"),
        "rejected": str(output / REJECTED),
        "roots": [str(directory / "e2e")],
        "files": r"\.(spec|test)\.(c|m)?[jt]sx?$",
        "baseURL": "http://127.0.0.1:4000/",
        "executable": "/browsers/shell",
        "trace": {"mode": "on", "screenshots": False, "snapshots": True, "sources": False},
        "artifacts": str(output / "artifacts"),
        "output": str(output),
        "reporter": str(output / "quality-playwright-reporter.mjs"),
        "results": str(output / "playwright.json"),
        "projects": ["phone", "desktop"],
        "viewports": [
            ["phone", {"width": 390, "height": 844}],
            ["desktop", {"width": 1440, "height": 900}],
        ],
    }


def test_the_configuration_overrides_what_decides_the_verdict(request_: SuiteRequest) -> None:
    text = wrapper(request_, {"retries": 3})
    assert 'const loaded = {};\nconst settings = {\n  "retries": 3\n};\n' in text
    assert text.endswith(WRAPPER_BODY)
    for line in ("retries: 0,", "forbidOnly: true,", "webServer: undefined,", "shard: null,"):
        assert f"  {line}\n" in WRAPPER_BODY
    assert reporter({"results": "/r.json"}) == (
        f'const settings = {{\n  "results": "/r.json"\n}};\n\n{REPORTER_BODY}'
    )


def test_the_component_configuration_is_imported_when_there_is_one(
    request_: SuiteRequest,
) -> None:
    directory = request_.directory
    assert user_config(directory) is None
    (directory / "playwright.config.js").write_text("export default {};\n")
    (directory / "playwright.config.ts").write_text("export default {};\n")
    (directory / "playwright.config.mjs").mkdir()
    config = directory / "playwright.config.ts"
    assert user_config(directory) == config
    assert f"import * as loaded from {json.dumps(str(config))};\n" in wrapper(request_, {})


def test_playwright_runs_in_the_component_with_a_private_home_and_no_browser_download(
    tmp_path: Path, node: Node, request_: SuiteRequest
) -> None:
    command = script(tmp_path / "playwright.sh", PLAYWRIGHT)
    values = settings(request_, "http://127.0.0.1:4000/", Path("/browsers/shell"))
    status, results = run_playwright(node, command, request_, values)
    output, home = request_.output, request_.output.parent / "web-e2e-home"
    assert (status, results) == (0, output / "playwright.json")
    assert (home / "argv").read_text().split() == [
        "test",
        "--config",
        str(output / "playwright.quality.mjs"),
    ]
    assert (home / "cwd").read_text().strip() == str(request_.directory)
    environ = set((home / "env").read_text().splitlines())
    assert {
        f"HOME={home}",
        f"TMPDIR={home / 'tmp'}",
        f"XDG_CACHE_HOME={home / '.cache'}",
        f"PLAYWRIGHT_BROWSERS_PATH={home / 'browsers'}",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1",
        "CI=1",
    } <= environ
    assert list((home / "browsers").iterdir()) == []
    assert (output / "playwright.log").read_text() == "1 passed\na warning\n"
    assert (output / "quality-playwright-reporter.mjs").read_text() == reporter(values)
    assert (output / "playwright.quality.mjs").read_text() == wrapper(request_, values)


def test_a_failing_run_returns_its_status_with_the_results(
    tmp_path: Path, node: Node, request_: SuiteRequest
) -> None:
    home = request_.output.parent / "web-e2e-home"
    home.mkdir()
    (home / "status").write_text("1\n")
    command = script(tmp_path / "playwright.sh", PLAYWRIGHT)
    status, _ = run_playwright(node, command, request_, {})
    assert status == 1


def test_a_run_without_results_is_a_tool_failure(
    tmp_path: Path, node: Node, request_: SuiteRequest
) -> None:
    home = request_.output.parent / "web-e2e-home"
    home.mkdir()
    (home / "no-results").write_text("")
    (home / "status").write_text("1\n")
    command = script(tmp_path / "playwright.sh", PLAYWRIGHT)
    with pytest.raises(ToolFailedError, match="exit status 1 without test results: a warning"):
        run_playwright(node, command, request_, {})
    assert (request_.output / "playwright.log").exists()


def test_a_run_that_cannot_start_is_a_tool_failure(tmp_path: Path, request_: SuiteRequest) -> None:
    missing = Node(tmp_path / "no-node/bin/node", tmp_path / "cache")
    with pytest.raises(ToolFailedError, match="playwright: "):
        run_playwright(missing, tmp_path / "playwright.sh", request_, {})


def test_presentation_options_are_allowlisted_and_transport_options_are_never_spread() -> None:
    assert '"locale", "timezoneId", "colorScheme"' in WRAPPER_BODY
    assert "allowed.has(key)" in WRAPPER_BODY
    assert "...presentation," in WRAPPER_BODY
    assert "...use," not in WRAPPER_BODY
    assert "...use.launchOptions" not in WRAPPER_BODY
    assert "launchOptions: { executablePath: settings.executable }" in WRAPPER_BODY
    assert '"globalSetup", "globalTeardown", "webServer", "build"' in WRAPPER_BODY
    assert "reject(`use.${key}`)" in WRAPPER_BODY
    assert "reject(`use.launchOptions.${option}`)" in WRAPPER_BODY
    assert 'serviceWorkers: "block"' in WRAPPER_BODY


def test_a_rejected_config_is_a_candidate_finding_with_the_key_and_repository_path(
    tmp_path: Path, node: Node, request_: SuiteRequest
) -> None:
    (request_.directory / "playwright.config.ts").write_text("export default {};\n")
    (request_.output / REJECTED).write_text('{"key":"use.proxy"}')
    command = script(tmp_path / "playwright.sh", PLAYWRIGHT)
    with pytest.raises(ToolFailedError) as raised:
        run_playwright(node, command, request_, {})
    found = raised.value.diagnostic
    assert (found.rule, found.message) == (
        "typescript.playwright",
        "rejected Playwright setting use.proxy",
    )
    assert found.location is not None and found.location.path == "web/playwright.config.ts"
    assert found.kind.value == "violation"
