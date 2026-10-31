import pytest

from quality.catalog import build_registry
from quality.cli.main import main


def test_explain_prints_rule_and_remedy(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "policy.unknown-key"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("policy.unknown-key: Policy contains an unknown key\n")
    assert "How to fix: Remove the key or correct its spelling." in out


def test_explain_unknown_rule_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "no.such-rule"]) == 2
    assert "unknown rule ID 'no.such-rule'" in capsys.readouterr().err


def test_explain_list_prints_every_rule(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "--list"]) == 0
    listed = [line.split("\t")[0] for line in capsys.readouterr().out.splitlines()]
    assert listed == sorted(build_registry().ids())


def test_every_rule_has_a_requirement_and_remedy() -> None:
    for rule in build_registry():
        assert rule.requirement.startswith("R")
        assert rule.title
        assert rule.explanation
        assert rule.remedy
