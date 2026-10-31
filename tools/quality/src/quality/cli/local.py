"""The installed tools and profile adapters a command runs with; nothing is downloaded."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from quality.adapters.dart import environment as dart_environment
from quality.adapters.dart.fixer import DartFixer
from quality.adapters.dart.helper import HELPER as DART_HELPER
from quality.adapters.dart.helper import Helper
from quality.adapters.dart.profile import DartProfile
from quality.adapters.dart.provision import DartSetup
from quality.adapters.native import ToolFailedError
from quality.adapters.php import composer as php_composer
from quality.adapters.php import runtime as php_runtime
from quality.adapters.php.fixer import PhpFixer
from quality.adapters.php.helper import HELPER as PHP_HELPER
from quality.adapters.php.helper import Helper as PhpHelper
from quality.adapters.php.profile import PhpProfile
from quality.adapters.php.provision import PhpSetup
from quality.adapters.python.environment import Environments, Uv
from quality.adapters.python.fixer import PythonFixer
from quality.adapters.python.profile import PythonProfile
from quality.adapters.python.provision import PythonSetup
from quality.adapters.typescript import environment as node_environment
from quality.adapters.typescript.chromium import Chromium
from quality.adapters.typescript.fixer import TypeScriptFixer
from quality.adapters.typescript.profile import TypeScriptProfile
from quality.adapters.typescript.provision import TypeScriptSetup
from quality.engine import ProfileAdapter, ProfileSetup, Tools
from quality.engine.fixes import ProfileFixer, worktree_entries
from quality.gitio import Repository
from quality.policy import Profile
from quality.toolchain import (
    ManifestError,
    Tool,
    ToolchainError,
    cache_root,
    extra_path,
    install,
    load_manifest,
    locate,
    python_root,
)
from quality.toolchain.install import MISSING

# Ruff and Pyright are pinned by the core's own lockfile and installed beside its interpreter.
CORE_TOOLS = ("ruff", "pyright")
NODE = "node"
CHROMIUM = "chromium"
DART = "dart"
COMPOSER = "composer"
# The system programs of the PHP profile, by their `[runtimes]` names in the manifest.
PHP_RUNTIMES = ("php", "xdebug")
SCANNER = "osv-scanner"


def pinned(name: str) -> Tool:
    """Return a tool of the manifest or raise ToolchainError."""
    try:
        return load_manifest().tools[name]
    except (ManifestError, KeyError) as error:
        raise ToolchainError(MISSING.diagnostic(f"toolchain manifest: {error}")) from error


def local_tools() -> Tools:
    """Locate the pinned tools of the common gates in the cache."""
    tool = pinned("gitleaks")
    root = cache_root()
    gitleaks = locate("gitleaks", tool, root)
    rules = extra_path("gitleaks", tool, root, "gitleaks.toml")
    if not rules.is_file():
        message = f"gitleaks rule set is not installed (expected {rules})"
        raise ToolchainError(MISSING.diagnostic(message))
    actionlint = locate("actionlint", pinned("actionlint"), root)
    return Tools(Path(sys.executable), gitleaks, rules, actionlint)


def local_scanner() -> Path:
    """Locate the pinned osv-scanner of the dependency audit."""
    return locate(SCANNER, pinned(SCANNER), cache_root())


def local_uv() -> Uv:
    """Locate the pinned uv and the directory of its managed interpreters."""
    return Uv(locate("uv", pinned("uv"), cache_root()), python_root())


def core_tool(name: str) -> Path:
    """Locate a tool installed in the core environment."""
    path = Path(sys.executable).parent / name
    if not (path.is_file() and os.access(path, os.X_OK)):
        message = f"{name} is not installed in the core environment (expected {path})"
        raise ToolchainError(MISSING.diagnostic(message))
    return path


def package_pins(*tables: str) -> Mapping[str, str]:
    """Return the packages of the manifest's `[packages]` tables of these names."""
    try:
        packages = load_manifest().packages
    except ManifestError as error:
        raise ToolchainError(MISSING.diagnostic(f"toolchain manifest: {error}")) from error
    return {name: pin for table in tables for name, pin in packages.get(table, {}).items()}


def npm_pins() -> Mapping[str, str]:
    """Return the npm packages the TypeScript profile pins, browser components' included."""
    return package_pins("npm", "npm-browser")


def npm_cache() -> Path:
    """Return the shared npm cache beside the toolchain cache."""
    return cache_root().parent / "npm"


def tool_step(name: str, step: Callable[[str, Tool, Path], Path]) -> Callable[[], Path]:
    """Defer locating or installing a pinned tool until a component of its profile needs it."""

    def run() -> Path:
        try:
            return step(name, pinned(name), cache_root())
        except ToolchainError as error:
            raise ToolFailedError(error.diagnostic) from error

    return run


