import functools
import hashlib
import io
from pathlib import Path

import pytest

from quality.cli import tools_cmd
from quality.cli.main import main
from quality.toolchain import ManifestError, Tool, ToolchainError, load_manifest, update
from quality.toolchain.install import INSTALL_FAILED
from quality.toolchain.manifest import MANIFEST


class FakeInstaller:
    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.failing = failing
        self.calls: list[tuple[str, str, Path]] = []

    def __call__(self, name: str, tool: Tool, root: Path) -> Path:
        self.calls.append((name, tool.version, root))
        if name in self.failing:
            message = f"cannot install {name} {tool.version}: checksum mismatch"
            raise ToolchainError(INSTALL_FAILED.diagnostic(message))
        return root / f"{name}-{tool.version}" / name


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tools_cmd, "cache_root", lambda: tmp_path)
    return tmp_path


def use(monkeypatch: pytest.MonkeyPatch, installer: FakeInstaller) -> FakeInstaller:
    monkeypatch.setattr(tools_cmd, "install", installer)
    return installer


def test_the_default_installs_what_checks_and_the_core_tests_need(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = use(monkeypatch, FakeInstaller())
    assert main(["tools", "install"]) == 0
    assert installer.calls == [
        ("gitleaks", "8.30.1", cache),
        ("actionlint", "1.7.12", cache),
        ("osv-scanner", "2.7.0", cache),
    ]
    assert capsys.readouterr().out.splitlines() == [
        f"gitleaks 8.30.1: {cache}/gitleaks-8.30.1/gitleaks",
        f"actionlint 1.7.12: {cache}/actionlint-1.7.12/actionlint",
        f"osv-scanner 2.7.0: {cache}/osv-scanner-2.7.0/osv-scanner",
    ]


def test_named_tools_are_installed_in_order(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = use(monkeypatch, FakeInstaller())
    assert main(["tools", "install", "osv-scanner", "actionlint"]) == 0
    assert [call[0] for call in installer.calls] == ["osv-scanner", "actionlint"]
    assert capsys.readouterr().out.splitlines() == [
        f"osv-scanner 2.7.0: {cache}/osv-scanner-2.7.0/osv-scanner",
        f"actionlint 1.7.12: {cache}/actionlint-1.7.12/actionlint",
    ]


def test_unknown_tools_are_a_usage_error(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = use(monkeypatch, FakeInstaller())
    assert main(["tools", "install", "gitleaks", "nmap", "curl"]) == 2
    assert installer.calls == []
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "unknown tools: nmap, curl\n")


def test_failed_installs_are_environment_errors(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, FakeInstaller(frozenset({"uv"})))
    assert main(["tools", "install", "uv", "gitleaks"]) == 3
    assert capsys.readouterr().out.splitlines() == [
        f"gitleaks 8.30.1: {cache}/gitleaks-8.30.1/gitleaks",
        "error: cannot install uv 0.12.24: checksum mismatch (tools.install-failed)",
        "  hint: Check the network and the manifest entry, then retry.",
        "FAILED: 1 error(s), 0 warning(s)",
    ]


def test_an_invalid_manifest_is_an_environment_error(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = use(monkeypatch, FakeInstaller())

    def broken() -> None:
        msg = "invalid toolchain manifest: bad"
        raise ManifestError(msg)

    monkeypatch.setattr(tools_cmd, "load_manifest", broken)
    assert main(["tools", "install"]) == 3
    assert installer.calls == []
    assert capsys.readouterr().out.splitlines() == [
        "error: invalid toolchain manifest: bad (tools.install-failed)",
        "  hint: Check the network and the manifest entry, then retry.",
        "FAILED: 1 error(s), 0 warning(s)",
    ]


def test_an_action_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["tools"])
    assert raised.value.code == 2
    assert "usage: quality tools" in capsys.readouterr().err


@pytest.fixture
def manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "toolchains.toml"
    path.write_bytes(MANIFEST.read_bytes())
    monkeypatch.setattr(tools_cmd, "MANIFEST", path)
    return path


def test_update_rewrites_the_pins_from_the_vendor_checksums(
    manifest: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = "https://github.com/rhysd/actionlint/releases/download/v1.7.13"
    sums = f"{'c' * 64}  actionlint_1.7.13_linux_amd64.tar.gz\n"
    sums += f"{'d' * 64}  actionlint_1.7.13_linux_arm64.tar.gz\n"
    documents = {f"{base}/actionlint_1.7.13_checksums.txt": sums.encode()}
    monkeypatch.setattr(
        tools_cmd,
        "update",
        functools.partial(update, opener=lambda url: io.BytesIO(documents[url])),
    )
    assert main(["tools", "update", "actionlint", "1.7.13"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "actionlint 1.7.12 -> 1.7.13 in toolchains.toml",
        f"  x86_64: {base}/actionlint_1.7.13_linux_amd64.tar.gz sha256 {'c' * 64}",
        f"  aarch64: {base}/actionlint_1.7.13_linux_arm64.tar.gz sha256 {'d' * 64}",
        "next: ./quality tools install actionlint, then review, stage, and commit",
    ]
    assert load_manifest(manifest).tools["actionlint"].version == "1.7.13"
    assert load_manifest(manifest).tools["gitleaks"] == load_manifest().tools["gitleaks"]


def test_update_lists_the_new_extras(
    manifest: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def vendor(url: str) -> io.BytesIO:
        if url.endswith("_checksums.txt"):
            names = ("linux_x64", "linux_arm64")
            return io.BytesIO(
                "".join(f"{'e' * 64}  gitleaks_8.31.0_{n}.tar.gz\n" for n in names).encode()
            )
        return io.BytesIO(b"rules\n")

    monkeypatch.setattr(tools_cmd, "update", functools.partial(update, opener=vendor))
    assert main(["tools", "update", "gitleaks", "8.31.0"]) == 0
    lines = capsys.readouterr().out.splitlines()
    rules = "https://raw.githubusercontent.com/gitleaks/gitleaks/v8.31.0/config/gitleaks.toml"
    assert lines[3] == f"  gitleaks.toml: {rules} sha256 {hashlib.sha256(b'rules\n').hexdigest()}"


def test_an_update_that_cannot_be_derived_changes_nothing(
    manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = manifest.read_bytes()
    assert main(["tools", "update", "uv", "0.12.24"]) == 2
    assert "(tools.update-invalid)" in capsys.readouterr().out
    assert manifest.read_bytes() == before
