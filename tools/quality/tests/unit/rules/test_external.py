from quality.diagnostics import Location, Severity
from quality.rules.external import external_links, judge
from quality.rules.markdown import parse_markdown

PAGE = """\
# Links

- [site](https://example.org/a#part) and [again](https://example.org/a)
- [plain](http://example.org/b), [mail](mailto:x@example.org), [local](other.md)
- <https://example.org/c>
- [upper](HTTPS://example.org/d)
"""


def test_external_links_are_collected_without_fragments() -> None:
    found = external_links(
        [
            ("docs/a.md", parse_markdown(PAGE)),
            ("README.md", parse_markdown("[x](http://example.org/b)\n")),
        ]
    )
    assert found == {
        "HTTPS://example.org/d": [Location("docs/a.md", 6)],
        "http://example.org/b": [Location("docs/a.md", 4), Location("README.md", 1)],
        "https://example.org/a": [Location("docs/a.md", 3), Location("docs/a.md", 3)],
        "https://example.org/c": [Location("docs/a.md", 5)],
    }


def test_only_definite_absence_fails() -> None:
    urls = {
        f"https://example.org/{status}": [Location("a.md", 1)]
        for status in ("200", "301", "403", "404", "410", "429", "500", "none")
    }
    results = {
        url: (None, "TimeoutError: timed out") if url.endswith("none") else (int(url[-3:]), "")
        for url in urls
    }
    found = [(d.message, d.severity) for d in judge(urls, results)]
    assert found == [
        ("https://example.org/403 returned HTTP 403", Severity.WARNING),
        ("https://example.org/404 returned HTTP 404", Severity.ERROR),
        ("https://example.org/410 returned HTTP 410", Severity.ERROR),
        ("https://example.org/429 returned HTTP 429", Severity.WARNING),
        ("https://example.org/500 returned HTTP 500", Severity.WARNING),
        (
            "https://example.org/none could not be verified: TimeoutError: timed out",
            Severity.WARNING,
        ),
    ]


def test_every_place_of_a_broken_link_is_reported() -> None:
    locations = [Location("a.md", 3), Location("b.md", 7)]
    found = list(judge({"https://gone.test/": locations}, {"https://gone.test/": (404, "")}))
    assert [(d.rule, d.location) for d in found] == [
        ("links.external-broken", locations[0]),
        ("links.external-broken", locations[1]),
    ]
