import http.client
import socket
from urllib.parse import urlsplit

import pytest

from quality.adapters import web
from quality.adapters.web import PinnedHTTPSConnection

PUBLIC = "93.184.215.14"
TIMEOUT = 5.0


def test_resolve_keeps_the_resolver_order_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
    ]

    def getaddrinfo(*_args: object, **_kwargs: object) -> list[object]:
        return list(infos)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    assert web.resolve("a.test", 443) == ["192.0.2.2", "192.0.2.1", "2001:db8::1"]


def test_connections_target_the_validated_address() -> None:
    plain = web.connect(urlsplit("http://a.test/x"), PUBLIC, TIMEOUT)
    assert isinstance(plain, http.client.HTTPConnection)
    assert (plain.host, plain.port, plain.timeout) == (PUBLIC, 80, TIMEOUT)
    secure = web.connect(urlsplit("https://a.test:8443/x"), PUBLIC, TIMEOUT)
    assert isinstance(secure, PinnedHTTPSConnection)
    assert (secure.host, secure.port, secure.address) == ("a.test", 8443, PUBLIC)


class FakeSocket:
    def close(self) -> None:
        pass


def test_tls_is_verified_against_the_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = FakeSocket()
    connected: list[tuple[tuple[str, int], float | None]] = []
    wrapped: list[tuple[object, str | None]] = []

    def create_connection(address: tuple[str, int], timeout: float | None) -> FakeSocket:
        connected.append((address, timeout))
        return raw

    def wrap_socket(sock: object, server_hostname: str | None = None) -> FakeSocket:
        wrapped.append((sock, server_hostname))
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", create_connection)
    connection = PinnedHTTPSConnection("a.test", 443, PUBLIC, TIMEOUT)
    monkeypatch.setattr(connection.tls, "wrap_socket", wrap_socket)
    connection.connect()
    assert connected == [((PUBLIC, 443), TIMEOUT)]
    assert wrapped == [(raw, "a.test")]
    assert connection.tls.check_hostname
