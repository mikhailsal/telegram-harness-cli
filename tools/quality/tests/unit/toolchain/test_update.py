import hashlib
import importlib
import io
from collections.abc import Mapping
from typing import IO

import pytest

from quality.toolchain import ManifestError, UpdateError, parse_manifest, update
from quality.toolchain.manifest import Architecture

UPDATER = importlib.import_module("quality.toolchain.update")

RULES_V2 = b"title = 'rules 2.0.0'\n"
X64, ARM = "a" * 64, "b" * 64
SUMS = f"{X64}  demo_2.0.0_linux_x64.tar.gz\n{ARM}  demo_2.0.0_linux_arm64.tar.gz\n"
BASE = "https://example.test/releases/download/v{0}"
MANIFEST = f"""\
# A demo manifest.
schema_version = 1

[tools.demo]
version = "1.2.0"
binary = "demo"
checksums = "{BASE.format("{version}")}/SUMS"

# Rules of this release.
[tools.demo.extras."rules.toml"]
url = "https://example.test/demo/v1.2.0/rules.toml"
sha256 = "{"1" * 64}"

[tools.demo.artifacts.x86_64]
url = "{BASE.format("1.2.0")}/demo_1.2.0_linux_x64.tar.gz"
sha256 = "{"2" * 64}"  # vendor checksum
packaging = "tar"
member = "demo-1.2.0/demo"

[tools.demo.artifacts.aarch64]
url = "{BASE.format("1.2.0")}/demo_1.2.0_linux_arm64.tar.gz"
sha256 = "{"3" * 64}"
packaging = "tar"
member = "demo-1.2.0/demo"

[tools.sdk]
version = "1.2.0"
binary = "sdk"
checksums = "{{url}}.sha256sum"

[tools.sdk.artifacts.x86_64]
url = "https://example.test/sdk/1.2.0/sdk-linux-x64.zip"
sha256 = "{"4" * 64}"
packaging = "binary"

[packages.npm]
"typescript" = "1.2.0"
"""


class Vendor:
    """Serves fixed documents and records every URL it is asked for."""

    def __init__(self, documents: Mapping[str, bytes]) -> None:
        self.documents = dict(documents)
        self.asked: list[str] = []

    def __call__(self, url: str) -> IO[bytes]:
        self.asked.append(url)
        if url not in self.documents:
            msg = f"download failed with HTTP 404: {url}"
            raise ManifestError(msg)
        return io.BytesIO(self.documents[url])


def vendor(**overrides: bytes) -> Vendor:
    documents = {
        f"{BASE.format('2.0.0')}/SUMS": SUMS.encode(),
        "https://example.test/demo/v2.0.0/rules.toml": RULES_V2,
        "https://example.test/sdk/2.0.0/sdk-linux-x64.zip.sha256sum": (
            f"{'c' * 64} *sdk-linux-x64.zip\n".encode()
        ),
    }
    return Vendor({**documents, **overrides})


def refused(text: str, name: str, version: str, source: Vendor | None = None) -> tuple[str, str]:
    with pytest.raises(UpdateError) as raised:
        update(text, name, version, opener=source or vendor())
    return raised.value.diagnostic.rule, raised.value.diagnostic.message


def test_only_the_tool_s_pins_change_and_comments_stay() -> None:
    source = vendor()
    updated = update(MANIFEST, "demo", "2.0.0", opener=source)
    rules_sha = hashlib.sha256(RULES_V2).hexdigest()
    expected = (
        MANIFEST.replace('version = "1.2.0"\nbinary = "demo"', 'version = "2.0.0"\nbinary = "demo"')
        .replace("demo/v1.2.0/rules.toml", "demo/v2.0.0/rules.toml")
        .replace("1" * 64, rules_sha)
        .replace("v1.2.0/demo_1.2.0", "v2.0.0/demo_2.0.0")
        .replace("2" * 64, X64)
        .replace("3" * 64, ARM)
        .replace("demo-1.2.0/demo", "demo-2.0.0/demo")
    )
    assert updated.text == expected
    assert (updated.old.version, updated.new.version) == ("1.2.0", "2.0.0")
    assert updated.new.extras["rules.toml"].sha256 == rules_sha
    assert source.asked.count(f"{BASE.format('2.0.0')}/SUMS") == 1
    assert parse_manifest(updated.text.encode()).tools["sdk"].version == "1.2.0"


