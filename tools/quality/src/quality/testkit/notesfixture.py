"""The `notes` reference fixture: an HTTP service on a real SQLite database (R08).

The standard library serves a JSON API whose operations an OpenAPI document
publishes; numbered SQL migrations build and upgrade the schema, and only
writers may create notes. The integration suite drives the real server and
database file; the manifest maps each capability requirement to those tests.
"""

from __future__ import annotations

from types import MappingProxyType

from quality.testkit.notestests import CONFTEST, INTEGRATION_TESTS, MANIFEST, OPENAPI, UNIT_TESTS
from quality.testkit.policies import ComponentSpec

SPEC = ComponentSpec(
    id="notes",
    root="notes",
    integration=("integration",),
    capabilities=("http-api", "database", "migrations", "authorization"),
    scenarios="scenarios.toml",
)

STORE = '''\
"""Notes in SQLite; numbered migrations build and upgrade the schema."""

import sqlite3
from contextlib import closing
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
COLUMNS = "id, text, author"


def connect(database: Path) -> sqlite3.Connection:
    """Open the database with rows addressable by column name."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def migrate(database: Path) -> int:
    """Apply the migrations newer than the schema; return the schema version."""
    with closing(connect(database)) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        for script in sorted(MIGRATIONS.glob("*.sql")):
            if int(script.name.split("_", 1)[0]) > version:
                db.executescript(script.read_text(encoding="utf-8"))
        return int(db.execute("PRAGMA user_version").fetchone()[0])


def list_notes(database: Path) -> list[dict[str, object]]:
    """Return every note, oldest first."""
    with closing(connect(database)) as db:
        rows = db.execute(f"SELECT {COLUMNS} FROM notes ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def get_note(database: Path, note: int) -> dict[str, object] | None:
    """Return one note, or None when there is none with that ID."""
    with closing(connect(database)) as db:
        row = db.execute(f"SELECT {COLUMNS} FROM notes WHERE id = ?", (note,)).fetchone()
    return dict(row) if row is not None else None


def create_note(database: Path, text: str, author: str) -> dict[str, object]:
    """Store a note and return it."""
    with closing(connect(database)) as db, db:
        cursor = db.execute("INSERT INTO notes (text, author) VALUES (?, ?)", (text, author))
    return {"id": cursor.lastrowid, "text": text, "author": author}
'''

SERVER = '''\
"""A JSON HTTP API for notes on the standard library's server."""

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from notes.store import create_note, get_note, list_notes

NOTE = re.compile(r"/notes/([0-9]+)")
WRITER = "writer"
MAX_BODY = 4096


class NotesServer(ThreadingHTTPServer):
    """Serve the API on loopback; port 0 picks a free port."""

    def __init__(self, database: Path, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), NotesHandler)
        self.database = database


class NotesHandler(BaseHTTPRequestHandler):
    """Serve listNotes, createNote, and getNote."""

    def log_message(self, format: str, *args: object) -> None:
        """Keep the server quiet."""

    @property
    def database(self) -> Path:
        """The database of the server."""
        return cast(NotesServer, self.server).database

    def do_GET(self) -> None:
        """List the notes or fetch one."""
        if self.path == "/notes":
            self.reply(HTTPStatus.OK, {"notes": list_notes(self.database)})
            return
        found = NOTE.fullmatch(self.path)
        note = get_note(self.database, int(found.group(1))) if found else None
        if note is None:
            self.reply(HTTPStatus.NOT_FOUND, {"error": "no such note"})
        else:
            self.reply(HTTPStatus.OK, note)

    def do_POST(self) -> None:
        """Create a note; only writers may."""
        if self.path != "/notes":
            self.reply(HTTPStatus.NOT_FOUND, {"error": "no such resource"})
        elif self.headers.get("X-Role") != WRITER:
            self.reply(HTTPStatus.FORBIDDEN, {"error": "only writers create notes"})
        elif (text := self.text()) is None:
            self.reply(HTTPStatus.BAD_REQUEST, {"error": "the body needs a non-blank text"})
        else:
            author = self.headers.get("X-User") or "anonymous"
            self.reply(HTTPStatus.CREATED, create_note(self.database, text, author))

    def text(self) -> str | None:
        """Return the non-blank text of a JSON body, or None."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body: object = json.loads(self.rfile.read(min(length, MAX_BODY)))
        except ValueError:
            return None
        text = cast(dict[str, object], body).get("text") if isinstance(body, dict) else None
        return text if isinstance(text, str) and text.strip() else None

    def reply(self, status: HTTPStatus, body: object) -> None:
        """Send a JSON response."""
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
'''

CREATE = """\
BEGIN;
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL CHECK (length(trim(text)) > 0)
);
PRAGMA user_version = 1;
COMMIT;
"""

AUTHOR = """\
BEGIN;
ALTER TABLE notes ADD COLUMN author TEXT NOT NULL DEFAULT 'anonymous';
PRAGMA user_version = 2;
COMMIT;
"""

CLIENT = '''\
"""A client of the notes API, and a server running beside the caller."""

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from notes.server import NotesServer


class Client:
    """Call the API at `base`, such as `http://127.0.0.1:8080`."""

    def __init__(self, base: str) -> None:
        self.base = base

    def call(
        self, method: str, path: str, body: bytes | None = None, **headers: str
    ) -> tuple[int, object]:
        """Send a request; return the status and the decoded JSON body."""
        request = Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def create(self, text: str, **headers: str) -> tuple[int, object]:
        """Create a note, as a writer unless the headers say otherwise."""
        body = json.dumps({"text": text}).encode()
        return self.call("POST", "/notes", body, **{"X-Role": "writer", **headers})


@contextmanager
def serving(database: Path) -> Generator[Client]:
    """Serve `database` on a free loopback port and yield a client of it."""
    server = NotesServer(database)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield Client(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
'''


FILES = MappingProxyType(
    {
        "src/notes/__init__.py": "",
        "src/notes/store.py": STORE,
        "src/notes/server.py": SERVER,
        "src/notes/migrations/0001_create_notes.sql": CREATE,
        "src/notes/migrations/0002_add_author.sql": AUTHOR,
        "src/notes/client.py": CLIENT,
        "tests/conftest.py": CONFTEST,
        "integration/conftest.py": CONFTEST,
        "tests/test_notes.py": UNIT_TESTS,
        "integration/test_api.py": INTEGRATION_TESTS,
        "openapi.json": OPENAPI,
        "scenarios.toml": MANIFEST,
    }
)
