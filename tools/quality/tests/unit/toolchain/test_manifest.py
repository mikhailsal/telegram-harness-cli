from pathlib import Path

import pytest

from quality.toolchain import ManifestError, load_manifest, parse_manifest
from quality.toolchain.manifest import Architecture, Packaging

SHA = "a" * 64
TOOL = f"""\
schema_version = 1

[tools.demo]
version = "1.2.3"
binary = "demo"

[tools.demo.artifacts.x86_64]
url = "https://example.invalid/demo.tar.gz"
sha256 = "{SHA}"
packaging = "tar"
member = "demo-1.2.3/demo"

[tools.demo.extras."rules.toml"]
url = "https://example.invalid/rules.toml"
sha256 = "{SHA}"
"""


def test_a_valid_manifest_is_parsed() -> None:
    manifest = parse_manifest(TOOL.encode())
    tool = manifest.tools["demo"]
    artifact = tool.artifacts[Architecture.X86_64]
    assert (tool.version, tool.binary) == ("1.2.3", "demo")
    assert (artifact.packaging, artifact.member) == (Packaging.TAR, "demo-1.2.3/demo")
    assert tool.extras["rules.toml"].url == "https://example.invalid/rules.toml"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("schema_version = 1", "schema_version = 2"),
        ('version = "1.2.3"', 'version = "latest"'),
        ('binary = "demo"', 'binary = "../demo"'),
        ("https://example.invalid/demo.tar.gz", "http://example.invalid/demo.tar.gz"),
        ("https://example.invalid/demo.tar.gz", "https://example.invalid/a b"),
        (f'sha256 = "{SHA}"\npackaging', f'sha256 = "{"A" * 64}"\npackaging'),
        (f'sha256 = "{SHA}"\npackaging', f'sha256 = "{SHA[:63]}"\npackaging'),
        ('packaging = "tar"', 'packaging = "rpm"'),
        ('member = "demo-1.2.3/demo"', 'member = "../demo"'),
        ('member = "demo-1.2.3/demo"', 'member = "/demo"'),
        ('member = "demo-1.2.3/demo"', 'member = "demo/./demo"'),
        ('member = "demo-1.2.3/demo"', 'member = "demo/.."'),
        ('member = "demo-1.2.3/demo"', ""),
        ('packaging = "tar"', 'packaging = "binary"'),
        ("[tools.demo.artifacts.x86_64]", "[tools.demo.artifacts.sparc]"),
        ('binary = "demo"', 'binary = "demo"\nchannel = "nightly"'),
        ('[tools.demo.extras."rules.toml"]', '[tools.demo.extras."../rules.toml"]'),
    ],
)
def test_invalid_values_are_rejected(old: str, new: str) -> None:
    assert TOOL.count(old) == 1
    with pytest.raises(ManifestError, match="invalid toolchain manifest"):
        parse_manifest(TOOL.replace(old, new).encode())


def test_binary_downloads_have_no_member() -> None:
    binary = TOOL.replace(
        'packaging = "tar"\nmember = "demo-1.2.3/demo"\n', 'packaging = "binary"\n'
    )
    artifact = parse_manifest(binary.encode()).tools["demo"].artifacts[Architecture.X86_64]
    assert (artifact.packaging, artifact.member) == (Packaging.BINARY, None)


@pytest.mark.parametrize("member", ["...a", "a.b/c+d", "x_y-z/.hidden"])
def test_member_segments_may_contain_dots(member: str) -> None:
    document = TOOL.replace('member = "demo-1.2.3/demo"', f'member = "{member}"')
    artifacts = parse_manifest(document.encode()).tools["demo"].artifacts
    assert artifacts[Architecture.X86_64].member == member


def test_a_tool_needs_an_artifact() -> None:
    document = 'schema_version = 1\n[tools.demo]\nversion = "1.0"\nbinary = "demo"\n'
    document += "artifacts = {}\n"
    with pytest.raises(ManifestError, match="artifacts"):
        parse_manifest(document.encode())


@pytest.mark.parametrize("data", [b"\xff\xfe", b"schema_version = ", b""])
def test_undecodable_or_malformed_documents_are_rejected(data: bytes) -> None:
    with pytest.raises(ManifestError, match="invalid toolchain manifest"):
        parse_manifest(data)


