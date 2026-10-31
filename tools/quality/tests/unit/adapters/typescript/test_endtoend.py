import http.client
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import browserrun, bundle, endtoend
from quality.adapters.typescript.browserrun import BrowserJob, BrowserReply
from quality.adapters.typescript.build import BuildOutcome, BuildRequest
from quality.adapters.typescript.bundle import collect
from quality.adapters.typescript.endtoend import BrowserTools, locked_axe, run_browser_suite
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.diagnostics import Diagnostic, Location
from quality.policy import Suite
from quality.testing import ACCESSIBILITY_SCAN, ResultsFormat, SuiteRequest, SuiteRun
from quality.testkit.nodefakes import browser_component

AXE = "4.11.0"
SPEC = "e2e/signup.spec.ts"
TAGS: list[JsonValue] = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
SETTINGS = """\
[components.browser]
entries = ["pages/index.html"]
"""
COMPONENT = browser_component(extra=SETTINGS)
FORM = "e2e/signup.spec.ts::signup > shows the form"
NOTICE = bundle.OUTPUT.diagnostic("the build warned", component="web")


def tools(tmp_path: Path) -> BrowserTools:
    node = Node(tmp_path / "node", tmp_path / "cache")
    return BrowserTools(node, tmp_path / "vite", tmp_path / "playwright", tmp_path / "shell", AXE)


@pytest.fixture
def request_(tmp_path: Path) -> SuiteRequest:
    snapshot, output = tmp_path / "snapshot", tmp_path / "work/web-e2e"
    (snapshot / "web").mkdir(parents=True)
    output.mkdir(parents=True)
    files = ("web/src/main.tsx", "web/e2e/signup.spec.ts")
    return SuiteRequest(
        COMPONENT, Suite.E2E, ("e2e",), snapshot, output, 0.0, tmp_path / "work", "tree", files
    )


def scan(origin: str, *violations: JsonValue, target: str = "#form") -> JsonValue:
    return {
        "testEngine": {"name": "axe-core", "version": AXE},
        "toolOptions": {"runOnly": {"type": "tag", "values": TAGS}},
        "url": f"{origin}/app/signup",
        "violations": list(violations),
        "passes": [{"id": "label", "nodes": [{"target": [target]}]}],
    }


def write(path: Path, data: JsonValue) -> str:
    path.write_text(json.dumps(data))
    return str(path)


def trace(path: Path, *events: JsonValue) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trace.trace", "\n".join(json.dumps(event) for event in events))
    return str(path)


def console(text: str, url: str = "", *, failed: bool = False) -> JsonValue:
    args: list[JsonValue] = [] if failed else [{"preview": text}]
    return {
        "type": "console",
        "messageType": "error",
        "text": text,
        "args": args,
        "location": {"url": url},
    }


def fetch(url: str) -> int:
    address = urlsplit(url)
    connection = http.client.HTTPConnection(address.hostname or "", address.port, timeout=10)
    try:
        connection.request("GET", address.path)
        return connection.getresponse().status
    finally:
        connection.close()


def entry(project: str, name: str, *attachments: tuple[str, str]) -> JsonValue:
    return {
        "file": SPEC,
        "title": ["signup", name],
        "project": project,
        "outcome": "expected",
        "expected": "passed",
        "attachments": [{"name": label, "path": path} for label, path in attachments],
    }


def document_trace(url: str, out: Path) -> str:
    """Construct this fake's document trace using a token the owned server actually issued."""
    connection = http.client.HTTPConnection(urlsplit(url).hostname or "", urlsplit(url).port)
    connection.request("GET", urlsplit(url).path)
    response = connection.getresponse()
    token = response.getheader("X-Quality-Document")
    response.read()
    connection.close()
    with zipfile.ZipFile(out / "document.zip", "w") as archive:
        archive.writestr(
            "trace.network",
            json.dumps(
                {
                    "type": "resource-snapshot",
                    "snapshot": {
                        "_resourceType": "document",
                        "pageref": "page@1",
                        "request": {"url": url},
                        "response": {
                            "status": 200,
                            "headers": [{"name": "X-Quality-Document", "value": token}],
                        },
                    },
                }
            ),
        )
    return str(out / "document.zip")


