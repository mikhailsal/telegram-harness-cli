"""Console errors, page errors, and missing files of browser tests (decision 0018).

Each test's Playwright trace is a zip of JSON-lines files. The browser
contexts' events hold every console message and every uncaught page error;
Chromium also logs each response with an error status as a console error
`Failed to load resource: ...` without arguments, located at the resource's
URL. Those are not console errors of the application: a request the gate's
server could not answer is reported from the server's own record as
`typescript.browser-resource`, attributed to each test whose trace shows it,
and a response a test fulfills itself, such as a simulated backend error, is
the test's business. Every other console error and every page error is a
`typescript.browser-console` error. Messages lose the gate server's origin,
whose port changes from run to run, so an exception can name them exactly.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from quality.boundedio import JsonValue, ReportError, parse_json
from quality.diagnostics import Diagnostic, Location, Rule

CONSOLE = Rule(
    id="typescript.browser-console",
    title="A browser scenario logged a console error or an uncaught page error",
    requirement="R06",
    explanation=(
        "A production page that logs an error or throws an uncaught exception is broken "
        "for its users even when the scenario's assertions pass."
    ),
    remedy=(
        "Fix the error. A console error that a scenario provokes on purpose needs an exception "
        "naming the rule, the test file, and the message."
    ),
)
RESOURCE = Rule(
    id="typescript.browser-resource",
    title="A browser scenario requested a file the production build does not have",
    requirement="R06",
    explanation=(
        "The gate's server answers only with the files of the production build. A request it "
        "cannot answer is a broken script, stylesheet, image, or link of the built page."
    ),
    remedy="Import the file from the source or put it in `public/`, so the build emits it.",
)
RULES = (CONSOLE, RESOURCE)

FAILED_LOAD = "Failed to load resource:"
TRACE_SUFFIX = ".trace"
NETWORK_SUFFIX = ".network"
DOCUMENT_HEADER = "x-quality-document"
TRACE_LIMIT = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TraceEvents:
    """The console errors, page errors, and failed loads one trace recorded, in order."""

    errors: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


def _object(value: JsonValue, key: str) -> dict[str, JsonValue]:
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, dict) else {}


def _page_error(event: dict[str, JsonValue]) -> str:
    error = _object(_object(event, "params"), "error")
    thrown = _object(error, "error")
    if thrown:
        name, message = thrown.get("name"), thrown.get("message")
        prefix = f"{name}: " if isinstance(name, str) and name else ""
        return f"uncaught {prefix}{message if isinstance(message, str) else ''}"
    return f"uncaught {json.dumps(error.get('value'))}"


def _event(event: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Return ("error", message) or ("failed", url) for an event that matters, else None."""
    if event.get("type") == "event" and event.get("method") == "pageError":
        return "error", _page_error(event)
    if event.get("type") != "console" or event.get("messageType") != "error":
        return None
    text = event.get("text")
    text = text if isinstance(text, str) else ""
    if text.startswith(FAILED_LOAD) and not event.get("args"):
        url = _object(event, "location").get("url")
        return "failed", url if isinstance(url, str) else ""
    return "error", text


def _lines(archive: zipfile.ZipFile, suffix: str) -> Iterator[bytes]:
    for info in archive.infolist():
        if not info.filename.endswith(suffix):
            continue
        if info.file_size > TRACE_LIMIT:
            msg = f"trace member {info.filename} exceeds {TRACE_LIMIT} bytes"
            raise ReportError(msg)
        with archive.open(info) as stream:
            yield from stream.read(TRACE_LIMIT + 1).splitlines()


def _events(
    archive: zipfile.ZipFile, name: str, suffix: str = TRACE_SUFFIX
) -> Iterator[dict[str, JsonValue]]:
    """Yield the JSON objects of the trace's lines, skipping lines that are not JSON."""
    for line in _lines(archive, suffix):
        try:
            event = parse_json(line, name)
        except ReportError:
            continue
        if isinstance(event, dict):
            yield event


def read_trace(path: Path, origin: str) -> TraceEvents:
    """Read the events of one trace; `origin` is removed from the messages."""
    errors: list[str] = []
    failed: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for kind, text in filter(None, map(_event, _events(archive, str(path)))):
                (errors if kind == "error" else failed).append(text.replace(origin, ""))
    except (OSError, zipfile.BadZipFile) as error:
        msg = f"the trace {path} cannot be read: {error}"
        raise ReportError(msg) from error
    return TraceEvents(tuple(errors), tuple(failed))


def _document_headers(event: dict[str, JsonValue], origin: str) -> list[JsonValue]:
    snapshot = _object(event, "snapshot")
    response, request = _object(snapshot, "response"), _object(snapshot, "request")
    if (event.get("type"), snapshot.get("_resourceType"), response.get("status")) != (
        "resource-snapshot",
        "document",
        200,
    ):
        return []
    if snapshot.get("_wasFulfilled") or snapshot.get("_wasAborted"):
        return []
    page, url = snapshot.get("pageref"), request.get("url")
    if not isinstance(page, str) or not page:
        return []
    if not isinstance(url, str) or not url.startswith(f"{origin}/"):
        return []
    headers = response.get("headers")
    return headers if isinstance(headers, list) else []


def _document_tokens(event: dict[str, JsonValue], origin: str) -> Iterator[str]:
    for header in _document_headers(event, origin):
        if not isinstance(header, dict):
            continue
        name, token = header.get("name"), header.get("value")
        if (
            isinstance(name, str)
            and name.lower() == DOCUMENT_HEADER
            and isinstance(token, str)
            and token
        ):
            yield token


def served_documents(path: Path, origin: str) -> frozenset[str]:
    """Read owned-server response tokens from this test's successful document navigations.

    Network HAR snapshots identify browser document responses separately from API requests.
    Fulfilled routes are substitutions even when their response copies a server token.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            return frozenset(
                token
                for event in _events(archive, str(path), NETWORK_SUFFIX)
                for token in _document_tokens(event, origin)
            )
    except (OSError, zipfile.BadZipFile) as error:
        msg = f"the trace {path} cannot be read: {error}"
        raise ReportError(msg) from error


def request_path(target: str) -> str:
    """Return the path of a request target or URL, which the server records."""
    parts = urlsplit(target)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def console_findings(component: str, file: str, test: str, events: TraceEvents) -> list[Diagnostic]:
    """Report each console and page error of one test once."""
    return [
        CONSOLE.diagnostic(
            f"{test}: {message}",
            location=Location(file),
            component=component,
            data={"symbol": message},
        )
        for message in dict.fromkeys(events.errors)
    ]


def resource_finding(component: str, file: str, test: str | None, path: str) -> Diagnostic:
    """Report one request the server could not answer, with the test that made it."""
    where = f"{test} requested" if test is not None else "a scenario requested"
    return RESOURCE.diagnostic(
        f"{where} {path}, which the production build does not have",
        location=Location(file),
        component=component,
        data={"symbol": path},
    )
