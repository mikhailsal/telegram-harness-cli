"""HTTP probes of external documentation links: public hosts only, no response bodies."""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import SplitResult, urljoin, urlsplit

MAX_REDIRECTS = 5
REDIRECTS = frozenset({301, 302, 303, 307, 308})
HEAD_UNSUPPORTED = frozenset({405, 501})
DEFAULT_PORTS = {"http": 80, "https": 443}
USER_AGENT = "quality-links"


@dataclass(frozen=True, slots=True)
class Probe:
    """The final HTTP status of a URL, or why it could not be verified."""

    status: int | None
    detail: str = ""


class ProbeError(Exception):
    """A URL cannot be probed safely."""


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection to an already validated address, verified against the host name."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        self.tls = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self.tls)
        self.address = address

    def connect(self) -> None:
        """Connect to the pinned address so a second DNS answer cannot redirect the probe."""
        raw = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self.tls.wrap_socket(raw, server_hostname=self.host)


class Response(Protocol):
    """The part of an HTTP response a probe reads; never the body."""

    @property
    def status(self) -> int:
        """The HTTP status code."""
        ...

    def getheader(self, name: str) -> str | None:
        """Return a response header."""
        ...


class Connection(Protocol):
    """The part of an HTTP connection a probe uses."""

    def request(self, method: str, url: str, *, headers: Mapping[str, str]) -> None:
        """Send a request."""
        ...

    def getresponse(self) -> Response:
        """Read the status line and headers."""
        ...

    def close(self) -> None:
        """Close the connection."""
        ...


type Resolver = Callable[[str, int], list[str]]
type Connector = Callable[[SplitResult, str, float], Connection]


def resolve(host: str, port: int) -> list[str]:
    """Return every address of a host, in the resolver's order of preference."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def connect(url: SplitResult, address: str, timeout: float) -> Connection:
    """Open a connection to `address` for the URL's scheme and host."""
    port = url.port or DEFAULT_PORTS[url.scheme]
    if url.scheme == "https":
        return PinnedHTTPSConnection(url.hostname or "", port, address, timeout)
    return http.client.HTTPConnection(address, port, timeout=timeout)


def _global(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def public_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    """Return the host's addresses, refusing hosts with any non-public address."""
    addresses = resolver(host, port)
    if not addresses:
        msg = f"{host} does not resolve"
        raise ProbeError(msg)
    if not all(_global(address) for address in addresses):
        msg = f"{host} resolves to a non-public address; not checked"
        raise ProbeError(msg)
    return addresses


def _ask(
    url: SplitResult, address: str, timeout: float, connector: Connector
) -> tuple[int, str | None]:
    host = url.netloc.rpartition("@")[2]
    path = (url.path or "/") + (f"?{url.query}" if url.query else "")
    status, location = 0, None
    for method in ("HEAD", "GET"):
        connection = connector(url, address, timeout)
        try:
            connection.request(method, path, headers={"Host": host, "User-Agent": USER_AGENT})
            response = connection.getresponse()
            status, location = response.status, response.getheader("Location")
        finally:
            connection.close()
        if status not in HEAD_UNSUPPORTED:
            break
    return status, location


def _ask_each(
    url: SplitResult, addresses: list[str], timeout: float, connector: Connector
) -> tuple[int, str | None]:
    """Try the validated addresses in order, as a socket connect would; the last error counts."""
    *others, last = addresses
    for address in others:
        with contextlib.suppress(OSError):
            return _ask(url, address, timeout, connector)
    return _ask(url, last, timeout, connector)


def probe(
    url: str,
    *,
    timeout: float,
    resolver: Resolver = resolve,
    connector: Connector = connect,
) -> Probe:
    """Follow a URL through at most a few redirects and return its final status."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in DEFAULT_PORTS or not parts.hostname:
            return Probe(None, f"unsupported URL {url}")
        try:
            port = parts.port or DEFAULT_PORTS[parts.scheme]
            addresses = public_addresses(parts.hostname, port, resolver)
            status, location = _ask_each(parts, addresses, timeout, connector)
        except ProbeError as error:
            return Probe(None, str(error))
        except (OSError, ValueError, http.client.HTTPException) as error:
            return Probe(None, f"{type(error).__name__}: {error}")
        if status in REDIRECTS and location:
            url = urljoin(url, location)
            continue
        return Probe(status)
    return Probe(None, "too many redirects")