def node_environments() -> node_environment.Environments:
    """Return the TypeScript environments, which locate the installed Node on first use."""
    return node_environment.Environments(tool_step(NODE, locate), npm_cache())


def pub_pins() -> Mapping[str, str]:
    """Return the pub packages the Dart profile pins."""
    return package_pins("pub")


def pub_cache() -> Path:
    """Return the shared pub cache beside the toolchain cache."""
    return cache_root().parent / "pub"


def dart_helper() -> Helper:
    """Return the Dart helper, compiled into its own directory beside the toolchain cache."""
    return Helper(DART_HELPER, cache_root().parent / "dart-helper")


def dart_environments() -> dart_environment.Environments:
    """Return the Dart environments, which locate the installed SDK on first use."""
    return dart_environment.Environments(tool_step(DART, locate), pub_cache())


def composer_pins() -> Mapping[str, str]:
    """Return the Composer packages the PHP profile pins."""
    return package_pins("composer")


def composer_cache() -> Path:
    """Return the shared Composer cache beside the toolchain cache."""
    return cache_root().parent / "composer"


def php_requirements() -> php_runtime.Requirements:
    """Return the PHP and Xdebug minor versions of the manifest's `[runtimes]`."""
    try:
        runtimes = load_manifest().runtimes
        php, xdebug = (runtimes[name].version for name in PHP_RUNTIMES)
    except (ManifestError, KeyError) as error:
        raise ToolchainError(MISSING.diagnostic(f"toolchain manifest: {error}")) from error
    return php_runtime.Requirements(php, xdebug)


def php_helper() -> PhpHelper:
    """Return the PHP helper, installed into its own directory beside the toolchain cache."""
    return PhpHelper(PHP_HELPER, cache_root().parent / "php-helper")


def php_environments() -> php_composer.Environments:
    """Return the PHP environments, which probe PHP and locate Composer on first use."""
    runtime = php_runtime.Runtime(
        php_runtime.system_php, tool_step(COMPOSER, locate), composer_cache(), php_requirements()
    )
    return php_composer.Environments(runtime)


def worktree_files(root: Path) -> list[str]:
    """List the working tree's tracked and untracked, not ignored, regular files."""
    return [entry.path for entry in worktree_entries(Repository.discover(root, os.environ))]


def local_profiles() -> Mapping[Profile, ProfileAdapter]:
    """Return the profile adapters of this build with their installed tools."""
    ruff, pyright = (core_tool(name) for name in CORE_TOOLS)
    python = PythonProfile(ruff=ruff, pyright=pyright, environments=Environments(local_uv()))
    typescript = TypeScriptProfile(node_environments(), npm_pins(), chromium=local_chromium())
    dart = DartProfile(dart_environments(), pub_pins(), dart_helper())
    php = PhpProfile(php_environments(), composer_pins(), php_helper())
    return {
        Profile.PYTHON: python,
        Profile.TYPESCRIPT: typescript,
        Profile.DART: dart,
        Profile.PHP: php,
    }


def local_chromium() -> Chromium:
    """Return the pinned headless shell that browser scenarios run in."""
    return Chromium(tool_step(CHROMIUM, locate), tool_step(CHROMIUM, install))


def local_setups() -> Mapping[Profile, ProfileSetup]:
    """Return what bootstrap and doctor use to provision and probe each profile."""
    typescript = TypeScriptSetup(
        tool_step(NODE, locate), tool_step(NODE, install), npm_cache(), npm_pins(), local_chromium()
    )
    dart = DartSetup(
        tool_step(DART, locate), tool_step(DART, install), pub_cache(), pub_pins(), dart_helper()
    )
    php = PhpSetup(
        php_runtime.system_php,
        tool_step(COMPOSER, locate),
        tool_step(COMPOSER, install),
        composer_cache(),
        php_requirements(),
        composer_pins(),
        php_helper(),
    )
    return {
        Profile.PYTHON: PythonSetup(local_uv()),
        Profile.TYPESCRIPT: typescript,
        Profile.DART: dart,
        Profile.PHP: php,
    }


def local_fixers() -> Mapping[Profile, ProfileFixer]:
    """Return the fixers of this build with their installed tools."""
    typescript = TypeScriptFixer(node_environments(), npm_pins(), worktree_files)
    dart = DartFixer(dart_environments(), pub_pins(), worktree_files)
    return {
        Profile.PYTHON: PythonFixer(core_tool("ruff")),
        Profile.TYPESCRIPT: typescript,
        Profile.DART: dart,
        Profile.PHP: PhpFixer(php_environments(), composer_pins(), worktree_files),
    }
