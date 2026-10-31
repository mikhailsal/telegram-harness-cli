"""The tests, OpenAPI document, and scenario manifest of the `notes` fixture.

The unit suite covers the sources on loopback; the integration suite proves
each requirement against the real server and database file.
"""

from __future__ import annotations

CONFTEST = """\
from collections.abc import Iterator
from pathlib import Path

import pytest

from notes.client import Client, serving
from notes.store import migrate


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "notes.sqlite3"
    migrate(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[Client]:
    with serving(database) as running:
        yield running
"""

UNIT_TESTS = """\
import sqlite3
from pathlib import Path

import pytest

from notes.client import Client
from notes.store import create_note, get_note, list_notes, migrate


def test_migrations_are_applied_once(database: Path) -> None:
    assert migrate(database) == 2


def test_the_store_reads_what_it_writes(database: Path) -> None:
    note = create_note(database, "milk", "ada")
    assert note == {"id": 1, "text": "milk", "author": "ada"}
    assert get_note(database, 1) == note
    assert list_notes(database) == [note]
    assert get_note(database, 2) is None


def test_blank_text_violates_the_schema(database: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        create_note(database, " ", "ada")


def test_the_server_serves_each_operation(client: Client) -> None:
    assert client.create("milk", **{"X-User": "ada"}) == (
        201,
        {"id": 1, "text": "milk", "author": "ada"},
    )
    assert client.create("eggs")[1] == {"id": 2, "text": "eggs", "author": "anonymous"}
    eggs = {"id": 2, "text": "eggs", "author": "anonymous"}
    assert client.call("GET", "/notes/2") == (200, eggs)
    assert client.call("GET", "/notes")[1] == {
        "notes": [
            {"id": 1, "text": "milk", "author": "ada"},
            {"id": 2, "text": "eggs", "author": "anonymous"},
        ]
    }


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("GET", "/notes/9", 404), ("GET", "/other", 404), ("POST", "/other", 404)],
)
def test_unknown_resources_are_not_found(
    client: Client, method: str, path: str, status: int
) -> None:
    assert client.call(method, path, b"{}")[0] == status


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"text": 1}', b'{"text": " "}'])
def test_invalid_bodies_are_bad_requests(client: Client, body: bytes) -> None:
    assert client.call("POST", "/notes", body, **{"X-Role": "writer"})[0] == 400


def test_only_writers_create_notes(client: Client) -> None:
    assert client.create("milk", **{"X-Role": "reader"})[0] == 403
"""

INTEGRATION_TESTS = """\
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from notes.client import Client
from notes.store import MIGRATIONS, migrate

DOCUMENT = Path(__file__).resolve().parents[1] / "openapi.json"
TYPES = {"integer": int, "string": str}


def test_a_writer_creates_a_note_and_reads_it_back(client: Client) -> None:
    status, created = client.create("milk", **{"X-User": "ada"})
    assert (status, created) == (201, {"id": 1, "text": "milk", "author": "ada"})
    assert client.call("GET", "/notes/1") == (200, created)
    assert client.call("GET", "/notes") == (200, {"notes": [created]})


def test_notes_are_rows_of_the_sqlite_file(client: Client, database: Path) -> None:
    client.create("milk")
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT id, text FROM notes").fetchall() == [(1, "milk")]


def test_responses_keep_the_published_contract(client: Client) -> None:
    schema = json.loads(DOCUMENT.read_text(encoding="utf-8"))["components"]["schemas"]["Note"]
    _, created = client.create("milk")
    assert isinstance(created, dict)
    note = cast(dict[str, object], created)
    assert set(note) == set(schema["required"]) == set(schema["properties"])
    for name, value in note.items():
        assert isinstance(value, TYPES[schema["properties"][name]["type"]])


@pytest.mark.parametrize("body", [b"not json", b'{"text": " "}'])
def test_invalid_bodies_are_rejected(client: Client, body: bytes) -> None:
    assert client.call("POST", "/notes", body, **{"X-Role": "writer"})[0] == 400


def test_a_missing_note_is_not_found(client: Client) -> None:
    assert client.call("GET", "/notes/7") == (404, {"error": "no such note"})


def test_readers_cannot_create_notes(client: Client) -> None:
    assert client.create("milk", **{"X-Role": "reader"}) == (
        403,
        {"error": "only writers create notes"},
    )
    assert client.call("GET", "/notes") == (200, {"notes": []})


def test_the_schema_refuses_blank_text(database: Path) -> None:
    with closing(sqlite3.connect(database)) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO notes (text) VALUES ('  ')")


def test_migrations_build_a_fresh_schema(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite3"
    assert migrate(database) == 2
    with closing(sqlite3.connect(database)) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(notes)")]
    assert columns == ["id", "text", "author"]


def test_a_version_1_database_upgrades(tmp_path: Path) -> None:
    database = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        db.executescript((MIGRATIONS / "0001_create_notes.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO notes (text) VALUES ('kept')")
        db.commit()
    assert migrate(database) == 2
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT text, author FROM notes").fetchall() == [("kept", "anonymous")]
"""

