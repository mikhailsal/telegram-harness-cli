"""The accessibility scans browser tests attach, judged by the gate (decision 0018).

A test proves accessibility by attaching the raw results of an
`@axe-core/playwright` scan as `accessibility-scan`, after asserting that the
interface it scans is present. A scan counts only when it is the one the gate
expects: axe-core at the locked version, run on the tags of the policy or
more, with no rule turned off, on a page of the gate's server that had
content, that is, a checked element other than the document and its body.
Anything else is a `typescript.accessibility-scan` error and no evidence.

Violations of a valid scan are `typescript.accessibility` findings, one per
axe rule and target: serious, critical, and unrated ones are errors, moderate
and minor ones warnings. The checks axe could not decide are reported for
manual review. Scans are evidence for the selected rules, not a conformance
claim.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from quality.boundedio import JsonValue, ReportError, load_json
from quality.diagnostics import Diagnostic, Location, Rule, Severity

ACCESSIBILITY = Rule(
    id="typescript.accessibility",
    title="An accessibility scan of a browser scenario found a violation",
    requirement="R06",
    explanation=(
        "axe-core checks the rendered page against the policy's WCAG tags. Serious and "
        "critical violations keep people who use assistive technology from the interface."
    ),
    remedy=(
        "Fix the markup the scan names: an accessible name, a label, contrast, a role, or the "
        "order of headings and landmarks."
    ),
)
SCAN = Rule(
    id="typescript.accessibility-scan",
    title="A browser scenario attached an accessibility scan the gate cannot accept",
    requirement="R06",
    explanation=(
        "A scan proves accessibility only when it ran the locked axe-core with the policy's "
        "tags and every rule on a rendered page of the gate's server. A scan of an empty "
        "page, of another origin, or with rules turned off proves nothing."
    ),
    remedy=(
        "Assert that the interface is visible, then attach the results of "
        "`new AxeBuilder({ page }).withTags(tags).analyze()` as `accessibility-scan`."
    ),
)
RULES = (ACCESSIBILITY, SCAN)

ATTACHMENT = "accessibility-scan"
ENGINE = "axe-core"
# Targets that show no content: none, the document, and its body.
NO_CONTENT = frozenset({"", "html", "body"})
WARNINGS = frozenset({"moderate", "minor"})
GROUPS = ("violations", "incomplete", "passes")
SCAN_LIMIT = 64 * 1024 * 1024

type Entry = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ScanPolicy:
    """What a valid scan must show: the locked axe-core, the tags, and the server."""

    version: str
    tags: frozenset[str]
    origin: str


@dataclass(frozen=True, slots=True)
class Scan:
    """One judged scan: why it is invalid, or its findings."""

    problem: str | None
    findings: tuple[Diagnostic, ...] = ()


def _object(value: JsonValue) -> Entry:
    return value if isinstance(value, dict) else {}


def _list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _target(value: JsonValue) -> str:
    """Render an axe target: selectors through frames, each through shadow roots."""
    parts = [
        " >>> ".join(str(item) for item in _list(part)) if isinstance(part, list) else str(part)
        for part in _list(value)
    ]
    return " | ".join(parts)


def _items(results: Entry, group: str) -> Iterator[tuple[Entry, list[str]]]:
    for item in _list(results.get(group)):
        entry = _object(item)
        nodes = [_target(_object(node).get("target")) for node in _list(entry.get("nodes"))]
        yield entry, nodes


def _engine(results: Entry, policy: ScanPolicy) -> str | None:
    engine = _object(results.get("testEngine"))
    if engine.get("name") == ENGINE and engine.get("version") == policy.version:
        return None
    found = f"{engine.get('name')} {engine.get('version')}"
    return f"the scan ran {found}, not the locked {ENGINE} {policy.version}"


def _tags(results: Entry, policy: ScanPolicy) -> str | None:
    only = _object(_object(results.get("toolOptions")).get("runOnly"))
    tags = {str(tag) for tag in _list(only.get("values"))}
    if only.get("type") == "tag" and policy.tags <= tags:
        return None
    return f"the scan must run the tags {', '.join(sorted(policy.tags))}"


def _rules(results: Entry, _policy: ScanPolicy) -> str | None:
    rules = _object(_object(results.get("toolOptions")).get("rules"))
    off = sorted(name for name, value in rules.items() if _object(value).get("enabled") is False)
    return f"the scan turned off the rules {', '.join(off)}" if off else None


def _origin(results: Entry, policy: ScanPolicy) -> str | None:
    url = results.get("url")
    parts = urlsplit(url if isinstance(url, str) else "")
    if f"{parts.scheme}://{parts.netloc}" == policy.origin:
        return None
    return "the scan did not check a page of the gate's server"


def _content(results: Entry, _policy: ScanPolicy) -> str | None:
    targets = (
        target for group in GROUPS for _, nodes in _items(results, group) for target in nodes
    )
    if all(target in NO_CONTENT for target in targets):
        return "the scanned page had no content"
    return None


# What a valid scan shows, in the order the reasons are given.
CHECKS = (_engine, _tags, _rules, _origin, _content)


def problem(results: Entry, policy: ScanPolicy) -> str | None:
    """Return why the results are not a scan the gate accepts, or None."""
    reasons = (check(results, policy) for check in CHECKS)
    return next((reason for reason in reasons if reason is not None), None)


def _findings(results: Entry, component: str, file: str, test: str) -> Iterator[Diagnostic]:
    location = Location(file)
    for entry, nodes in _items(results, "violations"):
        rule, impact = str(entry.get("id")), entry.get("impact")
        severity = Severity.WARNING if impact in WARNINGS else Severity.ERROR
        for target in nodes:
            yield ACCESSIBILITY.diagnostic(
                f"{test}: {rule} ({impact}) at {target}: {entry.get('help')}",
                location=location,
                component=component,
                severity=severity,
                data={"symbol": f"{rule} {target}"},
            )
    for entry, nodes in _items(results, "incomplete"):
        rule = str(entry.get("id"))
        yield ACCESSIBILITY.diagnostic(
            f"{test}: {rule} needs manual review on {len(nodes)} element(s): {entry.get('help')}",
            location=location,
            component=component,
            severity=Severity.INFO,
        )


def judge(path: Path, policy: ScanPolicy, *, component: str, file: str, test: str) -> Scan:
    """Judge one attached scan of a test in a repository file."""
    try:
        results = _object(load_json(path, SCAN_LIMIT))
    except ReportError as error:
        return Scan(f"the scan cannot be read: {error}")
    reason = problem(results, policy)
    if reason is not None:
        return Scan(reason)
    return Scan(None, tuple(_findings(results, component, file, test)))


def invalid(component: str, file: str, test: str, reason: str) -> Diagnostic:
    """Report a scan the gate cannot accept."""
    return SCAN.diagnostic(f"{test}: {reason}", location=Location(file), component=component)
