"""External documentation links: collected from Markdown, judged from probe results (R05)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from urllib.parse import urlsplit

from quality.diagnostics import Diagnostic, Location, Rule, Severity
from quality.rules.markdown import MarkdownFacts

BROKEN = Rule(
    id="links.external-broken",
    title="External link is gone",
    requirement="R05",
    explanation=(
        "`quality links` probes external http(s) links with timeouts. Only a definite answer "
        "that the page does not exist (HTTP 404 or 410) fails; the check runs separately "
        "from `quality check`, so an unavailable site never blocks an ordinary commit."
    ),
    remedy="Update or remove the link.",
)
UNVERIFIED = Rule(
    id="links.external-unverified",
    title="External link could not be verified",
    requirement="R05",
    explanation=(
        "Timeouts, network errors, refused or rate-limited requests, server errors, and hosts "
        "with non-public addresses are reported as warnings; they say nothing definite about "
        "the link."
    ),
    remedy="Check the link manually if the warning persists.",
    severity=Severity.WARNING,
)
RULES = (BROKEN, UNVERIFIED)

GONE = frozenset({404, 410})
OK_STATUS = range(200, 400)
WEB_SCHEMES = frozenset({"http", "https"})


def external_links(pages: Iterable[tuple[str, MarkdownFacts]]) -> dict[str, list[Location]]:
    """Map each external http(s) URL, without its fragment, to where it is linked."""
    found: dict[str, list[Location]] = {}
    for path, facts in pages:
        for link in facts.links:
            target = link.target.partition("#")[0]
            if urlsplit(target).scheme.lower() in WEB_SCHEMES:
                found.setdefault(target, []).append(Location(path, link.line))
    return dict(sorted(found.items()))


def judge(
    urls: Mapping[str, list[Location]], results: Mapping[str, tuple[int | None, str]]
) -> Iterator[Diagnostic]:
    """Turn probe results (status, detail) into diagnostics at every place a URL is linked."""
    for url, locations in urls.items():
        status, detail = results[url]
        if status in GONE:
            rule, message = BROKEN, f"{url} returned HTTP {status}"
        elif status is None:
            rule, message = UNVERIFIED, f"{url} could not be verified: {detail}"
        elif status not in OK_STATUS:
            rule, message = UNVERIFIED, f"{url} returned HTTP {status}"
        else:
            continue
        for location in locations:
            yield rule.diagnostic(message, location=location)
