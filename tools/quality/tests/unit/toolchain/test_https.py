import http.client
import re
from dataclasses import dataclass, field

import pytest

from quality.toolchain import ManifestError
from quality.toolchain.install import DOWNLOAD_TIMEOUT, MAX_REDIRECTS, https_opener


@dataclass
class FakeResponse:
    status: int
    location: str | None = None
    closed: bool = False

    def getheader(self, name: str) -> str | None:
        return self.location if name == "Location" else None

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeServer:
    routes: dict[str, FakeResponse]
    requests: list[tuple[str, int | None, str]] = field(
        default_factory=list[tuple[str, int | None, str]]
    )
    closed: int = 0

    def connection(self, host: str, port: int | None, *, timeout: float) -> FakeConnection:
        assert timeout == DOWNLOAD_TIMEOUT
        return FakeConnection(self, host, port)


@dataclass
class FakeConnection:
    server: FakeServer
    host: str
    port: int | None
    target: str = ""

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        assert (method, headers) == ("GET", {"User-Agent": "quality-toolchain"})
        self.target = target
        self.server.requests.append((self.host, self.port, target))

    def getresponse(self) -> FakeResponse:
        return self.server.routes[f"{self.host}{self.target}"]

    def close(self) -> None:
        self.server.closed += 1


def serve(monkeypatch: pytest.MonkeyPatch, routes: dict[str, FakeResponse]) -> FakeServer:
    server = FakeServer(routes)
    monkeypatch.setattr(http.client, "HTTPSConnection", server.connection)
    return server


def test_https_downloads_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = FakeResponse(200)
    server = serve(monkeypatch, {"example.invalid/file?x=1": ok, "example.invalid/": ok})
    assert https_opener("https://example.invalid:8443/file?x=1") is ok
    assert https_opener("https://example.invalid") is ok
    assert server.requests == [
        ("example.invalid", 8443, "/file?x=1"),
        ("example.invalid", None, "/"),
    ]
    assert not ok.closed


def test_https_redirects_are_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeResponse(302, "/moved")
    second = FakeResponse(301, "https://mirror.invalid/final")
    final = FakeResponse(200)
    server = serve(
        monkeypatch,
        {"a.invalid/start": first, "a.invalid/moved": second, "mirror.invalid/final": final},
    )
    assert https_opener("https://a.invalid/start") is final
    assert [host + target for host, _, target in server.requests] == [
        "a.invalid/start",
        "a.invalid/moved",
        "mirror.invalid/final",
    ]
    assert first.closed
    assert second.closed
    assert server.closed == 2


@pytest.mark.parametrize(
    "url", ["http://example.invalid/file", "https:///file", "file:///etc/passwd"]
)
def test_non_https_urls_are_refused(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    server = serve(monkeypatch, {})
    with pytest.raises(ManifestError, match="refusing to download from a non-HTTPS URL"):
        https_opener(url)
    assert server.requests == []


def test_a_redirect_to_http_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {"a.invalid/start": FakeResponse(307, "http://a.invalid/plain")})
    with pytest.raises(ManifestError, match=re.escape("non-HTTPS URL: http://a.invalid/plain")):
        https_opener("https://a.invalid/start")


def test_redirect_loops_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, {"a.invalid/loop": FakeResponse(308, "/loop")})
    with pytest.raises(ManifestError, match="too many redirects"):
        https_opener("https://a.invalid/loop")
    assert len(server.requests) == MAX_REDIRECTS + 1


@pytest.mark.parametrize("response", [FakeResponse(404), FakeResponse(302)])
def test_failed_responses_are_closed_and_reported(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse
) -> None:
    server = serve(monkeypatch, {"a.invalid/file": response})
    with pytest.raises(ManifestError) as raised:
        https_opener("https://a.invalid/file")
    assert (
        str(raised.value) == f"download failed with HTTP {response.status}: https://a.invalid/file"
    )
    assert response.closed
    assert server.closed == 1
