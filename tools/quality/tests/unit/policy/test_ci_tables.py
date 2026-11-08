"""The `[ci]` and `[audit]` tables of the policy (decision 0015)."""

import pytest

from quality.policy import parse_policy
from quality.testkit.policies import policy_toml


def rules_of(text: str) -> list[tuple[str, str]]:
    result = parse_policy(text.encode())
    assert result.policy is None
    return [(d.rule, d.message) for d in result.diagnostics]


CI = '[ci]\nworkflow = ".github/workflows/quality.yml"\nrequired_jobs = ["gate", "audit"]\n'


def test_the_ci_and_audit_tables_have_safe_defaults() -> None:
    result = parse_policy(policy_toml(extra=CI).encode())
    assert result.policy is not None
    ci, audit = result.policy.ci, result.policy.audit
    assert ci is not None
    assert (ci.aggregate_job, ci.codeowners, ci.control) == ("required", ".github/CODEOWNERS", [])
    assert (audit.blocking_score, audit.unscored) == (7.0, "error")
    assert parse_policy(policy_toml().encode()).policy is not None


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            ".github/workflows/quality.yml",
            ".github/workflows/nested/quality.yml",
            "'workflow' must be a file directly under .github/workflows",
        ),
        (".github/workflows/quality.yml", "ci/quality.yml", "'workflow' must be a file"),
        ('["gate", "audit"]', '["gate", "gate"]', "'required_jobs' must not repeat a job"),
        (
            '["gate", "audit"]',
            '["gate", "required"]',
            "the aggregate job cannot be one of the jobs it requires",
        ),
        ('["gate", "audit"]', "[]", "required_jobs: List should have at least 1 item"),
    ],
)
def test_the_ci_table_must_describe_one_workflow_and_distinct_jobs(
    old: str, new: str, message: str
) -> None:
    found = rules_of(policy_toml(extra=CI.replace(old, new)))
    assert len(found) == 1
    assert found[0][0] == "policy.invalid"
    assert message in found[0][1]


@pytest.mark.parametrize(
    ("audit", "key"),
    [("blocking_score = 10.5", "audit.blocking_score"), ('unscored = "ignore"', "audit.unscored")],
)
def test_the_audit_table_bounds_its_values(audit: str, key: str) -> None:
    found = rules_of(policy_toml(extra=f"[audit]\n{audit}\n"))
    assert [message.split(":")[0] for _, message in found] == [key]