class FakeRun:
    """Stands in for Playwright: requests files of the gate's server and writes results."""

    def __init__(self, status: int = 1) -> None:
        self.status = status
        self.statuses: list[int] = []
        self.values: dict[str, JsonValue] = {}

    def __call__(
        self, node: Node, script: Path, request: SuiteRequest, values: dict[str, JsonValue]
    ) -> tuple[int, Path]:
        self.values = values
        url = str(values["baseURL"])
        origin = url.removesuffix("/app/")
        self.statuses = [fetch(url), fetch(url + "logo.svg"), fetch(url + "fonts/x.woff2")]

        out = request.output
        shown = trace(
            out / "form.zip",
            console(f"boom at {origin}/app/main.js"),
            console("Failed to load resource: 404", f"{origin}/app/logo.svg", failed=True),
        )
        violation: JsonValue = {
            "id": "image-alt",
            "impact": "critical",
            "help": "Images must have alternative text",
            "nodes": [{"target": ["img"]}],
        }
        scans = {
            "form": write(out / "form.json", scan(origin, violation)),
            "clean": write(out / "clean.json", scan(origin)),
            "empty": write(out / "empty.json", scan(origin, target="body")),
        }
        tests = [
            entry(
                "narrow", "shows the form", ("trace", shown), (ACCESSIBILITY_SCAN, scans["form"])
            ),
            entry("wide", "shows the form", ("trace", shown), (ACCESSIBILITY_SCAN, scans["clean"])),
            entry("narrow", "scans one viewport", (ACCESSIBILITY_SCAN, scans["clean"])),
            entry("wide", "scans one viewport"),
            entry("narrow", "scans nothing", (ACCESSIBILITY_SCAN, scans["empty"])),
            entry("wide", "scans nothing", (ACCESSIBILITY_SCAN, scans["empty"])),
        ]
        navigation = document_trace(url, out)
        for test in tests:
            assert isinstance(test, dict)
            attachments = test["attachments"]
            assert isinstance(attachments, list)
            attachments.append({"name": "trace", "path": navigation})
        document: JsonValue = {"projects": ["narrow", "wide"], "tests": tests, "errors": ["late"]}
        return self.status, Path(write(Path(str(values["results"])), document))


def build_into(captured: list[BuildRequest], base: str = "/app/") -> object:
    def production_build(node: Node, vite: Path, request: BuildRequest) -> BuildOutcome:
        captured.append(request)
        dist = request.run / "dist"
        (dist / "pages").mkdir(parents=True)
        (dist / "pages/index.html").write_text("<p>page</p>\n")
        return BuildOutcome([NOTICE], collect(dist, request.identity), base)

    return production_build


def assert_native_evidence(request_: SuiteRequest, run: SuiteRun) -> None:
    """Check that native evidence keeps the candidate identity, digest, and served counts."""
    evidence = json.loads((request_.output / "build.json").read_text())
    assert (evidence["candidate"], evidence["base"]) == ("tree", "/app/")
    assert list(evidence["files"]) == ["pages/index.html"]
    assert run.reports == (
        request_.output / "build.json",
        request_.output / "playwright.log",
        request_.output / "server.json",
    )
    served = json.loads((request_.output / "server.json").read_text())
    assert (served["candidate"], served["digest"]) == ("tree", evidence["digest"])
    assert (served["documents"], served["passed_tests"]) == (2, 6)


