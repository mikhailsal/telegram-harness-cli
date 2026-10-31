import json
from pathlib import Path

import pytest

from quality.adapters.typescript import scans
from quality.adapters.typescript.scans import Scan, ScanPolicy, judge, problem
from quality.boundedio import JsonValue
from quality.diagnostics import Location, Severity

ORIGIN = "http://127.0.0.1:41234"
POLICY = ScanPolicy("4.11.0", frozenset({"wcag2a", "wcag2aa"}), ORIGIN)


def node(*target: JsonValue) -> JsonValue:
    return {"target": list(target)}


def results(**fields: JsonValue) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {
        "testEngine": {"name": "axe-core", "version": "4.11.0"},
        "toolOptions": {"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "best"]}},
        "url": f"{ORIGIN}/signup",
        "violations": [],
        "incomplete": [],
        "passes": [{"id": "button-name", "nodes": [node("html"), node("#send")]}],
    }
    return {**document, **fields}


def test_a_scan_of_the_page_with_the_locked_engine_and_the_tags_is_accepted() -> None:
    assert problem(results(), POLICY) is None
    rules: JsonValue = {"color-contrast": {"enabled": True}, "region": {}}
    assert (
        problem(
            results(
                toolOptions={
                    "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa"]},
                    "rules": rules,
                }
            ),
            POLICY,
        )
        is None
    )


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"testEngine": None}, "the scan ran None None, not the locked axe-core 4.11.0"),
        (
            {"testEngine": {"name": "axe-core", "version": "4.10.0"}},
            "the scan ran axe-core 4.10.0, not the locked axe-core 4.11.0",
        ),
        ({"toolOptions": {}}, "the scan must run the tags wcag2a, wcag2aa"),
        (
            {"toolOptions": {"runOnly": {"type": "rule", "values": ["wcag2a", "wcag2aa"]}}},
            "the scan must run the tags wcag2a, wcag2aa",
        ),
        (
            {"toolOptions": {"runOnly": {"type": "tag", "values": ["wcag2a"]}}},
            "the scan must run the tags wcag2a, wcag2aa",
        ),
        (
            {
                "toolOptions": {
                    "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa"]},
                    "rules": {"region": {"enabled": False}, "label": {"enabled": False}},
                }
            },
            "the scan turned off the rules label, region",
        ),
        ({"url": "http://localhost:5173/"}, "the scan did not check a page of the gate's server"),
        ({"url": None}, "the scan did not check a page of the gate's server"),
        ({"passes": []}, "the scanned page had no content"),
        (
            {
                "passes": [{"id": "html-has-lang", "nodes": [node("html")]}],
                "incomplete": [{"id": "region", "nodes": [node("body"), "broken"]}],
                "violations": "broken",
            },
            "the scanned page had no content",
        ),
    ],
)
def test_a_scan_the_gate_cannot_accept_names_its_problem(
    fields: dict[str, JsonValue], reason: str
) -> None:
    assert problem(results(**fields), POLICY) == reason


def write(path: Path, data: JsonValue) -> Path:
    path.write_text(json.dumps(data))
    return path


VIOLATIONS: list[JsonValue] = [
    {
        "id": "image-alt",
        "impact": "critical",
        "help": "Images must have alternative text",
        "nodes": [node("img"), node("iframe", ["x-card", "img.logo"])],
    },
    {
        "id": "region",
        "impact": "moderate",
        "help": "Content in landmarks",
        "nodes": [node("p")],
    },
    {"id": "heading-order", "impact": "minor", "help": "Heading order", "nodes": [node("h3")]},
    {"id": "custom", "impact": None, "help": "Unrated", "nodes": [node("div")]},
]
INCOMPLETE: list[JsonValue] = [
    {"id": "color-contrast", "help": "Contrast", "nodes": [node("a"), node("b")]}
]
# What the scan of VIOLATIONS and INCOMPLETE finds: severity, message, and data.
FOUND: list[tuple[Severity, str, dict[str, str]]] = [
    (
        Severity.ERROR,
        "a > b: image-alt (critical) at img: Images must have alternative text",
        {"symbol": "image-alt img"},
    ),
    (
        Severity.ERROR,
        (
            "a > b: image-alt (critical) at iframe | x-card >>> img.logo: "
            "Images must have alternative text"
        ),
        {"symbol": "image-alt iframe | x-card >>> img.logo"},
    ),
    (
        Severity.WARNING,
        "a > b: region (moderate) at p: Content in landmarks",
        {"symbol": "region p"},
    ),
    (
        Severity.WARNING,
        "a > b: heading-order (minor) at h3: Heading order",
        {"symbol": "heading-order h3"},
    ),
    (Severity.ERROR, "a > b: custom (None) at div: Unrated", {"symbol": "custom div"}),
    (
        Severity.INFO,
        "a > b: color-contrast needs manual review on 2 element(s): Contrast",
        {},
    ),
]


def test_violations_block_by_impact_and_incomplete_checks_are_reported(tmp_path: Path) -> None:
    path = write(tmp_path / "scan.json", results(violations=VIOLATIONS, incomplete=INCOMPLETE))
    scan = judge(path, POLICY, component="web", file="web/e2e/a.spec.ts", test="a > b")
    assert scan.problem is None
    found = [(item.severity, item.message, dict(item.data)) for item in scan.findings]
    assert found == FOUND
    assert {item.rule for item in scan.findings} == {"typescript.accessibility"}
    assert {item.location for item in scan.findings} == {Location("web/e2e/a.spec.ts")}
    assert {item.component for item in scan.findings} == {"web"}


def test_an_unreadable_or_invalid_scan_is_a_problem(tmp_path: Path) -> None:
    unreadable = judge(tmp_path / "none.json", POLICY, component="web", file="f", test="t")
    assert unreadable.problem is not None
    assert unreadable.problem.startswith("the scan cannot be read: ")
    invalid = judge(write(tmp_path / "a.json", []), POLICY, component="web", file="f", test="t")
    assert invalid == Scan("the scan ran None None, not the locked axe-core 4.11.0")


def test_a_scan_the_gate_cannot_accept_is_an_error_of_its_test() -> None:
    found = scans.invalid("web", "web/e2e/a.spec.ts", "a > b", "the scanned page had no content")
    assert found.rule == "typescript.accessibility-scan"
    assert (found.severity, found.component) == (Severity.ERROR, "web")
    assert found.message == "a > b: the scanned page had no content"
    assert found.location == Location("web/e2e/a.spec.ts")
