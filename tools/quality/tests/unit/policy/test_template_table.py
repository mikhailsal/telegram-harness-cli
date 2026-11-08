"""The `[template]` table of the policy (decision 0016)."""

from quality.policy import parse_policy
from quality.testkit.policies import policy_toml

TEMPLATE = '[template]\nexclude = ["PLAN.md", "docs/plan/**"]\nfenced = ["README.md"]\n'


def test_the_template_table_is_empty_unless_declared() -> None:
    plain = parse_policy(policy_toml().encode()).policy
    assert plain is not None
    assert (plain.template.exclude, plain.template.fenced) == ([], [])
    declared = parse_policy(policy_toml(extra=TEMPLATE).encode()).policy
    assert declared is not None
    assert declared.template.exclude == ["PLAN.md", "docs/plan/**"]
    assert declared.template.fenced == ["README.md"]


def test_a_fenced_file_is_listed_once() -> None:
    extra = TEMPLATE.replace('["README.md"]', '["README.md", "README.md"]')
    result = parse_policy(policy_toml(extra=extra).encode())
    assert result.policy is None
    assert [d.rule for d in result.diagnostics] == ["policy.invalid"]
    assert "'fenced' must not repeat a file" in result.diagnostics[0].message
