"""A scratch Dart component, resolved offline from the shared pub cache.

The component pins the profile's packages at the versions of the core's
toolchain manifest. The pinned SDK writes its `pubspec.lock` with
`dart pub get --offline`, so nothing is downloaded: bootstrapping this
repository filled the cache, because its Dart helper locks the same packages.
The `.dart_tool` directory that pub writes is removed again, so the work tree
looks like a fresh clone.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from quality.process import Command, run, sanitized
from quality.toolchain import cache_root, load_manifest, locate

LOCK_TIMEOUT = 600.0
SDK = "^3.13.0"
ANALYSIS_OPTIONS = """\
include: package:lints/recommended.yaml

analyzer:
  language:
    strict-casts: true
    strict-inference: true
    strict-raw-types: true
"""
CORE = """\
int sign(int value) {
  if (value < 0) {
    return -1;
  } else {
    return 1;
  }
}
"""
TESTS = """\
import 'package:{name}/core.dart';
import 'package:test/test.dart';

void main() {{
  test('sign', () {{
    expect(sign(-3), -1);
    expect(sign(4), 1);
  }});
}}
"""


def pinned_packages() -> dict[str, str]:
    """Return the pub packages the Dart profile pins, at their versions."""
    return dict(load_manifest().packages["pub"])


def pinned_dart() -> Path:
    """Return the installed pinned `dart` executable."""
    return locate("dart", load_manifest().tools["dart"], cache_root())


def pub_cache() -> Path:
    """Return the shared pub cache beside the toolchains."""
    return cache_root().parent / "pub"


def pubspec(name: str, *, pinned: bool = True, **fields: object) -> str:
    """Return a package manifest; `pinned` adds the profile's packages as dev dependencies."""
    document: dict[str, object] = {
        "name": name,
        "publish_to": "none",
        "environment": {"sdk": SDK},
    }
    if pinned:
        document["dev_dependencies"] = pinned_packages()
    document.update(fields)
    return yaml.safe_dump(document, sort_keys=False)


def workspace(members: Sequence[str]) -> str:
    """Return the manifest of a pub workspace root that lists `members`."""
    return pubspec("workspace_root", workspace=list(members))


def write(directory: Path, files: Mapping[str, str]) -> None:
    """Write text files below `directory`."""
    for path, text in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def lock(directory: Path, dart: Path | None = None) -> Path:
    """Resolve the package or workspace in `directory` from the cache; return its lockfile."""
    executable = dart or pinned_dart()
    with tempfile.TemporaryDirectory(prefix="quality-pub-") as home:
        extra = {"HOME": home, "PUB_CACHE": str(pub_cache()), "DART_SUPPRESS_ANALYTICS": "true"}
        argv = (str(executable), "pub", "get", "--offline")
        env = sanitized(path=[executable.parent], extra=extra)
        completed = run(Command(argv, cwd=directory, timeout=LOCK_TIMEOUT, env=env))
    if not completed.ok:
        detail = completed.stderr_text().strip() or completed.stdout_text().strip()
        msg = f"pub could not lock {directory}: {detail}"
        raise AssertionError(msg)
    shutil.rmtree(directory / ".dart_tool", ignore_errors=True)
    return directory / "pubspec.lock"


def package_files(name: str, files: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the files of a package `name`: manifest, options, and `files` or the default."""
    sources = {"lib/core.dart": CORE, "test/core_test.dart": TESTS.format(name=name)}
    return {
        "pubspec.yaml": pubspec(name),
        "analysis_options.yaml": ANALYSIS_OPTIONS,
        **(sources if files is None else files),
    }


def dart_project(
    directory: Path,
    files: Mapping[str, str] | None = None,
    *,
    name: str = "calc",
    dart: Path | None = None,
) -> Path:
    """Write and lock a complete component with `files` into `directory`; return it."""
    write(directory, package_files(name, files))
    lock(directory, dart)
    return directory
