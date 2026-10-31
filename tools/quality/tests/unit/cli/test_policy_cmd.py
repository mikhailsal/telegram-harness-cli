import json
from pathlib import Path

import pytest

from quality.cli.main import main
from quality.policy.schema import (
    SCENARIOS_SCHEMA_PATH,
    SCHEMA_PATH,
    policy_schema_text,
    scenarios_schema_text,
)
from quality.testkit.policies import ComponentSpec, policy_toml


def write_policy(root: Path, text: str) -> None:
    (root / "quality.toml").write_text(text)


def test_valid_policy_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_policy(tmp_path, policy_toml())
    assert main(["policy", "validate", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "PASSED: 0 error(s), 0 warning(s)\n"


def test_invalid_policy_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy(tmp_path, policy_toml().replace("line = 80", "line = 180"))
    assert main(["policy", "validate", "--root", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "quality.toml: error: coverage.line: Input should be less than or equal to 100" in out
    assert out.endswith("FAILED: 1 error(s), 0 warning(s)\n")


def test_cross_validation_runs_after_schema_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy(tmp_path, policy_toml(ComponentSpec(extra='depends_on = ["ghost"]')))
    assert main(["policy", "validate", "--root", str(tmp_path), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "policy validate"
    assert report["exit_code"] == 2
    assert [d["rule"] for d in report["diagnostics"]] == ["policy.reference"]
    assert report["diagnostics"][0]["component"] == "app"


def test_missing_policy_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["policy", "validate", "--root", str(tmp_path)]) == 2
    assert "(policy.missing)" in capsys.readouterr().out


def test_schema_is_printed_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["policy", "schema", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == policy_schema_text()
    assert not (tmp_path / SCHEMA_PATH).exists()


def test_the_scenario_schema_is_printed_on_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["policy", "schema", "--root", str(tmp_path), "--scenarios"]) == 0
    assert capsys.readouterr().out == scenarios_schema_text()


def test_schema_write_then_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["policy", "schema", "--root", str(tmp_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert out.count("(policy.schema-stale)") == 2
    assert SCENARIOS_SCHEMA_PATH in out
    assert main(["policy", "schema", "--root", str(tmp_path), "--write"]) == 0
    assert (tmp_path / SCHEMA_PATH).read_text() == policy_schema_text()
    assert (tmp_path / SCENARIOS_SCHEMA_PATH).read_text() == scenarios_schema_text()
    assert main(["policy", "schema", "--root", str(tmp_path), "--check"]) == 0
    (tmp_path / SCENARIOS_SCHEMA_PATH).write_text("{}\n")
    assert main(["policy", "schema", "--root", str(tmp_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert out.count("(policy.schema-stale)") == 1
    assert SCENARIOS_SCHEMA_PATH in out
