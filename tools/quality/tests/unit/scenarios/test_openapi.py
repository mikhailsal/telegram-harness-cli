import json
from pathlib import Path

import pytest

from quality.adapters.settings import Table
from quality.boundedio import ReportError
from quality.scenarios.openapi import load_operations, operations

DOCUMENT: Table = {
    "openapi": "3.1.0",
    "paths": {
        "/notes": {
            "get": {"operationId": "listNotes"},
            "post": {"operationId": "createNote"},
            "parameters": [],
        },
        "/notes/{id}": {"get": {"operationId": " "}, "delete": {}},
    },
}


def test_operations_are_named_by_id_or_by_method_and_path() -> None:
    assert operations(DOCUMENT, "api.json") == (
        "listNotes",
        "createNote",
        "GET /notes/{id}",
        "DELETE /notes/{id}",
    )


def test_a_swagger_document_counts_too() -> None:
    assert operations({"swagger": "2.0", "paths": {"/a": {"head": {}}}}, "s.json") == ("HEAD /a",)


@pytest.mark.parametrize(
    ("document", "problem"),
    [
        ({"paths": {}}, "is not an OpenAPI document with a 'paths' object"),
        ({"openapi": "3.1.0"}, "is not an OpenAPI document with a 'paths' object"),
        ({"openapi": "3.1.0", "paths": {"/a": []}}, "the path item '/a' is not an object"),
        (
            {"openapi": "3.1.0", "paths": {"/a": {"get": "x"}}},
            "'get' of '/a' is not an operation object",
        ),
        (
            {
                "openapi": "3.1.0",
                "paths": {"/a": {"get": {"operationId": "x"}, "put": {"operationId": "x"}}},
            },
            "names several operations x",
        ),
    ],
)
def test_unusable_documents_are_rejected(document: Table, problem: str) -> None:
    with pytest.raises(ReportError, match=problem):
        operations(document, "api.json")


def test_json_and_yaml_documents_are_read(tmp_path: Path) -> None:
    (tmp_path / "openapi.json").write_text(json.dumps(DOCUMENT))
    assert load_operations(tmp_path / "openapi.json")[0] == "listNotes"
    yaml = "openapi: 3.1.0\npaths:\n  /a:\n    get:\n      operationId: getA\n"
    for name in ("openapi.yaml", "openapi.yml"):
        (tmp_path / name).write_text(yaml)
        assert load_operations(tmp_path / name) == ("getA",)


def test_other_formats_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "openapi.toml").write_text("")
    with pytest.raises(ReportError, match=r"openapi\.toml must be a \.json, \.yaml, or \.yml"):
        load_operations(tmp_path / "openapi.toml")
