"""The gate's own server of a production build (decision 0018).

The end-to-end suites of a browser component run against exactly the output
the gate built: a static server in the core's process, on `127.0.0.1` and a
port the kernel assigns, so an occupied port or another application cannot
answer for it. It serves only the files the build's evidence lists, below the
resolved `base`, and only while their SHA-256 still matches the evidence. A
directory or a path without an extension that names no file is the entry
point, for client routing; any other unknown path is a 404. Every request is
recorded with its status before it is answered. The server stops when the
block that started it ends, also on a failure or an interruption, and never
touches another process.
"""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import secrets
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast, override
from urllib.parse import unquote, urlsplit

from quality.adapters.typescript.bundle import Output

HOST = "127.0.0.1"
INDEX = "index.html"
FALLBACK_TYPE = "application/octet-stream"


@dataclass(slots=True)
class Requests:
    """Each request the server answered, in order, as a path and a status."""

    answered: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    documents: int = 0
    tokens: list[str] = field(default_factory=list[str])
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, path: str, status: int, *, document: bool = False) -> str | None:
        """Record one answered request."""
        with self.lock:
            self.answered.append((path, status))
            if document and status == HTTPStatus.OK:
                self.documents += 1
                token = secrets.token_hex(16)
                self.tokens.append(token)
                return token
        return None

    def missing(self) -> tuple[str, ...]:
        """Return each path the server had no file for, once, in the order first requested."""
        with self.lock:
            return tuple(dict.fromkeys(p for p, s in self.answered if s == HTTPStatus.NOT_FOUND))


@dataclass(frozen=True, slots=True)
class Site:
    """What the server serves: the build's output below `base`, and the entry point."""

    output: Output
    base: str
    entry: str

    def resolve(self, target: str) -> str | None:
        """Return the output file a request path names, the entry point, or None."""
        path = unquote(urlsplit(target).path)
        if not path.startswith(self.base):
            return None
        relative = path[len(self.base) :]
        directory = relative == "" or relative.endswith("/")
        normal = posixpath.normpath(relative + INDEX if directory else relative)
        if normal in self.output.files:
            return normal
        route = directory or not posixpath.splitext(posixpath.basename(normal))[1]
        return self.entry if route and not normal.startswith("..") else None

    def read(self, name: str) -> bytes | None:
        """Read an output file, or None when it no longer matches the build's evidence."""
        try:
            data = (self.output.directory / name).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == self.output.files.get(name) else None


class SiteServer(ThreadingHTTPServer):
    """Serve a site on loopback, on a port the kernel assigns, and record each request."""

    daemon_threads = True

    def __init__(self, site: Site) -> None:
        super().__init__((HOST, 0), SiteHandler)
        self.site = site
        self.requests = Requests()


class SiteHandler(BaseHTTPRequestHandler):
    """Answer GET and HEAD from the site; every other method is refused."""

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Record requests instead of logging them to standard error."""

    def _answer(self, *, body: bool) -> None:
        server = cast(SiteServer, self.server)
        name = server.site.resolve(self.path)
        data = None if name is None else server.site.read(name)
        status = HTTPStatus.OK
        if name is None:
            status = HTTPStatus.NOT_FOUND
        elif data is None:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        document = body and name is not None and name.endswith(".html")
        token = server.requests.add(self.path, status, document=document)
        self.send_response(status)
        self._headers(name, data, token)
        if body and data is not None:
            self.wfile.write(data)

    def _headers(self, name: str | None, data: bytes | None, token: str | None) -> None:
        """Send content metadata and the owned document token for one response."""
        if token is not None:
            self.send_header("X-Quality-Document", token)
        if data is None or name is None:
            self.send_header("Content-Length", "0")
        else:
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or FALLBACK_TYPE)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        """Serve a file of the build, the entry point, or a 404."""
        self._answer(body=True)

    def do_HEAD(self) -> None:
        """Answer like GET without the body."""
        self._answer(body=False)


@dataclass(frozen=True, slots=True)
class Server:
    """A running server: its address and what it answered."""

    origin: str
    base: str
    requests: Requests

    @property
    def url(self) -> str:
        """The URL of the application: the origin and the build's `base`."""
        return self.origin + self.base


def absolute_base(base: str) -> str:
    """Return the path a build is served below: its absolute `base`, else the root."""
    if not base.startswith("/"):
        return "/"
    return base if base.endswith("/") else base + "/"


@contextmanager
def serve(output: Output, base: str, entry: str) -> Generator[Server]:
    """Serve the output on loopback while the block runs; stop the server however it ends."""
    site = Site(output, absolute_base(base), posixpath.normpath(entry))
    server = SiteServer(site)
    thread = threading.Thread(target=server.serve_forever, name="quality-serve", daemon=True)
    thread.start()
    try:
        yield Server(f"http://{HOST}:{server.server_address[1]}", site.base, server.requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
