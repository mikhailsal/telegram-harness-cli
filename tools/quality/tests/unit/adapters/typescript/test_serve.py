import http.client
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from quality.adapters.typescript.bundle import Output, collect
from quality.adapters.typescript.serve import Server, absolute_base, serve

FILES = {
    "index.html": "<!doctype html><title>page</title>\n",
    "assets/app.js": "console.log(1);\n",
    "docs/index.html": "<p>docs</p>\n",
}


@pytest.fixture
def output(tmp_path: Path) -> Output:
    for name, text in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return collect(tmp_path, "tree")


def fetch(server: Server, target: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    address = urlsplit(server.origin)
    connection = http.client.HTTPConnection(address.hostname or "", address.port, timeout=10)
    try:
        connection.request(method, target)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_the_build_is_served_from_loopback_on_an_assigned_port(output: Output) -> None:
    with serve(output, "/", "index.html") as server:
        assert server.origin.startswith("http://127.0.0.1:")
        assert server.url == server.origin + "/"
        status, headers, body = fetch(server, "/assets/app.js")
    assert (status, body) == (200, b"console.log(1);\n")
    assert headers["Content-Type"] == "text/javascript"
    assert headers["Cache-Control"] == "no-store"
    assert server.requests.answered == [("/assets/app.js", 200)]


@pytest.mark.parametrize(
    ("target", "body"),
    [
        ("/", FILES["index.html"]),
        ("/docs/", FILES["docs/index.html"]),
        ("/settings/profile?tab=1", FILES["index.html"]),
        ("/settings/", FILES["index.html"]),
        ("/%61ssets/app.js", FILES["assets/app.js"]),
    ],
)
def test_directories_and_client_routes_are_answered_by_entry_points(
    output: Output, target: str, body: str
) -> None:
    with serve(output, "/", "./index.html") as server:
        status, _, data = fetch(server, target)
    assert (status, data.decode()) == (200, body)


@pytest.mark.parametrize("target", ["/logo.svg", "/assets/app.js.map", "/../secret", "/a/../../b"])
def test_a_file_the_build_does_not_have_is_missing(output: Output, target: str) -> None:
    with serve(output, "/", "index.html") as server:
        status, headers, body = fetch(server, target)
        assert server.requests.missing() == (target,)
    assert (status, headers["Content-Length"], body) == (404, "0", b"")


def test_only_paths_below_the_base_are_served(output: Output) -> None:
    with serve(output, "/app", "index.html") as server:
        assert server.url.endswith("/app/")
        inside, _, _ = fetch(server, "/app/assets/app.js")
        outside, _, _ = fetch(server, "/assets/app.js")
        fetch(server, "/assets/app.js")
        assert server.requests.missing() == ("/assets/app.js",)
    assert (inside, outside) == (200, 404)


def test_a_file_that_changed_or_vanished_after_the_build_is_not_served(output: Output) -> None:
    (output.directory / "assets/app.js").write_text("changed\n")
    (output.directory / "docs/index.html").unlink()
    with serve(output, "/", "index.html") as server:
        changed, _, body = fetch(server, "/assets/app.js")
        vanished, _, _ = fetch(server, "/docs/")
        assert server.requests.missing() == ()
    assert (changed, body, vanished) == (500, b"", 500)


def test_head_answers_without_a_body_and_other_methods_are_refused(output: Output) -> None:
    with serve(output, "/", "index.html") as server:
        status, headers, body = fetch(server, "/", "HEAD")
        refused, _, _ = fetch(server, "/", "POST")
    assert (status, body) == (200, b"")
    assert headers["Content-Length"] == str(len(FILES["index.html"]))
    assert refused == 501


def test_an_unknown_type_is_sent_as_bytes(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<p>page</p>\n")
    (tmp_path / "data.qq1").write_text("x")
    with serve(collect(tmp_path, "tree"), "/", "index.html") as server:
        _, headers, _ = fetch(server, "/data.qq1")
    assert headers["Content-Type"] == "application/octet-stream"


def test_the_server_stops_when_the_block_ends_however_it_ends(output: Output) -> None:
    message = "the run failed"
    with pytest.raises(RuntimeError, match=message), serve(output, "/", "index.html") as server:
        raise RuntimeError(message)
    with pytest.raises(OSError):
        fetch(server, "/")


@pytest.mark.parametrize(
    ("base", "expected"),
    [("/", "/"), ("/app", "/app/"), ("/app/", "/app/"), ("./", "/"), ("", "/")],
)
def test_a_relative_base_is_served_from_the_root(base: str, expected: str) -> None:
    assert absolute_base(base) == expected
