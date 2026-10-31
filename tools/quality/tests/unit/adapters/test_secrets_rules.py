import json
import re
import tomllib
from pathlib import Path

import pytest

from quality.adapters import secrets
from quality.adapters.native import ToolFailedError
from quality.adapters.secrets import (
    Finding,
    RulesError,
    derive_config,
    scan_directory,
    secret_diagnostics,
)
from quality.engine import Tools
from quality.process import Command, Completed
from quality.testkit import candidates
from quality.testkit.candidates import pinned_tools
from quality.toolchain import load_manifest
from quality.toolchain.install import tool_path


@pytest.fixture(scope="module")
def tools() -> Tools:
    return pinned_tools()


UPSTREAM = """\
title = "rules"

[allowlist]
description = "global"
paths = [
    '''node_modules''',
    '''(?:^|/)package-lock\\.json$''',
]
regexes = ['''^example$''']

[[rules]]
id = "demo"
regex = '''ghp_[0-9a-zA-Z]{36}'''
"""


def test_only_the_global_path_allowlist_is_removed() -> None:
    derived = derive_config(UPSTREAM)
    expected = tomllib.loads(UPSTREAM)
    del expected["allowlist"]["paths"]
    assert tomllib.loads(derived) == expected
    assert "node_modules" not in derived


def test_the_pinned_rule_set_can_be_derived(tools: Tools) -> None:
    upstream = tools.gitleaks_rules.read_text()
    derived = tomllib.loads(derive_config(upstream))
    original = tomllib.loads(upstream)
    assert "paths" in original["allowlist"]
    assert "paths" not in derived["allowlist"]
    assert derived["rules"] == original["rules"]


@pytest.mark.parametrize(
    ("upstream", "message"),
    [
        ('title = "x"\n', "the rule set has no global [allowlist] table"),
        ("[allowlist]\nregexes = []\n", "the global allowlist has no 'paths' array"),
        (
            "[allowlist]\nregexes = []\n[other]\npaths = [\n'''a''',\n]\n",
            "the global allowlist has no 'paths' array",
        ),
        (
            "[allowlist]\npaths = ['''a''']\nregexes = [\n'''b''',\n]\n",
            "removing the path allowlist changed other settings",
        ),
    ],
)
def test_unexpected_rule_sets_are_rejected(upstream: str, message: str) -> None:
    with pytest.raises(RulesError, match=re.escape(message)):
        derive_config(upstream)


def test_malformed_rule_sets_are_rejected() -> None:
    with pytest.raises(tomllib.TOMLDecodeError):
        derive_config("[allowlist]\npaths = [\n'''a''',\n]\nbroken = \n")


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        (None, "No such file or directory"),
        (b"\xff\xfe", "codec can't decode"),
        (b'title = "x"\n', "the rule set has no global [allowlist] table"),
        (b"[allowlist]\npaths = [\n'''a''',\n]\nbroken = \n", "Invalid value"),
    ],
    ids=["missing", "undecodable", "no-allowlist", "malformed"],
)
def test_unusable_rule_files_fail_the_scan(
    tmp_path: Path, tools: Tools, content: bytes | None, fragment: str
) -> None:
    rules = tmp_path / "rules.toml"
    if content is not None:
        rules.write_bytes(content)
    (tmp_path / "tree").mkdir()
    with pytest.raises(ToolFailedError) as raised:
        scan_directory(tmp_path / "tree", tmp_path, gitleaks=tools.gitleaks, rules=rules)
    message = raised.value.diagnostic.message
    assert message.startswith(f"gitleaks: cannot prepare rules from {rules}: ")
    assert fragment in message


class FakeGitleaks:
    def __init__(self, report: object, status: int) -> None:
        self.report = report
        self.status = status

    def __call__(self, command: Command, *, name: str, accepted: set[int]) -> Completed:
        assert (name, accepted) == ("gitleaks", {0, 9})
        argv = list(command.argv)
        assert argv[1:2] == ["dir"]
        assert "--redact" in argv
        if self.report is not None:
            path = Path(argv[argv.index("--report-path") + 1])
            path.write_text(json.dumps(self.report))
        return Completed(command.argv, self.status, b"", b"", 0.0)


def fake_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: object, status: int
) -> list[Finding]:
    monkeypatch.setattr(secrets, "run_tool", FakeGitleaks(report, status))
    rules = tmp_path / "rules.toml"
    rules.write_text(UPSTREAM)
    root = tmp_path / "tree"
    root.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    return scan_directory(root, work, gitleaks=Path("/bin/false"), rules=rules)


def entry(tmp_path: Path, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "File": str(tmp_path / "tree" / "src" / "a.py"),
        "StartLine": 3,
        "StartColumn": 7,
        "RuleID": "demo",
        "Description": "Demo token",
        "Secret": "REDACTED",
    }
    value.update(changes)
    return {key: item for key, item in value.items() if item is not None}


def test_reports_are_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = fake_scan(tmp_path, monkeypatch, [entry(tmp_path)], 9)
    assert found == [Finding("src/a.py", 3, 7, "demo", "Demo token")]


@pytest.mark.parametrize(
    ("report", "status", "message"),
    [
        ({"File": "x"}, 9, "report is not a list"),
        (["x"], 9, "report entry is not an object"),
        ("missing-file", 9, "report entry without text field File"),
        ("outside", 9, "finding outside the scanned tree: /etc/passwd"),
        ("bool-line", 9, "report entry without number field StartLine"),
        ("text-column", 9, "report entry without number field StartColumn"),
        ("missing-rule", 9, "report entry without text field RuleID"),
        ([], 9, "exit status and report disagree"),
        ("valid", 0, "exit status and report disagree"),
    ],
)
def test_malformed_reports_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: object, status: int, message: str
) -> None:
    variants: dict[str, object] = {
        "missing-file": [entry(tmp_path, File=None)],
        "outside": [entry(tmp_path, File="/etc/passwd")],
        "bool-line": [entry(tmp_path, StartLine=True)],
        "text-column": [entry(tmp_path, StartColumn="7")],
        "missing-rule": [entry(tmp_path, RuleID=None)],
        "valid": [entry(tmp_path)],
    }
    chosen = variants.get(report, report) if isinstance(report, str) else report
    with pytest.raises(ToolFailedError) as raised:
        fake_scan(tmp_path, monkeypatch, chosen, status)
    assert raised.value.diagnostic.message == f"gitleaks: {message}"


def test_a_missing_report_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ToolFailedError, match="gitleaks: report not found or not a regular file"):
        fake_scan(tmp_path, monkeypatch, None, 0)


def test_history_findings_are_relabelled() -> None:
    found = [Finding("abc123/src/a.py", 2, 5, "demo", "Demo token")]

    def relabel(path: str) -> tuple[str, str]:
        oid, _, rest = path.partition("/")
        return rest, f"history blob {oid}: "

    (diagnostic,) = secret_diagnostics(found, relabel)
    assert diagnostic.rule == "secrets.detected"
    assert diagnostic.message == "history blob abc123: demo: Demo token (redacted)"
    assert diagnostic.location is not None
    assert (diagnostic.location.path, diagnostic.location.line) == ("src/a.py", 2)
    assert diagnostic.location.column == 5


def test_pinned_tools_require_the_rule_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = load_manifest().tools["gitleaks"]
    executable = tool_path("gitleaks", tool, tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setattr(candidates, "cache_root", lambda: tmp_path)
    with pytest.raises(AssertionError, match="run `quality tools install` first"):
        pinned_tools()
