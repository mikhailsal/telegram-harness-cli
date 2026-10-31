import http.client
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import pytest

import quality_launcher_uv
from fakes import BINARY, MEMBER, RELEASE, Server, no_network, release, tarball
from quality_launcher_uv import (
    DOWNLOAD_TIMEOUT,
    MAX_REDIRECTS,
    LauncherError,
    Opener,
    Uv,
    executable,
    https_open,
    install_uv,
)


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
class FakeHttps:
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
    server: FakeHttps
    host: str
    port: int | None
    target: str = ""

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        assert (method, headers) == ("GET", {"User-Agent": "quality-launcher"})
        self.target = target
        self.server.requests.append((self.host, self.port, target))

    def getresponse(self) -> FakeResponse:
        return self.server.routes[f"{self.host}{self.target}"]

    def close(self) -> None:
        self.server.closed += 1


def serve(monkeypatch: pytest.MonkeyPatch, routes: dict[str, FakeResponse]) -> FakeHttps:
    server = FakeHttps(routes)
    monkeypatch.setattr(http.client, "HTTPSConnection", server.connection)
    return server


def test_https_downloads_are_returned_open(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = FakeResponse(200)
    server = serve(monkeypatch, {"example.invalid/file?x=1": ok, "example.invalid/": ok})
    assert https_open("https://example.invalid:8443/file?x=1") is ok
    assert https_open("https://example.invalid") is ok
    assert server.requests == [
        ("example.invalid", 8443, "/file?x=1"),
        ("example.invalid", None, "/"),
    ]
    assert (ok.closed, server.closed) == (False, 0)


def test_https_redirects_are_followed_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeResponse(302, "/moved")
    second = FakeResponse(301, "https://mirror.invalid/final")
    final = FakeResponse(200)
    routes = {"a.invalid/start": first, "a.invalid/moved": second, "mirror.invalid/final": final}
    server = serve(monkeypatch, routes)
    assert https_open("https://a.invalid/start") is final
    assert [target for _, _, target in server.requests] == ["/start", "/moved", "/final"]
    assert (first.closed, second.closed, server.closed) == (True, True, 2)


@pytest.mark.parametrize("url", ["http://example.invalid/uv.tar.gz", "https:///uv.tar.gz"])
def test_only_https_urls_are_opened(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    server = serve(monkeypatch, {})
    with pytest.raises(LauncherError, match="refusing to download from a non-HTTPS URL"):
        https_open(url)
    assert server.requests == []


def test_a_redirect_to_http_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {"a.invalid/start": FakeResponse(307, "http://a.invalid/plain")})
    with pytest.raises(LauncherError, match=r"non-HTTPS URL: http://a\.invalid/plain"):
        https_open("https://a.invalid/start")


@pytest.mark.parametrize("response", [FakeResponse(404), FakeResponse(302)])
def test_failed_responses_are_closed_and_reported(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse
) -> None:
    server = serve(monkeypatch, {"a.invalid/uv": response})
    with pytest.raises(LauncherError, match=f"download failed with HTTP {response.status}"):
        https_open("https://a.invalid/uv")
    assert (response.closed, server.closed) == (True, 1)


def test_redirects_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, {"a.invalid/loop": FakeResponse(308, "/loop")})
    with pytest.raises(LauncherError, match="too many redirects"):
        https_open("https://a.invalid/loop")
    assert len(server.requests) == MAX_REDIRECTS + 1


def test_uv_is_installed_from_a_verified_archive(tmp_path: Path) -> None:
    server = Server()
    path = install_uv(release(), tmp_path, "x86_64", server)
    assert path == tmp_path / "toolchains" / "uv-9.9.9" / "uv"
    assert path.read_bytes() == BINARY
    assert path.stat().st_mode & 0o777 == 0o755
    assert sorted(p.name for p in (tmp_path / "toolchains").rglob("*")) == ["uv", "uv-9.9.9"]
    assert install_uv(release(), tmp_path, "x86_64", no_network) == path
    assert len(server.downloads) == 1


def test_a_stale_installation_is_replaced(tmp_path: Path) -> None:
    stale = tmp_path / "toolchains" / "uv-9.9.9"
    stale.mkdir(parents=True)
    (stale / "uv").write_bytes(b"partial")
    (stale / "leftover").write_text("x", encoding="utf-8")
    assert not executable(stale / "uv")
    path = install_uv(release(), tmp_path, "x86_64", Server())
    assert path.read_bytes() == BINARY
    assert sorted(p.name for p in stale.iterdir()) == ["uv"]
    assert [p.name for p in (tmp_path / "toolchains").iterdir()] == ["uv-9.9.9"]


def failing(error: OSError) -> Opener:
    def opener(url: str) -> IO[bytes]:
        raise error

    return opener


@pytest.mark.parametrize(
    ("uv", "opener", "message"),
    [
        (release(sha256="0" * 64), Server(), "checksum mismatch: expected 0000"),
        (release(), failing(ConnectionResetError("reset by peer")), "reset by peer"),
        (release(member="uv-x86_64/missing"), Server(), "uv-x86_64/missing"),
        (release(b"not a tar archive"), Server(b"not a tar archive"), "file could not be opened"),
    ],
)
def test_a_failed_installation_leaves_nothing_behind(
    tmp_path: Path, uv: Uv, opener: Opener, message: str
) -> None:
    with pytest.raises(LauncherError, match=f"cannot install uv 9.9.9: .*{message}"):
        install_uv(uv, tmp_path, "x86_64", opener)
    assert list((tmp_path / "toolchains").iterdir()) == []


def test_symbolic_link_members_are_never_followed(tmp_path: Path) -> None:
    data = tarball((MEMBER, None))
    with pytest.raises(LauncherError, match=f"archive member {MEMBER} is not a regular file"):
        install_uv(release(data), tmp_path, "x86_64", Server(data))
    assert list((tmp_path / "toolchains").iterdir()) == []


def test_downloads_over_the_limit_are_abandoned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(quality_launcher_uv, "CHUNK", 64)
    monkeypatch.setattr(quality_launcher_uv, "DOWNLOAD_LIMIT", 100)
    reads: list[int] = []

    class Counting(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            reads.append(size or 0)
            return super().read(size)

    def opener(url: str) -> IO[bytes]:
        return Counting(RELEASE)

    with pytest.raises(LauncherError, match="download exceeds 100 bytes"):
        install_uv(release(), tmp_path, "x86_64", opener)
    assert reads == [64, 64]


def test_an_architecture_without_an_artifact_fails(tmp_path: Path) -> None:
    with pytest.raises(LauncherError, match=r"uv 9\.9\.9 has no artifact for aarch64"):
        install_uv(release(), tmp_path, "aarch64", no_network)
    assert not (tmp_path / "toolchains").exists()


def test_executables_are_regular_files_with_the_execute_bit(tmp_path: Path) -> None:
    script = tmp_path / "tool"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    assert not executable(script)
    script.chmod(0o755)
    assert executable(script)
    assert not executable(tmp_path)
    assert not executable(tmp_path / "missing")
