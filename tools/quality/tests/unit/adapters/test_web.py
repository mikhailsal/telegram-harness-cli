import http.client
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import SplitResult

import pytest

from quality.adapters import web
from quality.adapters.web import Probe, ProbeError, probe, public_addresses

PUBLIC = "93.184.215.14"
TIMEOUT = 5.0

type Answer = int | tuple[int, str] | BaseException
type Request = tuple[str, str, str, dict[str, str]]


@dataclass
class FakeResponse:
    status: int
    location: str | None = None

    def getheader(self, name: str) -> str | None:
        return self.location if name == "Location" else None


@dataclass
class FakeConnection:
    server: Server
    url: SplitResult
    address: str
    pending: FakeResponse | None = None

    def request(self, method: str, url: str, *, headers: Mapping[str, str]) -> None:
        if self.address in self.server.unreachable:
            raise OSError(101, "Network is unreachable")
        self.server.requests.append((method, self.url.netloc, url, dict(headers)))
        key = f"{self.url.scheme}://{self.url.netloc}{url}"
        answer = self.server.answers.get((method, key), self.server.answers.get(("*", key)))
        match answer:
            case BaseException():
                raise answer
            case (int() as status, str() as location):
                self.pending = FakeResponse(status, location)
            case int() as status:
                self.pending = FakeResponse(status)
            case None:
                self.pending = FakeResponse(404)

    def getresponse(self) -> FakeResponse:
        assert self.pending is not None
        return self.pending

    def close(self) -> None:
        self.server.closed += 1


@dataclass
class Server:
    answers: dict[tuple[str, str], Answer]
    addresses: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    requests: list[Request] = field(default_factory=list[Request])
    connected: list[str] = field(default_factory=list[str])
    unreachable: set[str] = field(default_factory=set[str])
    closed: int = 0

    def resolve(self, host: str, port: int) -> list[str]:
        assert port in {80, 443, 8443}
        return self.addresses.get(host, [PUBLIC])

    def connect(self, url: SplitResult, address: str, timeout: float) -> FakeConnection:
        assert timeout == TIMEOUT
        self.connected.append(address)
        return FakeConnection(self, url, address)

    def probe(self, url: str) -> Probe:
        return probe(url, timeout=TIMEOUT, resolver=self.resolve, connector=self.connect)


def test_a_page_is_probed_with_head_at_the_validated_address() -> None:
    server = Server({("HEAD", "https://example.org/"): 200})
    assert server.probe("https://example.org") == Probe(200)
    assert server.requests == [
        ("HEAD", "example.org", "/", {"Host": "example.org", "User-Agent": "quality-links"})
    ]
    assert server.connected == [PUBLIC]
    assert server.closed == 1


@pytest.mark.parametrize("refusal", [405, 501])
def test_get_is_used_when_head_is_not_supported(refusal: int) -> None:
    server = Server({("HEAD", "http://a.test/x"): refusal, ("GET", "http://a.test/x"): 200})
    assert server.probe("http://a.test/x") == Probe(200)
    assert [request[0] for request in server.requests] == ["HEAD", "GET"]
    assert server.closed == 2


def test_a_refusal_of_both_methods_is_the_final_status() -> None:
    server = Server({("*", "http://a.test/x"): 405})
    assert server.probe("http://a.test/x") == Probe(405)
    assert [request[0] for request in server.requests] == ["HEAD", "GET"]


def test_redirects_are_followed_and_each_hop_is_validated() -> None:
    server = Server(
        {
            ("*", "https://a.test/old"): (301, "/new?x=1"),
            ("*", "https://a.test/new?x=1"): (302, "https://b.test/final"),
            ("*", "https://b.test/final"): 404,
        },
        addresses={"b.test": ["2606:4700:4700::1111"]},
    )
    assert server.probe("https://a.test/old#section") == Probe(404)
    assert server.connected == [PUBLIC, PUBLIC, "2606:4700:4700::1111"]


def test_a_redirect_without_a_location_is_final() -> None:
    assert Server({("*", "https://a.test/"): 302}).probe("https://a.test/") == Probe(302)


