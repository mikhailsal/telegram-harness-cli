import json
import zipfile
from pathlib import Path

import pytest

from quality.adapters.typescript import traces
from quality.adapters.typescript.traces import TraceEvents, read_trace
from quality.boundedio import JsonValue, ReportError
from quality.diagnostics import Location, Severity

ORIGIN = "http://127.0.0.1:41234"


def console(text: str, args: list[JsonValue] | None = None, url: str = "") -> JsonValue:
    return {
        "type": "console",
        "messageType": "error",
        "text": text,
        "args": args if args is not None else [{"preview": text}],
        "location": {"url": url},
    }


def page_error(error: JsonValue) -> JsonValue:
    return {"type": "event", "method": "pageError", "params": {"error": error}}


def trace(path: Path, *members: tuple[str, list[JsonValue]], extra: bytes = b"") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, events in members:
            lines = b"\n".join(json.dumps(event).encode() for event in events)
            archive.writestr(name, lines + extra)
    return path


def test_console_and_page_errors_are_read_from_every_trace_member(tmp_path: Path) -> None:
    first: list[JsonValue] = [
        console(f"the request to {ORIGIN}/api failed"),
        {"type": "console", "messageType": "warning", "text": "a warning"},
        {"type": "before", "method": "goto"},
        page_error({"error": {"name": "TypeError", "message": "x is undefined"}}),
    ]
    second: list[JsonValue] = [
        page_error({"error": {"name": "", "message": "plain"}}),
        page_error({"error": {"message": 3}}),
        page_error({"value": "thrown text"}),
        page_error({"value": None}),
        {"type": "console", "messageType": "error", "text": None},
    ]
    path = trace(
        tmp_path / "trace.zip",
        ("trace.trace", first),
        ("1-trace.trace", second),
        ("trace.network", [console("not a trace member")]),
    )
    assert read_trace(path, ORIGIN) == TraceEvents(
        (
            "the request to /api failed",
            "uncaught TypeError: x is undefined",
            "uncaught plain",
            "uncaught ",
            'uncaught "thrown text"',
            "uncaught null",
            "",
        )
    )


def test_a_failed_load_without_arguments_is_a_request_not_a_console_error(tmp_path: Path) -> None:
    events: list[JsonValue] = [
        console("Failed to load resource: 404", [], f"{ORIGIN}/missing.svg"),
        {"type": "console", "messageType": "error", "text": "Failed to load resource: 500"},
        console("Failed to load resource: logged by the application"),
    ]
    found = read_trace(trace(tmp_path / "trace.zip", ("trace.trace", events)), ORIGIN)
    assert found.failed == ("/missing.svg", "")
    assert found.errors == ("Failed to load resource: logged by the application",)


def test_lines_that_are_not_json_objects_are_skipped(tmp_path: Path) -> None:
    path = trace(tmp_path / "trace.zip", ("trace.trace", [[1], "text"]), extra=b"\n{broken")
    assert read_trace(path, ORIGIN) == TraceEvents()


@pytest.mark.parametrize("content", [None, b"not a zip archive"])
def test_a_trace_that_cannot_be_read_is_a_report_error(
    tmp_path: Path, content: bytes | None
) -> None:
    path = tmp_path / "trace.zip"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(ReportError, match=r"the trace .* cannot be read"):
        read_trace(path, ORIGIN)


def test_an_oversized_trace_member_is_a_report_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(traces, "TRACE_LIMIT", 8)
    path = trace(tmp_path / "trace.zip", ("trace.trace", [console("too long")]))
    with pytest.raises(ReportError, match=r"trace member trace\.trace exceeds 8 bytes"):
        read_trace(path, ORIGIN)


@pytest.mark.parametrize(
    ("target", "path"),
    [("/a.svg", "/a.svg"), (f"{ORIGIN}/a.svg?v=1#top", "/a.svg?v=1"), ("/x?", "/x")],
)
def test_a_request_is_named_by_its_path_and_query(target: str, path: str) -> None:
    assert traces.request_path(target) == path


def test_each_console_error_of_a_test_is_one_finding() -> None:
    events = TraceEvents(("the page logs an error", "uncaught Error", "the page logs an error"))
    found = traces.console_findings("web", "web/e2e/a.spec.ts", "a > b", events)
    assert [item.message for item in found] == [
        "a > b: the page logs an error",
        "a > b: uncaught Error",
    ]
    assert found[0].rule == "typescript.browser-console"
    assert found[0].severity is Severity.ERROR
    assert found[0].location == Location("web/e2e/a.spec.ts")
    assert found[0].data == {"symbol": "the page logs an error"}


@pytest.mark.parametrize(
    ("test", "message"),
    [
        ("a > b", "a > b requested /logo.svg, which the production build does not have"),
        (None, "a scenario requested /logo.svg, which the production build does not have"),
    ],
)
def test_a_missing_file_is_reported_with_the_test_that_requested_it(
    test: str | None, message: str
) -> None:
    found = traces.resource_finding("web", "web/index.html", test, "/logo.svg")
    assert (found.rule, found.message) == ("typescript.browser-resource", message)
    assert (found.component, found.data) == ("web", {"symbol": "/logo.svg"})
