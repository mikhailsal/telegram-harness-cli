"""Scratch projects driven through real Git hooks and the core under test.

Each project is a new repository with Python, TypeScript, Dart, or PHP
application components. Its `quality` entry point runs this checkout's core
with the interpreter running the suite, so hooks exercise exactly the code
under test. A project can instead
embed a copy of that core at `tools/quality`, where its entry point runs the
copy in the work tree as the launcher would. The pinned toolchains and uv's
package cache are reached through the caller's cache directory; nothing is
downloaded.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import quality
from quality.engine.provenance import BOOT
from quality.process import Command, Completed, run
from quality.testkit.browserproject import MANIFEST, browser_project
from quality.testkit.dartproject import dart_project
from quality.testkit.gitrepo import GitRepo
from quality.testkit.phpproject import php_project
from quality.testkit.policies import (
    ComponentSpec,
    browser_spec,
    classification_toml,
    policy_toml,
)
from quality.testkit.pythonproject import python_project
from quality.testkit.typescriptproject import typescript_project

CORE_SOURCE = Path(quality.__file__).resolve().parents[1]
TYPESCRIPT_HELPER = Path("helpers") / "typescript"
DART_HELPER = Path("helpers") / "dart"
DART_HELPER_FILES = ("bin", "lib", "pubspec.yaml", "pubspec.lock")
PHP_HELPER = Path("helpers") / "php"
PHP_HELPER_FILES = ("bin", "src", "composer.json", "composer.lock")
EMBEDDED_CORE = "tools/quality"
README = "# Scratch project\n\nSee [agent instructions](AGENTS.md).\n"
AGENTS = "# Agents\n\nRun `quality check` before committing and `quality fix` to format.\n"
IGNORED = ".venv/\n__pycache__/\nnode_modules/\n.dart_tool/\nvendor/\n"
CORE = """\
def sign(value: int) -> int:
    if value < 0:
        return -1
    return 1
"""
TESTS = """\
from {name}.core import sign


def test_sign() -> None:
    assert sign(-3) == -1
    assert sign(4) == 1
"""
TIMEOUT = 900.0
RULE = re.compile(r"\(([a-z]+\.[a-z-]+)\)(?: \[[^\]]+\])?$", re.MULTILINE)


def entry_point(source: Path = CORE_SOURCE) -> str:
    """Return the `quality` script that runs the core in `source` (the core under test)."""
    argv = (sys.executable, "-I", "-c", BOOT, str(source))
    return f'#!/bin/sh\nexec {" ".join(shlex.quote(part) for part in argv)} "$@"\n'


def shared_caches(environ: Mapping[str, str]) -> dict[str, str]:
    """Return the cache variables that lead a scratch user to the installed toolchain."""
    home = Path(environ.get("HOME") or Path.home())
    caches = {"XDG_CACHE_HOME": environ.get("XDG_CACHE_HOME") or str(home / ".cache")}
    if "UV_CACHE_DIR" in environ:
        caches["UV_CACHE_DIR"] = environ["UV_CACHE_DIR"]
    return caches


def component(name: str = "app") -> ComponentSpec:
    """Return a Python application component rooted at `name/`."""
    return ComponentSpec(id=name, root=name)


def browser(name: str = "web", extra: str = "") -> ComponentSpec:
    """Return the browser component `name`, with its end-to-end suite and scenario manifest."""
    spec = browser_spec(component=name, root=name, extra=extra)
    return replace(spec, e2e=("e2e",), scenarios=MANIFEST)


def typescript(name: str = "web") -> ComponentSpec:
    """Return a TypeScript application component rooted at `name/`."""
    return ComponentSpec(id=name, root=name, profile="typescript")


def dart(name: str = "calc") -> ComponentSpec:
    """Return a Dart application component rooted at `name/`; `name` is its package name."""
    return ComponentSpec(id=name, root=name, profile="dart", source=("lib",), unit=("test",))


def php(name: str = "shop") -> ComponentSpec:
    """Return a PHP application component rooted at `name/`, with sources in `src/`."""
    return ComponentSpec(id=name, root=name, profile="php", source=("src",), unit=("tests",))


def output(result: Completed) -> str:
    """Return everything a command printed."""
    return result.stdout_text() + result.stderr_text()


def rules_of(result: Completed) -> set[str]:
    """Return the rules of the findings a command printed as text."""
    return set(RULE.findall(output(result)))


@dataclass(frozen=True, slots=True)
class Project:
    """A repository with Python components, committed before any hook exists.

    `caches` replaces the shared cache variables, for a user whose cache holds
    only some of the toolchains.
    """

    repo: GitRepo
    caches: Mapping[str, str] | None = None

    @property
    def path(self) -> Path:
        """The work tree."""
        return self.repo.path

    def environ(self) -> dict[str, str]:
        """Return the scratch user's environment with its caches."""
        caches = shared_caches(os.environ) if self.caches is None else self.caches
        return self.repo.environ(**caches)

    def run(self, *argv: str) -> Completed:
        """Run a command in the work tree as the user would."""
        return run(Command(argv, cwd=self.path, timeout=TIMEOUT, env=self.environ()))

    def quality(self, *args: str) -> Completed:
        """Run the project's `quality` entry point."""
        return self.run("./quality", *args)

    def git(self, *args: str) -> Completed:
        """Run Git with the shared caches, so hooks find the toolchain."""
        return self.run("git", *args)

    def write(self, path: str, text: str) -> None:
        """Write a work-tree file."""
        self.repo.write(path, text)

    def commit(self, message: str, *paths: str) -> Completed:
        """Stage the paths (everything when none) and commit through the hooks."""
        self.repo.git("add", "--", *(paths or (".",)))
        return self.git("commit", "-q", "-m", message)

    def head(self) -> str:
        """Return the current commit."""
        return self.repo.head()

    def index_tree(self) -> str:
        """Return the tree the index would commit."""
        return self.repo.git("write-tree").strip()

    def hook(self, name: str) -> Path:
        """Return the path of a hook in the repository's hooks directory."""
        return self.path / ".git" / "hooks" / name


