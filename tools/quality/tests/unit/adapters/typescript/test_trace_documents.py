import json
import zipfile
from pathlib import Path

import pytest

from quality.adapters.typescript import traces
from quality.adapters.typescript.traces import served_documents
from quality.boundedio import JsonValue, ReportError

ORIGIN = "http://127.0.0.1:41234"
HEADER: list[JsonValue] = [{"name": "X-Quality-Document", "value": "candidate-token"}]


def document(**fields: JsonValue) -> JsonValue:
    snapshot: dict[str, JsonValue] = {
        "_resourceType": "document",
        "pageref": "page@native-chromium",
        "request": {"url": f"{ORIGIN}/", "method": "GET"},
        "response": {"status": 200, "headers": HEADER},
        **fields,
    }
    return {"type": "resource-snapshot", "snapshot": snapshot}


def trace(tmp_path: Path, events: list[JsonValue], *, extra: bytes = b"") -> Path:
    path = tmp_path / "trace.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trace.trace", json.dumps(document()))
        archive.writestr(
            "trace.network", b"\n".join(json.dumps(event).encode() for event in events) + extra
        )
        valid = document(
            response={
                "status": 200,
                "headers": [{"name": "X-Quality-Document", "value": "server-token"}],
            }
        )
        archive.writestr("1-trace.network", json.dumps(valid))
    return path


def test_owned_document_tokens_are_read_from_every_network_member(tmp_path: Path) -> None:
    headers: list[JsonValue] = [
        {"name": "x-quality-document", "value": "second-token"},
        {"name": "X-QUALITY-DOCUMENT", "value": "server-token"},
    ]
    events = [document(response={"status": 200, "headers": headers})]
    assert served_documents(trace(tmp_path, events), ORIGIN) == {"server-token", "second-token"}


@pytest.mark.parametrize(
    "event",
    [
        {"type": "console"},
        {"type": "resource-snapshot", "snapshot": "broken"},
        document(_resourceType="fetch"),
        document(response={"status": 404, "headers": HEADER}),
        document(_wasFulfilled=True),
        document(_wasAborted=True),
        document(pageref=None),
        document(pageref=""),
        document(request={"url": None}),
        document(request={"url": f"{ORIGIN}.example.invalid/"}),
        document(request={"url": "http://127.0.0.1:1/"}),
        document(response={"status": 200, "headers": {}}),
        document(response={"status": 200, "headers": [None, "broken"]}),
        document(response={"status": 200, "headers": [{"name": None, "value": "x"}]}),
        document(response={"status": 200, "headers": [{"name": "other", "value": "x"}]}),
        document(response={"status": 200, "headers": [{"name": "X-Quality-Document"}]}),
        document(
            response={"status": 200, "headers": [{"name": "X-Quality-Document", "value": ""}]}
        ),
    ],
)
def test_only_owned_browser_documents_with_nonempty_server_tokens_count(
    tmp_path: Path, event: JsonValue
) -> None:
    # The second network member is always a valid document; the input adds no token.
    assert served_documents(trace(tmp_path, [event]), ORIGIN) == {"server-token"}


def test_malformed_network_lines_are_ignored(tmp_path: Path) -> None:
    assert served_documents(trace(tmp_path, [[1], "text"], extra=b"\n{broken"), ORIGIN) == {
        "server-token"
    }


@pytest.mark.parametrize("present", [True, False])
def test_missing_or_invalid_trace_is_a_report_error(tmp_path: Path, present: bool) -> None:
    path = tmp_path / "trace.zip"
    if present:
        path.write_bytes(b"not zip")
    with pytest.raises(ReportError, match=r"the trace .* cannot be read"):
        served_documents(path, ORIGIN)


def test_oversized_network_members_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = trace(tmp_path, [document()])
    monkeypatch.setattr(traces, "TRACE_LIMIT", 8)
    with pytest.raises(ReportError, match=r"trace member trace\.network exceeds 8 bytes"):
        served_documents(path, ORIGIN)


def test_a_trace_without_network_documents_has_no_served_evidence(tmp_path: Path) -> None:
    path = tmp_path / "trace.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trace.trace", json.dumps(document()))
        archive.writestr("trace.network", "")
    assert served_documents(path, ORIGIN) == frozenset()
