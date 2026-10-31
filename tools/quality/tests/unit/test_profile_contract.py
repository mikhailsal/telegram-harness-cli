"""The contract every language profile meets; a new profile must pass it unchanged.

Each test is parametrized over `Profile`, so adding an enum member without
wiring it into every stage fails here first (docs/guides/extending.md).
"""

import time
from pathlib import Path

import pytest

from quality.boundedio import ReportError
from quality.catalog import build_registry
from quality.cli.component_cmd import CONVENTIONS
from quality.cli.local import local_fixers, local_profiles, local_setups
from quality.discovery import classify, detect
from quality.discovery.filetypes import PROFILE_LANGUAGES
from quality.discovery.manifests import PARSERS
from quality.policy import Profile, Suite
from quality.rules.coverage import Expectation, evaluate
from quality.testing import SuiteRequest
from quality.testkit.policies import ComponentSpec, build_policy

PROFILES = list(Profile)
# A source file each profile must find in its unit coverage report.
SOURCES = {
    Profile.PYTHON: "src/app/core.py",
    Profile.TYPESCRIPT: "src/core.ts",
    Profile.DART: "lib/core.dart",
    Profile.PHP: "src/Core.php",
}
ROOTS = {Profile.DART: ("lib",)}


def spec(profile: Profile) -> ComponentSpec:
    return ComponentSpec(profile=str(profile), source=ROOTS.get(profile, ("src",)))


@pytest.mark.parametrize("profile", PROFILES)
def test_every_stage_has_the_profile(profile: Profile) -> None:
    assert profile in local_profiles()
    assert profile in local_setups()
    assert profile in local_fixers()
    assert PROFILE_LANGUAGES[profile]
    assert profile in CONVENTIONS
    assert profile in {owner for owner, _ in PARSERS.values()}
    assert profile in SOURCES, "add a sample source file for the new profile to this contract"


@pytest.mark.parametrize("profile", PROFILES)
def test_the_profile_s_rules_are_registered_and_explained(profile: Profile) -> None:
    registry = build_registry()
    rules = local_profiles()[profile].rules
    assert rules
    for rule in rules:
        assert registry.get(rule.id) == rule
        assert rule.explanation.strip()
        assert rule.remedy.strip()


@pytest.mark.parametrize("profile", PROFILES)
def test_the_profile_expects_coverage_of_its_own_source_only(profile: Profile) -> None:
    adapter = local_profiles()[profile]
    assert adapter.expected(SOURCES[profile])
    for other in (path for owner, path in SOURCES.items() if owner is not profile):
        assert not adapter.expected(other)
    assert not adapter.expected("README.md")
    assert not adapter.expected("src/core.go")


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("data", [b"", b"not a coverage report\x00"])
def test_an_empty_or_malformed_report_never_passes(
    profile: Profile, data: bytes, tmp_path: Path
) -> None:
    policy = build_policy(spec(profile))
    component = policy.components[0]
    request = SuiteRequest(
        component,
        Suite.UNIT,
        tuple(component.tests.unit),
        tmp_path,
        tmp_path,
        time.time(),
        tmp_path,
    )
    try:
        report = local_profiles()[profile].coverage(data, request)
    except ReportError:
        return
    expectation = Expectation(component.id, frozenset({SOURCES[profile]}), (), {}, frozenset())
    _, found = evaluate(expectation, report, policy.coverage)
    assert "coverage.unaccounted" in {diagnostic.rule for diagnostic in found}


@pytest.mark.parametrize("profile", PROFILES)
def test_source_without_a_matching_profile_fails(profile: Profile) -> None:
    policy = build_policy(spec(profile))
    root = ROOTS.get(profile, ("src",))[0]
    for path, rule in (
        (f"{root}/core.go", "discovery.unsupported-language"),
        (f"{root}/core.rb", "discovery.unsupported-language"),
        *(
            (f"{root}/{Path(sample).name}", "discovery.language-mismatch")
            for owner, sample in SOURCES.items()
            if owner is not profile
        ),
    ):
        found = classify(policy, path, detect(path))
        assert [diagnostic.rule for diagnostic in found.diagnostics] == [rule], path