def embed_core(path: Path) -> Path:
    """Copy the core under test, its manifest, and its lock to `path`; return its source root.

    The helpers' sources and manifests come along when the core has them,
    because the adapters run them beside the package; installed packages
    (`node_modules`, `.dart_tool`, `vendor`) do not.
    """
    ignored = shutil.ignore_patterns("__pycache__")
    shutil.copytree(CORE_SOURCE / "quality", path / "src" / "quality", ignore=ignored)
    for name in ("toolchains.toml", "uv.lock"):
        shutil.copyfile(CORE_SOURCE.parent / name, path / name)
    helper = CORE_SOURCE.parent / TYPESCRIPT_HELPER
    if (helper / "src").is_dir():
        modules = shutil.ignore_patterns("node_modules")
        shutil.copytree(helper / "src", path / TYPESCRIPT_HELPER / "src", ignore=modules)
        shutil.copyfile(helper / "package.json", path / TYPESCRIPT_HELPER / "package.json")
    for helper, names in ((DART_HELPER, DART_HELPER_FILES), (PHP_HELPER, PHP_HELPER_FILES)):
        if (CORE_SOURCE.parent / helper / "bin").is_dir():
            _copy_helper(CORE_SOURCE.parent / helper, path / helper, names)
    return path / "src"


def _copy_helper(source: Path, target: Path, names: tuple[str, ...]) -> None:
    target.mkdir(parents=True)
    for name in names:
        if (source / name).is_dir():
            shutil.copytree(
                source / name, target / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        else:
            shutil.copyfile(source / name, target / name)


def create_project(
    path: Path,
    *components: ComponentSpec,
    core: bool = False,
    files: Mapping[str, Mapping[str, str]] | None = None,
) -> Project:
    """Write and commit a passing project; no hook is installed yet.

    With `core`, the project embeds the core under test at `tools/quality`,
    classified as vendor content, and its entry point runs that copy.
    `files` replaces the generated files of a Python component, by its ID.
    """
    specs = components or (component(),)
    extra = classification_toml("quality", "config", "Entry point of the quality gate.")
    repo = GitRepo.create(path)
    source = CORE_SOURCE
    if core:
        source = embed_core(repo.path / EMBEDDED_CORE)
        extra += classification_toml(f"{EMBEDDED_CORE}/**", "vendor", "A copy of the core.")
    repo.write("quality.toml", policy_toml(*specs, extra=extra))
    repo.write("quality", entry_point(source), executable=True)
    repo.write("README.md", README)
    repo.write("AGENTS.md", AGENTS)
    repo.write(".gitignore", IGNORED)
    for spec in specs:
        if spec.profile == "typescript":
            writer = browser_project if spec.browser else typescript_project
            writer(repo.path / spec.root, name=spec.id)
            continue
        if spec.profile == "dart":
            dart_project(repo.path / spec.root, name=spec.id)
            continue
        if spec.profile == "php":
            php_project(repo.path / spec.root)
            continue
        default = {
            f"src/{spec.id}/__init__.py": "",
            f"src/{spec.id}/core.py": CORE,
            f"tests/test_{spec.id}.py": TESTS.format(name=spec.id),
        }
        python_project(repo.path / spec.root, (files or {}).get(spec.id, default), name=spec.id)
    repo.commit_all("initial project")
    return Project(repo)
