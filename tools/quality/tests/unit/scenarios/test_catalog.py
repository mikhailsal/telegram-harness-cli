from quality.policy import Capability, Suite
from quality.scenarios.catalog import CATALOG, OUTSIDE, REAL, requirement


def test_every_capability_brings_requirements_named_after_it() -> None:
    assert set(CATALOG) == set(Capability)
    for capability, requirements in CATALOG.items():
        assert requirements
        assert all(found.id.startswith(f"{capability}.") for found in requirements)
        assert all(found.summary and found.levels for found in requirements)


def test_requirement_ids_are_unique() -> None:
    ids = [found.id for requirements in CATALOG.values() for found in requirements]
    assert len(ids) == len(set(ids))


def test_boundaries_must_be_proved_outside_the_unit_suite() -> None:
    assert Suite.UNIT not in REAL | OUTSIDE
    smoke = requirement("cli.smoke")
    assert smoke is not None
    assert smoke[0] is Capability.CLI
    assert smoke[1].levels == OUTSIDE
    assert not smoke[1].optional
    engine = requirement("database.engine")
    assert engine is not None
    assert engine[1].levels == REAL


def test_optional_requirements_may_be_declared_not_applicable() -> None:
    found = requirement("http-api.not-found")
    assert found is not None
    assert found[1].optional
    assert requirement("http-api.unknown") is None