OPENAPI = """\
{
  "openapi": "3.1.0",
  "info": {"title": "Notes", "version": "1.0.0"},
  "paths": {
    "/notes": {
      "get": {"operationId": "listNotes", "responses": {"200": {"description": "The notes"}}},
      "post": {"operationId": "createNote", "responses": {
        "201": {"description": "The new note"}, "400": {"description": "No non-blank text"},
        "403": {"description": "Only writers create notes"}}}
    },
    "/notes/{id}": {
      "get": {"operationId": "getNote", "responses": {
        "200": {"description": "The note"}, "404": {"description": "No such note"}}}
    }
  },
  "components": {"schemas": {"Note": {
    "type": "object",
    "required": ["id", "text", "author"],
    "properties": {
      "id": {"type": "integer"}, "text": {"type": "string"}, "author": {"type": "string"}
    }
  }}}
}
"""


def _scenario(identifier: str, capability: str, requirements: str, test: str) -> str:
    operations = OPERATIONS if identifier == "create-and-read" else ""
    return (
        f'[[scenarios]]\nid = "{identifier}"\nsummary = "{identifier} of the notes service"\n'
        f'capability = "{capability}"\nlevel = "integration"\nstage = "commit"\n'
        f"requirements = [{requirements}]\n{operations}"
        f'tests = ["integration.test_api::{test}"]\n'
    )


CRUD = "test_a_writer_creates_a_note_and_reads_it_back"
ROWS = "test_notes_are_rows_of_the_sqlite_file"
OPERATIONS = 'operations = ["listNotes", "createNote", "getNote"]\n'
SCENARIOS = (
    ("create-and-read", "http-api", '"http-api.success"', CRUD),
    ("contract", "http-api", '"http-api.contract"', "test_responses_keep_the_published_contract"),
    ("invalid-body", "http-api", '"http-api.invalid-input"', "test_invalid_bodies_are_rejected*"),
    ("missing-note", "http-api", '"http-api.not-found"', "test_a_missing_note_is_not_found"),
    ("reader-refused", "http-api", '"http-api.permission"', "test_readers_cannot_create_notes"),
    ("sqlite-rows", "database", '"database.engine", "database.read-write"', ROWS),
    ("blank-text", "database", '"database.constraints"', "test_the_schema_refuses_blank_text"),
    ("fresh-schema", "migrations", '"migrations.fresh"', "test_migrations_build_a_fresh_schema"),
    ("upgrade", "migrations", '"migrations.upgrade"', "test_a_version_1_database_upgrades"),
    (
        "writers-only",
        "authorization",
        '"authorization.forbidden"',
        "test_readers_cannot_create_notes",
    ),
)
NOT_APPLICABLE = """\
[[not_applicable]]
requirement = "database.transactions"
reason = "Every write is one statement, and each migration is one transaction."

[[not_applicable]]
requirement = "authorization.isolation"
reason = "All notes share one space; there are no tenants."
"""
MANIFEST = "\n".join(
    [
        'schema_version = 1\ncomponent = "notes"\nopenapi = "openapi.json"\n',
        *(_scenario(*scenario) for scenario in SCENARIOS),
        NOT_APPLICABLE,
    ]
)