def test_a_missing_manifest_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "toolchains.toml"
    with pytest.raises(ManifestError, match=f"cannot read toolchain manifest {missing}"):
        load_manifest(missing)


TREE = TOOL.replace(
    'packaging = "tar"\nmember = "demo-1.2.3/demo"', 'packaging = "tree"\nmember = "demo"'
)


def test_a_tree_tool_names_its_executable_and_top_directory() -> None:
    document = TREE.replace('binary = "demo"', 'binary = "demo"\nexecutable = "bin/demo"')
    tool = parse_manifest(document.encode()).tools["demo"]
    assert (tool.program, tool.artifacts[Architecture.X86_64].member) == ("bin/demo", "demo")
    assert parse_manifest(TREE.encode()).tools["demo"].program == "demo"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('member = "demo"', 'member = "demo/bin"'),
        ('member = "demo"\n', ""),
        ('binary = "demo"', 'binary = "demo"\nexecutable = "../demo"'),
        ('binary = "demo"', 'binary = "demo"\nexecutable = "/bin/demo"'),
    ],
)
def test_invalid_tree_values_are_rejected(old: str, new: str) -> None:
    assert TREE.count(old) == 1
    with pytest.raises(ManifestError, match="invalid toolchain manifest"):
        parse_manifest(TREE.replace(old, new).encode())


@pytest.mark.parametrize(
    "packages",
    [
        '[packages.npm]\n"@scope/name" = "1.2.3"\nplain = "0.0.1"\n',
        "",
    ],
)
def test_package_pins_are_optional_and_exact(packages: str) -> None:
    manifest = parse_manifest(f"{TOOL}\n{packages}".encode())
    expected = {"npm": {"@scope/name": "1.2.3", "plain": "0.0.1"}} if packages else {}
    assert manifest.packages == expected


def test_pub_package_names_may_contain_underscores() -> None:
    manifest = parse_manifest(f'{TOOL}\n[packages.pub]\nbuild_runner = "2.4.0"\n'.encode())
    assert manifest.packages == {"pub": {"build_runner": "2.4.0"}}


def test_composer_package_names_have_a_vendor() -> None:
    document = f'{TOOL}\n[packages.composer]\n"phpunit/php-code-coverage" = "14.4.1"\n'
    manifest = parse_manifest(document.encode())
    assert manifest.packages == {"composer": {"phpunit/php-code-coverage": "14.4.1"}}


def test_runtimes_are_optional_and_name_a_minor_version() -> None:
    assert parse_manifest(TOOL.encode()).runtimes == {}
    manifest = parse_manifest(f'{TOOL}\n[runtimes.php]\nversion = "8.4"\n'.encode())
    assert manifest.runtimes["php"].version == "8.4"


@pytest.mark.parametrize(
    "runtime",
    [
        '[runtimes.php]\nversion = "8.4.1"\n',
        '[runtimes.php]\nversion = "8"\n',
        "[runtimes.php]\nversion = 8.4\n",
        '[runtimes.php]\nversion = "8.4"\nurl = "https://e.com"\n',
        '[runtimes.PHP]\nversion = "8.4"\n',
    ],
)
def test_invalid_runtimes_are_rejected(runtime: str) -> None:
    with pytest.raises(ManifestError, match="invalid toolchain manifest"):
        parse_manifest(f"{TOOL}\n{runtime}".encode())


@pytest.mark.parametrize(
    "packages",
    [
        '[packages.npm]\nname = "^1.2.3"\n',
        '[packages.npm]\nname = "1.2"\n',
        '[packages.npm]\nName = "1.2.3"\n',
        '[packages.npm]\n"@/name" = "1.2.3"\n',
        '[packages.composer]\n"vendor//name" = "1.2.3"\n',
        '[packages.composer]\n"Vendor/name" = "1.2.3"\n',
        '[packages.NPM]\nname = "1.2.3"\n',
    ],
)
def test_invalid_package_pins_are_rejected(packages: str) -> None:
    with pytest.raises(ManifestError, match="invalid toolchain manifest"):
        parse_manifest(f"{TOOL}\n{packages}".encode())
