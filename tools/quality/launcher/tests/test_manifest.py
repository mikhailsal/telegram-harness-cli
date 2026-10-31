import tomllib
from pathlib import Path

import pytest

from fakes import manifest, release
from quality_launcher_uv import Artifact, LauncherError, Uv, load_uv, machine, read_tables

MANIFEST = Path(__file__).resolve().parents[2] / "toolchains.toml"


def test_the_real_manifest_reads_as_tomllib_reads_it() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    pin = tomllib.loads(text)["tools"]["uv"]
    expected = {"tools.uv": {key: value for key, value in pin.items() if isinstance(value, str)}}
    for arch, table in pin["artifacts"].items():
        expected[f"tools.uv.artifacts.{arch}"] = table
    assert read_tables(text, "tools.uv") == expected
    artifacts = {
        arch: Artifact(table["url"], table["sha256"], table["member"])
        for arch, table in pin["artifacts"].items()
    }
    assert load_uv(MANIFEST) == Uv(pin["version"], pin["binary"], artifacts)
    assert set(artifacts) == {"x86_64", "aarch64"}


def test_a_generated_manifest_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "toolchains.toml"
    path.write_text(manifest(release()), encoding="utf-8")
    assert load_uv(path) == release()


def test_only_the_uv_tables_are_read() -> None:
    text = """# Pinned tools.
schema_version = 1
[tools.uvx]
version = 2
[tools.uv]  # the pin
version = "1.0"  # inline comments are fine
# a comment inside the table

  binary   =   "uv"
[[tools.uv.list]]
anything = [1, 2]
[tools.uv.artifacts.x86_64]
url = "https://example.invalid/uv.tar.gz"
"""
    assert read_tables(text, "tools.uv") == {
        "tools.uv": {"version": "1.0", "binary": "uv"},
        "tools.uv.artifacts.x86_64": {"url": "https://example.invalid/uv.tar.gz"},
    }


ARTIFACT = '[tools.uv.artifacts.x86_64]\nurl = "u"\nsha256 = "s"\nmember = "m"\n'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[tools.uv]\nversion = "1"\nbinary = 1\n', 'line 3: expected key = "string"'),
        ("[tools.uv]\nversion = '1'\n", 'line 2: expected key = "string"'),
        ('[tools.uv]\nversion = "a\\"b"\n', 'line 2: expected key = "string"'),
        ('[tools.uv]\nversion = """1"""\n', 'line 2: expected key = "string"'),
        ('[tools.other]\nversion = "1"\n', r"has no \[tools.uv\] table"),
        ('[tools.uv]\nbinary = "uv"\n', r"\[tools.uv\] needs a non-empty version"),
        ('[tools.uv]\nversion = "1"\nbinary = ""\n', r"\[tools.uv\] needs a non-empty binary"),
        (
            '[tools.uv]\nversion = "1"\nbinary = "uv"\n' + ARTIFACT + 'packaging = "zip"\n',
            r"\[tools.uv.artifacts.x86_64\] must use tar packaging",
        ),
        (
            '[tools.uv]\nversion = "1"\nbinary = "uv"\n' + ARTIFACT,
            r"\[tools.uv.artifacts.x86_64\] needs a non-empty packaging",
        ),
        (
            '[tools.uv]\nversion = "1"\nbinary = "uv"\n'
            + ARTIFACT.replace('member = "m"\n', "")
            + 'packaging = "tar"\n',
            r"\[tools.uv.artifacts.x86_64\] needs a non-empty member",
        ),
    ],
)
def test_malformed_manifests_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "toolchains.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LauncherError, match=message):
        load_uv(path)


def test_a_missing_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LauncherError, match="the launcher must stay in tools/quality/launcher"):
        load_uv(tmp_path / "toolchains.toml")


@pytest.mark.parametrize(
    ("raw", "arch"),
    [("x86_64", "x86_64"), ("AMD64", "x86_64"), ("aarch64", "aarch64"), ("arm64", "aarch64")],
)
def test_machine_names_are_normalized(raw: str, arch: str) -> None:
    assert machine(raw) == arch


def test_other_machines_are_unsupported() -> None:
    with pytest.raises(LauncherError, match="unsupported machine architecture: riscv64"):
        machine("riscv64")
