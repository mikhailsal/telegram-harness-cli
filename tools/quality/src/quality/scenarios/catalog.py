"""The requirements each capability brings (R08, decision 0014).

A requirement is a behavior every component with the capability must prove
with a scenario. An optional requirement may instead be declared not
applicable with a reason; the others apply whenever the capability is
declared. `levels` are the suites whose tests can prove it. A `browser`
requirement applies only to browser components (decision 0018), and its
cases must also have produced its `evidence`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from quality.policy import Capability, Suite
from quality.testing import ACCESSIBILITY_SCAN

ANY = frozenset(Suite)
REAL = frozenset({Suite.INTEGRATION, Suite.E2E, Suite.ACCEPTANCE})
OUTSIDE = frozenset({Suite.E2E, Suite.ACCEPTANCE})
# The evidence of a case that attached a valid accessibility scan in every viewport.
EVIDENCE_NAMES = MappingProxyType(
    {ACCESSIBILITY_SCAN: "a valid accessibility scan in every viewport"}
)


@dataclass(frozen=True, slots=True)
class Requirement:
    """One behavior a capability obliges a component to prove."""

    id: str
    summary: str
    levels: frozenset[Suite] = ANY
    optional: bool = False
    browser: bool = False
    evidence: str | None = None


def _requirements(*requirements: Requirement) -> tuple[Requirement, ...]:
    return requirements


CATALOG: Mapping[Capability, tuple[Requirement, ...]] = MappingProxyType(
    {
        Capability.LIBRARY: _requirements(
            Requirement("library.public-api", "consumer code uses the public API"),
        ),
        Capability.CLI: _requirements(
            Requirement("cli.smoke", "the built artifact starts and answers", OUTSIDE),
            Requirement("cli.flows", "declared critical flows through the command line", REAL),
        ),
        Capability.HTTP_API: _requirements(
            Requirement("http-api.success", "public handlers serve successful operations"),
            Requirement("http-api.contract", "responses keep the published contract"),
            Requirement("http-api.invalid-input", "invalid input is rejected", optional=True),
            Requirement("http-api.not-found", "missing resources are reported", optional=True),
            Requirement("http-api.permission", "forbidden requests are refused", optional=True),
        ),
        Capability.DATABASE: _requirements(
            Requirement("database.engine", "tests use the real chosen engine", REAL),
            Requirement("database.read-write", "the adapter reads and writes", REAL),
            Requirement("database.constraints", "constraints hold", REAL, optional=True),
            Requirement(
                "database.transactions", "transactions commit and roll back", REAL, optional=True
            ),
        ),
        Capability.MIGRATIONS: _requirements(
            Requirement("migrations.fresh", "migrations build a fresh schema", REAL),
            Requirement(
                "migrations.upgrade", "a supported prior schema upgrades", REAL, optional=True
            ),
        ),
        Capability.CONTRACTS: _requirements(
            Requirement(
                "contracts.consumer",
                "the consumer's expectations hold against the owned provider",
                frozenset({Suite.CONTRACT}),
                optional=True,
            ),
            Requirement(
                "contracts.provider",
                "the provider satisfies its owned consumers' expectations",
                frozenset({Suite.CONTRACT}),
                optional=True,
            ),
        ),
        Capability.QUEUES: _requirements(
            Requirement(
                "queues.contract",
                "messages keep their contract",
                frozenset({Suite.CONTRACT, Suite.INTEGRATION}),
            ),
            Requirement("queues.acknowledgement", "messages are acknowledged", REAL, optional=True),
            Requirement("queues.retry", "failed deliveries are retried", REAL, optional=True),
            Requirement(
                "queues.duplicate", "duplicate deliveries are harmless", REAL, optional=True
            ),
        ),
        Capability.UI: _requirements(
            Requirement("ui.smoke", "the built application loads", OUTSIDE),
            Requirement("ui.flows", "declared critical flows through the interface", OUTSIDE),
            Requirement(
                "ui.accessibility",
                "accessibility scans of the declared rendered states",
                OUTSIDE,
                browser=True,
                evidence=ACCESSIBILITY_SCAN,
            ),
            Requirement(
                "ui.keyboard",
                "keyboard operation and focus handling of the critical controls",
                OUTSIDE,
                browser=True,
            ),
        ),
        Capability.AUTHORIZATION: _requirements(
            Requirement("authorization.forbidden", "forbidden access is refused"),
            Requirement(
                "authorization.isolation", "tenants cannot reach each other", optional=True
            ),
        ),
    }
)


def requirements(capability: Capability, *, browser: bool) -> tuple[Requirement, ...]:
    """Return what a capability requires of a browser component, or of any other."""
    return tuple(found for found in CATALOG[capability] if browser or not found.browser)


def requirement(identifier: str) -> tuple[Capability, Requirement] | None:
    """Find a requirement and its capability by ID."""
    for capability, requirements in CATALOG.items():
        for found in requirements:
            if found.id == identifier:
                return capability, found
    return None
