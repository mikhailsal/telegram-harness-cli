"""The committed toolchain manifest pins what the profiles and the launcher expect."""

import tomllib

from quality.toolchain import load_manifest
from quality.toolchain.manifest import MANIFEST, Architecture, Packaging


def test_the_committed_manifest_pins_gitleaks_with_its_rules() -> None:
    assert MANIFEST.name == "toolchains.toml"
    manifest = load_manifest()
    gitleaks = manifest.tools["gitleaks"]
    assert gitleaks.version == "8.30.1"
    assert set(gitleaks.artifacts) == {Architecture.X86_64, Architecture.AARCH64}
    assert all(artifact.member == "gitleaks" for artifact in gitleaks.artifacts.values())
    assert list(gitleaks.extras) == ["gitleaks.toml"]
    assert "/v8.30.1/config/" in gitleaks.extras["gitleaks.toml"].url
    assert set(manifest.tools) == {
        "uv",
        "gitleaks",
        "osv-scanner",
        "actionlint",
        "node",
        "chromium",
        "dart",
        "composer",
    }


def test_the_committed_manifest_pins_node_as_a_tree_and_the_npm_packages() -> None:
    manifest = load_manifest()
    node = manifest.tools["node"]
    assert (node.version, node.program) == ("24.22.0", "bin/node")
    for architecture, suffix in ((Architecture.X86_64, "x64"), (Architecture.AARCH64, "arm64")):
        artifact = node.artifacts[architecture]
        assert artifact.packaging is Packaging.TREE
        assert artifact.member == f"node-v24.22.0-linux-{suffix}"
        assert artifact.url.endswith(f"/v24.22.0/node-v24.22.0-linux-{suffix}.tar.xz")
    assert manifest.packages["npm"]["typescript"] == "6.0.3"
    assert manifest.packages["npm"]["@vitest/coverage-v8"] == "5.0.5"


def test_the_committed_manifest_pins_the_headless_shell_and_the_browser_packages() -> None:
    manifest = load_manifest()
    chromium = manifest.tools["chromium"]
    assert (chromium.version, chromium.program, chromium.checksums) == (
        "156.0.8078.4",
        "chrome-headless-shell",
        None,
    )
    for architecture, suffix in ((Architecture.X86_64, "64"), (Architecture.AARCH64, "-arm64")):
        artifact = chromium.artifacts[architecture]
        archive = f"chrome-headless-shell-linux{suffix}"
        assert (artifact.packaging, artifact.member) == (Packaging.TREE, archive)
        assert artifact.url == (
            "https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.4/"
            f"linux{suffix}/{archive}.zip"
        )
    browser = manifest.packages["npm-browser"]
    assert (browser["react"], browser["vite"], browser["@playwright/test"]) == (
        "19.3.1",
        "8.3.5",
        "1.64.1",
    )
    assert not set(browser) & set(manifest.packages["npm"])


def test_the_committed_manifest_pins_the_dart_sdk_as_a_zip_tree_and_the_pub_packages() -> None:
    manifest = load_manifest()
    dart = manifest.tools["dart"]
    assert (dart.version, dart.program) == ("3.13.6", "bin/dart")
    for architecture, suffix in ((Architecture.X86_64, "x64"), (Architecture.AARCH64, "arm64")):
        artifact = dart.artifacts[architecture]
        assert (artifact.packaging, artifact.member) == (Packaging.TREE, "dart-sdk")
        assert artifact.url.endswith(f"/3.13.6/sdk/dartsdk-linux-{suffix}-release.zip")
    assert manifest.packages["pub"] == {"test": "1.33.0", "coverage": "1.16.0", "lints": "6.1.0"}


def test_the_committed_manifest_pins_composer_its_packages_and_the_php_runtimes() -> None:
    manifest = load_manifest()
    composer = manifest.tools["composer"]
    assert (composer.version, composer.program) == ("2.10.3", "composer")
    artifacts = set(composer.artifacts.values())
    assert len(artifacts) == 1
    (artifact,) = artifacts
    assert artifact.packaging is Packaging.BINARY
    assert artifact.url == "https://getcomposer.org/download/2.10.3/composer.phar"
    assert manifest.packages["composer"] == {
        "phpunit/phpunit": "13.4.3",
        "phpunit/php-code-coverage": "14.4.1",
        "phpstan/phpstan": "2.3.1",
        "friendsofphp/php-cs-fixer": "3.95.27",
    }
    versions = {name: runtime.version for name, runtime in manifest.runtimes.items()}
    assert versions == {"php": "8.4", "xdebug": "3.5"}


def test_uv_s_pin_is_the_version_both_projects_require() -> None:
    version = load_manifest().tools["uv"].version
    for project in (
        MANIFEST.parent / "pyproject.toml",
        MANIFEST.parent / "launcher/pyproject.toml",
    ):
        required = tomllib.loads(project.read_text())["tool"]["uv"]["required-version"]
        assert required == f"=={version}", project