def test_a_checksum_file_beside_each_artifact_may_star_its_names() -> None:
    updated = update(MANIFEST, "sdk", "2.0.0", opener=vendor())
    artifact = updated.new.artifacts[Architecture.X86_64]
    assert artifact.url == "https://example.test/sdk/2.0.0/sdk-linux-x64.zip"
    assert artifact.sha256 == "c" * 64
    assert artifact.member is None
    assert '"typescript" = "1.2.0"' in updated.text


@pytest.mark.parametrize(
    ("text", "name", "version", "message"),
    [
        ("schema_version = 2\n", "demo", "2.0.0", "invalid toolchain manifest"),
        (MANIFEST, "nope", "2.0.0", "unknown tool 'nope'; the manifest pins demo, sdk"),
        (MANIFEST, "demo", "1.2.0", "'1.2.0' is not a release other than the pinned 1.2.0"),
        (MANIFEST, "demo", "../2", "'../2' is not a release other than the pinned 1.2.0"),
        (
            MANIFEST.replace('checksums = "{url}.sha256sum"\n', ""),
            "sdk",
            "2.0.0",
            "the tool names no vendor checksum file in 'checksums'",
        ),
        (
            MANIFEST.replace("sdk/1.2.0/", "sdk/latest/"),
            "sdk",
            "2.0.0",
            "the x86_64 URL does not contain the version 1.2.0",
        ),
        (
            MANIFEST.replace("demo/v1.2.0/rules", "demo/main/rules"),
            "demo",
            "2.0.0",
            "the URL of the extra 'rules.toml' does not contain the version 1.2.0",
        ),
        (
            MANIFEST.replace("3" * 64, "2" * 64),
            "demo",
            "2.0.0",
            "the rewritten manifest would change more than demo; update it by hand",
        ),
    ],
)
def test_an_entry_that_cannot_be_derived_is_refused(
    text: str, name: str, version: str, message: str
) -> None:
    rule, found = refused(text, name, version)
    assert rule == "tools.update-invalid"
    assert found.startswith(message)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {f"{BASE.format('2.0.0')}/SUMS": SUMS.encode().splitlines()[0]},
            f"{BASE.format('2.0.0')}/SUMS does not list demo_2.0.0_linux_arm64.tar.gz",
        ),
        (
            {f"{BASE.format('2.0.0')}/SUMS": b"\xff\xfe" + SUMS.encode()},
            f"{BASE.format('2.0.0')}/SUMS does not list demo_2.0.0_linux_x64.tar.gz",
        ),
    ],
)
def test_a_checksum_file_must_list_every_new_artifact(
    overrides: dict[str, bytes], message: str
) -> None:
    rule, found = refused(MANIFEST, "demo", "2.0.0", Vendor({**vendor().documents, **overrides}))
    assert (rule, found) == ("tools.update-failed", message)


def test_a_failed_or_oversized_download_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = Vendor({})
    rule, found = refused(MANIFEST, "demo", "2.0.0", missing)
    assert rule == "tools.update-failed"
    assert found.startswith(f"cannot download {BASE.format('2.0.0')}/SUMS: download failed")
    monkeypatch.setattr(UPDATER, "DOCUMENT_LIMIT", 8)
    monkeypatch.setattr(UPDATER, "CHUNK", 4)
    rule, found = refused(MANIFEST, "demo", "2.0.0")
    assert (rule, found) == ("tools.update-failed", f"{BASE.format('2.0.0')}/SUMS exceeds 8 bytes")