def test_redirect_loops_stop() -> None:
    server = Server({("*", "https://a.test/loop"): (307, "/loop")})
    assert server.probe("https://a.test/loop") == Probe(None, "too many redirects")
    assert len(server.requests) == web.MAX_REDIRECTS + 1


@pytest.mark.parametrize(
    "url", ["ftp://a.test/file", "https:///no-host", "mailto:someone@example.org"]
)
def test_unsupported_urls_are_not_contacted(url: str) -> None:
    server = Server({})
    assert server.probe(url) == Probe(None, f"unsupported URL {url}")
    assert server.connected == []


def test_a_redirect_to_another_scheme_is_not_followed() -> None:
    server = Server({("*", "https://a.test/"): (301, "file:///etc/passwd")})
    assert server.probe("https://a.test/") == Probe(None, "unsupported URL file:///etc/passwd")


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.1.2.3"],
        ["169.254.169.254"],
        ["::1"],
        ["fe80::1%eth0"],
        [PUBLIC, "192.168.0.1"],
        ["not-an-address"],
    ],
)
def test_hosts_with_non_public_addresses_are_refused(addresses: list[str]) -> None:
    server = Server({}, addresses={"inside.test": addresses})
    found = server.probe("http://inside.test/admin")
    assert found == Probe(None, "inside.test resolves to a non-public address; not checked")
    assert server.connected == []


def test_unresolvable_hosts_are_unverified() -> None:
    server = Server({}, addresses={"gone.test": []})
    assert server.probe("https://gone.test/") == Probe(None, "gone.test does not resolve")


def test_public_address_raises_for_refused_hosts() -> None:
    with pytest.raises(ProbeError, match=r"^h resolves to a non-public address; not checked$"):
        public_addresses("h", 80, lambda _host, _port: ["127.0.0.1"])
    assert public_addresses("h", 80, lambda _host, _port: [PUBLIC]) == [PUBLIC]


def test_unreachable_addresses_fall_through_in_resolver_order() -> None:
    ipv6 = "2606:4700:4700::1111"
    server = Server({("*", "https://a.test/"): 200}, addresses={"a.test": [ipv6, PUBLIC]})
    server.unreachable.add(ipv6)
    assert server.probe("https://a.test/") == Probe(200)
    assert server.connected == [ipv6, PUBLIC]
    assert server.closed == 2


def test_the_last_address_error_is_reported() -> None:
    ipv6 = "2606:4700:4700::1111"
    server = Server({("*", "https://a.test/"): 200}, addresses={"a.test": [PUBLIC, ipv6]})
    server.unreachable.update({PUBLIC, ipv6})
    found = server.probe("https://a.test/")
    assert found == Probe(None, "OSError: [Errno 101] Network is unreachable")
    assert server.connected == [PUBLIC, ipv6]


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (TimeoutError("timed out"), "TimeoutError: timed out"),
        (http.client.BadStatusLine("junk"), "BadStatusLine: junk"),
        (ValueError("bad"), "ValueError: bad"),
    ],
)
def test_network_errors_are_unverified(error: BaseException, detail: str) -> None:
    server = Server({("*", "https://a.test/"): error})
    assert server.probe("https://a.test/") == Probe(None, detail)
    assert server.closed == 1


def test_resolver_errors_are_unverified() -> None:
    def failing(_host: str, _port: int) -> list[str]:
        raise socket.gaierror(-2, "Name or service not known")

    found = probe("https://a.test/", timeout=TIMEOUT, resolver=failing)
    assert found == Probe(None, "gaierror: [Errno -2] Name or service not known")


def test_ports_queries_and_credentials() -> None:
    server = Server({("*", "https://user:secret@a.test:8443/p?q=1"): 200})
    assert server.probe("https://user:secret@a.test:8443/p?q=1") == Probe(200)
    method, _, path, headers = server.requests[0]
    assert (method, path, headers["Host"]) == ("HEAD", "/p?q=1", "a.test:8443")