def test_a_suite_runs_on_the_fresh_build_and_judges_what_the_tests_left(
    tmp_path: Path, request_: SuiteRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[BuildRequest] = []
    fake = FakeRun()
    monkeypatch.setattr(endtoend, "production_build", build_into(captured))
    monkeypatch.setattr(browserrun, "run_playwright", fake)

    def owned(job: BrowserJob) -> tuple[BrowserReply, str | None]:
        return browserrun.execute(job), None

    monkeypatch.setattr(endtoend, "run_owned", owned)
    run = run_browser_suite(tools(tmp_path), request_)
    (built,) = captured
    assert (built.run, built.identity) == (request_.output / "build", "tree")
    assert (built.files, built.sources) == (request_.files, frozenset({"web/src/main.tsx"}))
    assert fake.statuses == [200, 404, 404]
    assert fake.values["executable"] == str(tmp_path / "shell")
    assert (run.returncode, run.results_format, run.unhandled) == (1, ResultsFormat.PLAYWRIGHT, 1)
    assert run.evidence == {FORM: frozenset({ACCESSIBILITY_SCAN})}
    assert_native_evidence(request_, run)
    found = [(item.rule, item.message, item.location) for item in run.findings]
    spec, entry_file = Location("web/e2e/signup.spec.ts"), Location("web/pages/index.html")
    assert found == [
        (NOTICE.rule, NOTICE.message, None),
        ("typescript.browser-console", f"{FORM}: boom at /app/main.js", spec),
        (
            "typescript.accessibility",
            f"{FORM}: image-alt (critical) at img: Images must have alternative text",
            spec,
        ),
        (
            "typescript.accessibility-scan",
            "e2e/signup.spec.ts::signup > scans nothing: the scanned page had no content",
            spec,
        ),
        (
            "typescript.browser-resource",
            f"{FORM} requested /app/logo.svg, which the production build does not have",
            spec,
        ),
        (
            "typescript.browser-resource",
            "a scenario requested /app/fonts/x.woff2, which the production build does not have",
            entry_file,
        ),
    ]


def test_a_build_without_output_leaves_nothing_to_test(
    tmp_path: Path, request_: SuiteRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = Diagnostic("tools.failed", NOTICE.severity, "vite: the build failed")

    def production_build(node: Node, vite: Path, request: BuildRequest) -> BuildOutcome:
        return BuildOutcome([failed])

    monkeypatch.setattr(endtoend, "production_build", production_build)
    with pytest.raises(ToolFailedError) as raised:
        run_browser_suite(tools(tmp_path), request_)
    assert raised.value.diagnostic == failed


def package(directory: Path, name: str, manifest: str) -> None:
    (directory / "node_modules" / name).mkdir(parents=True, exist_ok=True)
    (directory / "node_modules" / name / "package.json").write_text(manifest)


def test_the_axe_core_that_the_adapter_loads_is_the_locked_one(tmp_path: Path) -> None:
    web = tmp_path / "web"
    package(tmp_path, "axe-core", '{"version": "4.0.0"}')
    package(web, "@axe-core/playwright", '{"version": "4.11.1"}')
    assert locked_axe(web, tmp_path) == "4.0.0"
    package(web / "node_modules/@axe-core/playwright", "axe-core", '{"version": "4.11.0"}')
    assert locked_axe(web, tmp_path) == "4.11.0"


@pytest.mark.parametrize(
    ("manifest", "problem"),
    [
        (None, "@axe-core/playwright and its axe-core are not installed for"),
        ("{", "axe-core: .*package.json"),
        ('{"version": 4}', "names no version"),
    ],
)
def test_an_axe_core_the_gate_cannot_read_is_a_tool_failure(
    tmp_path: Path, manifest: str | None, problem: str
) -> None:
    package(tmp_path, "@axe-core/playwright", "{}")
    if manifest is not None:
        package(tmp_path, "axe-core", manifest)
    with pytest.raises(ToolFailedError, match=problem):
        locked_axe(tmp_path, tmp_path)


def test_a_successful_report_without_fetching_the_build_blocks_the_gate(
    tmp_path: Path, request_: SuiteRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(endtoend, "production_build", build_into([]))

    def substitute(job: BrowserJob) -> tuple[BrowserReply, str | None]:
        report = job.request.output / "playwright.json"
        write(
            report,
            {"projects": ["narrow"], "tests": [entry("narrow", "substituted")], "errors": []},
        )
        return BrowserReply(0, report, "http://127.0.0.1:4000"), "no namespaces"

    monkeypatch.setattr(endtoend, "run_owned", substitute)
    result = run_browser_suite(tools(tmp_path), request_)
    assert result.unisolated == "no namespaces"
    assert result.evidence == {}
    assert [found.rule for found in result.findings] == [NOTICE.rule, "typescript.browser-served"]
    assert "passed without fetching a document from the owned server" in result.findings[-1].message
    assert result.invalidated == frozenset({"e2e/signup.spec.ts::signup > substituted"})
