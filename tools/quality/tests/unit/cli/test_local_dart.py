from pathlib import Path

import pytest

from quality.adapters.dart.fixer import DartFixer
from quality.adapters.dart.helper import HELPER
from quality.adapters.dart.profile import DartProfile
from quality.adapters.dart.provision import DartSetup
from quality.cli import local
from quality.cli.local import (
    cache_root,
    dart_environments,
    dart_helper,
    local_fixers,
    local_profiles,
    local_setups,
    pinned,
    pub_cache,
    pub_pins,
    tool_step,
    worktree_files,
)
from quality.policy import Profile
from quality.toolchain import ManifestError, Tool, ToolchainError, load_manifest


def test_the_dart_profile_uses_the_pinned_packages_the_pub_cache_and_the_helper() -> None:
    profile = local_profiles()[Profile.DART]
    setup = local_setups()[Profile.DART]
    fixer = local_fixers()[Profile.DART]
    assert isinstance(profile, DartProfile)
    assert isinstance(setup, DartSetup)
    assert isinstance(fixer, DartFixer)
    assert profile.pins == setup.pins == fixer.pins == load_manifest().packages["pub"]
    assert pub_pins() == profile.pins
    assert pub_cache() == cache_root().parent / "pub" == setup.cache
    assert dart_environments().cache == pub_cache() == fixer.environments.cache
    assert profile.helper == dart_helper() == setup.helper
    assert (dart_helper().source, dart_helper().cache) == (
        HELPER,
        cache_root().parent / "dart-helper",
    )
    assert fixer.listing is worktree_files


def test_the_pub_pins_need_a_valid_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> object:
        msg = "bad manifest"
        raise ManifestError(msg)

    monkeypatch.setattr(local, "load_manifest", broken)
    with pytest.raises(ToolchainError) as raised:
        pub_pins()
    assert raised.value.diagnostic.message == "toolchain manifest: bad manifest"


def test_a_tool_step_names_its_tool() -> None:
    def found(name: str, tool: Tool, cache: Path) -> Path:
        return cache / f"{name}-{tool.version}"

    assert tool_step("dart", found)() == cache_root() / f"dart-{pinned('dart').version}"
