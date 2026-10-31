import pytest

from quality.adapters.php import runtime
from quality.adapters.php.fixer import PhpFixer
from quality.adapters.php.helper import HELPER
from quality.adapters.php.profile import PhpProfile
from quality.adapters.php.provision import PhpSetup
from quality.adapters.php.runtime import Requirements
from quality.cli import local
from quality.cli.local import (
    cache_root,
    composer_cache,
    composer_pins,
    local_fixers,
    local_profiles,
    local_setups,
    php_environments,
    php_helper,
    php_requirements,
    worktree_files,
)
from quality.policy import Profile
from quality.toolchain import ManifestError, ToolchainError, load_manifest
from quality.toolchain.manifest import Manifest


def test_the_php_profile_uses_the_pinned_packages_the_composer_cache_and_the_helper() -> None:
    profile = local_profiles()[Profile.PHP]
    setup = local_setups()[Profile.PHP]
    fixer = local_fixers()[Profile.PHP]
    assert isinstance(profile, PhpProfile)
    assert isinstance(setup, PhpSetup)
    assert isinstance(fixer, PhpFixer)
    assert profile.pins == setup.pins == fixer.pins == load_manifest().packages["composer"]
    assert composer_pins() == profile.pins
    assert composer_cache() == cache_root().parent / "composer" == setup.cache
    assert php_environments().runtime.cache == composer_cache()
    assert fixer.environments.runtime.cache == composer_cache()
    assert php_environments().runtime.locate_php is runtime.system_php
    assert setup.locate_php is runtime.system_php
    assert profile.helper == php_helper() == setup.helper
    assert (php_helper().source, php_helper().cache) == (HELPER, cache_root().parent / "php-helper")
    assert fixer.listing is worktree_files


def test_the_runtimes_come_from_the_manifest() -> None:
    assert php_requirements() == Requirements("8.4", "3.5")
    setup = local_setups()[Profile.PHP]
    assert isinstance(setup, PhpSetup)
    assert setup.required == php_requirements()
    assert php_environments().runtime.required == php_requirements()


def test_the_php_pins_and_runtimes_need_a_valid_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> object:
        msg = "bad manifest"
        raise ManifestError(msg)

    monkeypatch.setattr(local, "load_manifest", broken)
    for call in (composer_pins, php_requirements):
        with pytest.raises(ToolchainError) as raised:
            call()
        assert raised.value.diagnostic.message == "toolchain manifest: bad manifest"


def test_a_manifest_without_the_php_runtimes_is_a_toolchain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest()
    runtimes = {"php": manifest.runtimes["php"]}
    monkeypatch.setattr(
        local, "load_manifest", lambda: Manifest.model_copy(manifest, update={"runtimes": runtimes})
    )
    with pytest.raises(ToolchainError) as raised:
        php_requirements()
    assert raised.value.diagnostic.message == "toolchain manifest: 'xdebug'"
