"""The rules of scenario manifests and their reconciliation with test results (R08)."""

from __future__ import annotations

from quality.diagnostics import Kind, Rule, Severity

MANIFEST = Rule(
    id="scenarios.manifest",
    title="A scenario manifest is missing or invalid",
    requirement="R08",
    explanation=(
        "A component that declares capabilities names its scenario manifest in "
        "quality.toml: a TOML file of schema version 1 for that component, validated "
        "against docs/reference/scenarios.schema.json."
    ),
    remedy="Add or correct the manifest; `quality scenarios` validates it.",
    kind=Kind.USAGE,
)
REFERENCE = Rule(
    id="scenarios.reference",
    title="A scenario refers to something that is not declared",
    requirement="R08",
    explanation=(
        "A scenario's capability and enhanced profile must be declared by its component, "
        "its requirements must belong to its capability, its level must be a suite the "
        "component declares, and its operations must be operations of the manifest's "
        "OpenAPI document. A quarantine entry may suspend only scenarios of its suite."
    ),
    remedy="Declare what the scenario refers to, or correct the reference.",
    kind=Kind.USAGE,
)
UNCOVERED = Rule(
    id="scenarios.uncovered",
    title="An obligation has no scenario",
    requirement="R08",
    explanation=(
        "Every requirement of a declared capability needs a scenario, unless it is "
        "optional and declared not applicable with a reason. Every declared capability, "
        "every OpenAPI operation, and every enabled enhanced profile needs a scenario too."
    ),
    remedy="Add a scenario that proves the obligation, or declare an optional one not applicable.",
)
AMBIGUOUS = Rule(
    id="scenarios.ambiguous",
    title="A scenario declaration or mapping is ambiguous",
    requirement="R08",
    explanation=(
        "Scenario IDs are unique and either active or retired, a requirement is either "
        "covered or not applicable, a scenario names each test once, and an exact test "
        "name must name a single case of the suite's results."
    ),
    remedy="Remove the duplicate, or give the test cases distinct names.",
)
LEVEL = Rule(
    id="scenarios.level",
    title="A scenario is proved by a suite its obligation does not accept",
    requirement="R08",
    explanation=(
        "A command-line or UI smoke test runs the built artifact (e2e or acceptance), "
        "database, migration, and queue guarantees use real dependencies (integration or "
        "later), owned contracts are proved by the contract suite, and performance "
        "budgets are not measured under the unit suite's coverage instrumentation."
    ),
    remedy="Move the scenario's tests to a suite the obligation accepts.",
)
DELETED = Rule(
    id="scenarios.deleted",
    title="A scenario was removed without being retired",
    requirement="R08",
    explanation=(
        "Removing a scenario removes an obligation. A scenario of the base revision must "
        "stay in the manifest or move to `retired` with a reason, so the removal is reviewed."
    ),
    remedy="Restore the scenario, or retire its ID with the reason.",
)
ENHANCED = Rule(
    id="scenarios.enhanced",
    title="An enabled enhanced profile cannot be enforced",
    requirement="R08",
    explanation=(
        "Mutation testing has no tested profile in this release. Enabling it would "
        "promise a gate that does not run, so the check fails instead."
    ),
    remedy="Remove 'mutation' from 'enhanced'; decision 0014 lists the enforced profiles.",
    kind=Kind.USAGE,
)
FAILED = Rule(
    id="scenarios.failed",
    title="A mandatory scenario's test failed",
    requirement="R08",
    explanation="A scenario passes only when every test case it maps to ran and passed.",
    remedy="Fix the failing test or the behavior it proves.",
)
SKIPPED = Rule(
    id="scenarios.skipped",
    title="A mandatory scenario's test did not pass",
    requirement="R08",
    explanation=(
        "A skipped test, or one expected to fail, proves nothing; the scenario stays unmet."
    ),
    remedy="Remove the skip or the expected-failure marker, or map the scenario to a passing test.",
)
ABSENT = Rule(
    id="scenarios.absent",
    title="A mandatory scenario's test is not in the results",
    requirement="R08",
    explanation=(
        "Every test a scenario names must be among the cases its suite ran, and a prefix "
        "must select at least one case. A deleted, renamed, or deselected test, or a "
        "prefix that selects nothing, leaves the scenario unproved."
    ),
    remedy="Restore the test, or update the manifest to the test's current name.",
)
UNVERIFIED = Rule(
    id="scenarios.unverified",
    title="A mandatory scenario's suite left no results",
    requirement="R08",
    explanation=(
        "A scenario is proved only by results of this run. When its suite could not run "
        "or its evidence was invalid, the scenario is unproved."
    ),
    remedy="Fix the suite's run, reported separately, then rerun the check.",
)
EVIDENCE = Rule(
    id="scenarios.evidence",
    title="A mandatory scenario's test did not produce the evidence its requirement needs",
    requirement="R08",
    explanation=(
        "Some requirements need more than a passing test: `ui.accessibility` needs a valid "
        "accessibility scan of the rendered page in every viewport the test ran in. A test "
        "that passed without it proves only its own assertions."
    ),
    remedy="Attach an accessibility scan of the rendered page from the test.",
)
QUARANTINED = Rule(
    id="scenarios.quarantined",
    title="A mandatory scenario relies on a quarantined test",
    requirement="R08",
    explanation=(
        "A quarantined test proves nothing, whatever its result. A scenario that relies "
        "on one fails unless the quarantine entry names the scenario."
    ),
    remedy="Fix the flaky test and remove the quarantine, or name the scenario in the entry.",
)
SUSPENDED = Rule(
    id="scenarios.suspended",
    title="A mandatory scenario is suspended by a quarantine",
    requirement="R08",
    explanation=(
        "The quarantine entry of one of the scenario's tests names the scenario, so it is "
        "unproved but does not fail until the entry expires."
    ),
    remedy="Fix the flaky test and remove the quarantine before it expires.",
    severity=Severity.WARNING,
)
SUGGESTION = Rule(
    id="scenarios.suggestion",
    title="A component may have an undeclared capability",
    requirement="R08",
    explanation=(
        "Dependencies and files hint at boundaries such as HTTP or a database. A hint is "
        "not a requirement: only the component's owners know which capabilities apply."
    ),
    remedy="Declare the capability with its scenarios, or ignore the hint.",
    severity=Severity.INFO,
)
RULES = (
    MANIFEST,
    REFERENCE,
    UNCOVERED,
    AMBIGUOUS,
    LEVEL,
    DELETED,
    ENHANCED,
    FAILED,
    SKIPPED,
    ABSENT,
    UNVERIFIED,
    EVIDENCE,
    QUARANTINED,
    SUSPENDED,
    SUGGESTION,
)
