import base64
import hashlib
import io
import json
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from quality.adapters.secrets import (
    Finding,
    scan_directory,
    secret_diagnostics,
)
from quality.engine import Tools
from quality.testkit.candidates import pinned_tools


def token(seed: str) -> str:
    """Build a high-entropy token at run time, so no secret is ever committed."""
    encoded = base64.b64encode(hashlib.sha256(seed.encode()).digest() * 2).decode()
    return "".join(ch for ch in encoded if ch.isalnum())[:36]


def github_token(seed: str = "demo") -> str:
    return "ghp" + "_" + token(seed)


SECRET = github_token()
LINE = f"github_token = {SECRET}\n".encode()
MARKER = "gitleaks" + ":allow"


@pytest.fixture(scope="module")
def tools() -> Tools:
    return pinned_tools()


def write_tree(root: Path, files: Mapping[str, bytes]) -> None:
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class Scanner:
    def __init__(self, base: Path, tools: Tools) -> None:
        self.base = base
        self.tools = tools
        self.runs = 0

    def __call__(self, files: Mapping[str, bytes], root: Path | None = None) -> list[Finding]:
        self.runs += 1
        tree = root or self.base / "tree"
        write_tree(tree, files)
        work = self.base / f"work-{self.runs}"
        work.mkdir()
        return scan_directory(
            tree, work, gitleaks=self.tools.gitleaks, rules=self.tools.gitleaks_rules
        )

    def report(self) -> str:
        return (self.base / f"work-{self.runs}" / "gitleaks" / "report.json").read_text()


@pytest.fixture
def scan(tmp_path: Path, tools: Tools) -> Scanner:
    return Scanner(tmp_path, tools)


def zipped(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, data)
    return buffer.getvalue()


def located(found: list[Finding]) -> list[tuple[str, int, str]]:
    return sorted((finding.path, finding.line, finding.rule) for finding in found)


def test_a_clean_tree_has_no_findings(scan: Scanner) -> None:
    assert scan({"README.md": b"# Nothing to see\n", "src/app.py": b"x = 1\n"}) == []
    derived = tomllib.loads((scan.base / "work-1" / "gitleaks" / "gitleaks.toml").read_text())
    assert "paths" not in derived["allowlist"]


def test_secrets_are_found_everywhere_the_upstream_allowlist_skipped(scan: Scanner) -> None:
    files = {
        "plain.txt": LINE,
        "blob.bin": b"\x00\x01\x02PNG\x00" + LINE + b"\xff\xfe\x00",
        "logo.png": b"\x89PNG\r\n\x1a\n" + LINE,
        "bundle.zip": zipped("inner/conf.txt", LINE),
        "node_modules/pkg/index.js": LINE,
        "package-lock.json": LINE,
        "vendor/github.com/x/y.go": LINE,
    }
    assert located(scan(files)) == [
        ("blob.bin", 1, "github-pat"),
        ("bundle.zip!inner/conf.txt", 1, "github-pat"),
        ("logo.png", 3, "github-pat"),
        ("node_modules/pkg/index.js", 1, "github-pat"),
        ("package-lock.json", 1, "github-pat"),
        ("plain.txt", 1, "github-pat"),
        ("vendor/github.com/x/y.go", 1, "github-pat"),
    ]


def test_findings_are_redacted(scan: Scanner) -> None:
    found = scan({"plain.txt": LINE})
    assert SECRET not in scan.report()
    assert "REDACTED" in scan.report()
    diagnostics = list(secret_diagnostics(found))
    assert [d.message for d in diagnostics] == [
        f"github-pat: {found[0].description} (redacted)",
    ]
    assert all(SECRET not in repr(d) for d in diagnostics)


LEAKY = {"plain.txt": LINE, "config/app.env": LINE}


def fingerprints(scan: Scanner, found: list[Finding]) -> list[str]:
    reported = [str(entry["Fingerprint"]) for entry in json.loads(scan.report())]
    return [*reported, *(f"{f.path}:{f.rule}:{f.line}" for f in found)]


def test_candidate_settings_cannot_hide_findings(scan: Scanner) -> None:
    found = scan(LEAKY, scan.base / "first")
    assert len(found) == 2
    ignored = "\n".join(fingerprints(scan, found)).encode() + b"\n"
    bypass = {
        **LEAKY,
        ".gitleaks.toml": b"[extend]\nuseDefault = false\n[allowlist]\npaths = ['''.*''']\n",
        ".gitleaksignore": ignored,
        ".gitignore": b"plain.txt\nconfig/\n",
    }
    assert located(scan(bypass, scan.base / "second")) == located(found)


def test_nested_ignore_files_are_not_read(scan: Scanner) -> None:
    root = scan.base / "tree"
    found = scan(LEAKY, root)
    ignored = "\n".join(fingerprints(scan, found)).encode() + b"\n"
    again = scan({"config/.gitleaksignore": ignored, "sub/.gitleaksignore": ignored}, root)
    assert located(again) == located(found)


def test_inline_allow_comments_are_ignored(scan: Scanner) -> None:
    marked = f"github_token = {SECRET}  # {MARKER}\n".encode()
    assert located(scan({"plain.txt": marked})) == [("plain.txt", 1, "github-pat")]
