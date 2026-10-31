import pytest

from quality.ci.gitlab import check_gitlab_lanes
from quality.ci.lanes import check_lanes, gate_lanes


@pytest.mark.parametrize(
    "fake",
    [
        "# ./quality gate --lane browser",
        "echo quality gate --lane browser",
        'echo "./quality gate --lane browser"',
        "printf '%s' './quality gate --lane browser'",
        "echo 'text\n./quality gate --lane browser\ntext'",
        'TEXT="text\n./quality gate --lane browser\ntext"',
        "cat <<'EOF'\n./quality gate --lane browser\nEOF",
        "if false; then\n./quality gate --lane browser\nfi",
        "echo setup; if false; then\n./quality gate --lane browser\nfi",
        "exit 0\n./quality gate --lane browser",
        "false &&\n./quality gate --lane browser",
        "quality() { echo quality; }\n./quality gate --lane browser",
        "echo `true`\n./quality gate --lane browser",
        "case x in\n x)\n exit 256 ;;\nesac\n./quality gate --lane browser",
    ],
)
def test_nonexecuted_gate_text_cannot_cover_the_browser_lane(fake: str) -> None:
    script = "./quality gate --lane main\n" + fake
    document = {"gate": {"script": script}}
    findings = list(check_gitlab_lanes(".gitlab-ci.yml", document))
    assert any(d.rule == "ci.lanes" and d.data["symbol"] == "browser" for d in findings)
    jobs = [{"steps": [{"run": script}]}]
    assert any(d.data["symbol"] == "browser" for d in check_lanes("workflow.yml", jobs))


@pytest.mark.parametrize(
    "script",
    [
        "# ./quality gate --lane browser\n./quality gate --lane main",
        "echo ignored # ./quality gate --lane browser\n./quality gate --lane main",
        './quality gate --lane main --reports "a#b" # ./quality gate --lane browser',
    ],
)
def test_comments_preserve_the_following_real_command(script: str) -> None:
    assert list(gate_lanes(script)) == ["main"]


@pytest.mark.parametrize(
    "script",
    [
        'echo "a#b\\"quoted"\n./quality gate --lane browser',
        "echo 'backslash \\\n# quoted data'\n./quality gate --lane browser",
        "./quality gate --lane browser --reports a\\#b",
        "./quality gate \\\n --lane browser",
        (
            "if true; then\n set -- value\nelif false; then\n set -- other\n"
            "else\n set -- last\nfi\n"
            "./quality gate --lane browser"
        ),
        "if true; then\n if true; then\n echo setup\n fi\nfi\nquality gate --lane browser",
    ],
)
def test_literal_calls_survive_quoted_data_and_completed_argument_setup(script: str) -> None:
    assert list(gate_lanes(script)) == ["browser"]


@pytest.mark.parametrize(
    "script",
    [
        "for item in one; do\n./quality gate --lane browser\ndone",
        "case value in\n./quality gate --lane browser\nesac",
        './quality gate --lane "unterminated',
        "./quality gate --lane browser; echo success",
        "./quality gate --lane a' '",
    ],
)
def test_compound_or_incomplete_shell_commands_cannot_prove_coverage(script: str) -> None:
    assert list(gate_lanes(script)) == ["<unresolved>"]


@pytest.mark.parametrize("fake", ["# ./quality gate", "echo quality gate", "echo './quality gate'"])
def test_fake_only_gitlab_scripts_cannot_be_a_complete_gate(fake: str) -> None:
    findings = list(check_gitlab_lanes(".gitlab-ci.yml", {"gate": {"script": fake}}))
    assert [(d.rule, d.data["symbol"]) for d in findings] == [("ci.lanes", "gate")]
