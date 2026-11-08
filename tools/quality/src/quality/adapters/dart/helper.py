"""The Dart helper: structure and imports from the pinned package:analyzer (decision 0012).

The helper is a Dart program in `tools/quality/helpers/dart`, located relative
to this package so a materialized candidate core uses its own helper. Starting
it from source takes about 16 seconds, so it runs as a kernel snapshot compiled
once into the cache, named by a digest of the helper's pubspec.yaml,
pubspec.lock, `bin/` and `lib/` files and the SDK version: a changed helper or
SDK gets a new snapshot, and a stale one is never used.

The build works in a temporary copy of those files inside the cache directory.
pub resolves the helper's own lockfile there, offline for a check and online
for bootstrap, and the compiled snapshot is renamed into place only when it is
complete, so a concurrent run never sees part of one. Requests and responses
follow the protocol of the TypeScript helper (`helperio`).
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters import helperio
from quality.adapters.dart.environment import Sdk, pub_get
from quality.adapters.helperio import Entry, Fields
from quality.adapters.native import fail, run_tool
from quality.boundedio import JsonValue
from quality.process import Command

# src/quality/adapters/dart/helper.py -> tools/quality
CORE = Path(__file__).resolve().parents[4]
HELPER = CORE / "helpers" / "dart"
MAIN = "bin/main.dart"
MANIFESTS = ("pubspec.yaml", "pubspec.lock")
SOURCES = ("bin", "lib")
BUILD_TIMEOUT = 900.0
TIMEOUT = 1800.0
TOOL = "dart helper"
FIELDS = Fields(TOOL)


def helper_files(source: Path) -> list[str]:
    """Return the helper's files that its snapshot is built from, relative to `source`."""
    files = [name for name in MANIFESTS if (source / name).is_file()]
    for directory in SOURCES:
        found = (path for path in (source / directory).rglob("*") if path.is_file())
        files += sorted(path.relative_to(source).as_posix() for path in found)
    return files


def digest(source: Path, version: bytes) -> str:
    """Return the digest of the helper's files and the SDK version."""
    hasher = hashlib.sha256(b"dart-helper\0" + version + b"\0")
    for name in helper_files(source):
        data = (source / name).read_bytes()
        hasher.update(f"{name}\0{len(data)}\0".encode())
        hasher.update(data)
    return hasher.hexdigest()


@dataclass(slots=True)
class Helper:
    """The helper's sources and the cache directory of its compiled snapshots."""

    source: Path
    cache: Path
    _programs: dict[Path, Path] = field(default_factory=dict[Path, Path])

    def _build(self, sdk: Sdk, target: Path, *, offline: bool) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.cache, prefix="build-") as scratch:
            build, home = Path(scratch) / "helper", Path(scratch) / "home"
            for name in helper_files(self.source):
                copy = build / name
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.source / name, copy)
            pub_get(sdk, build, home, label=TOOL, offline=offline)
            output = Path(scratch) / "helper.dill"
            argv = sdk.command("compile", "kernel", MAIN, "-o", str(output))
            run_tool(sdk.run_in(argv, build, home, timeout=BUILD_TIMEOUT), name=TOOL, accepted={0})
            if not output.is_file():
                fail(TOOL, f"`dart compile kernel` wrote no snapshot at {output}")
            output.replace(target)

    def program(self, sdk: Sdk, *, offline: bool = True) -> Path:
        """Return the compiled helper for this SDK, building it when the cache lacks it."""
        known = self._programs.get(sdk.executable)
        if known is not None and known.is_file():
            return known
        if not (self.source / MAIN).is_file():
            fail(TOOL, f"the helper is missing at {self.source / MAIN}")
        if not sdk.version_file.is_file():
            fail(TOOL, f"the SDK has no version file at {sdk.version_file}")
        target = self.cache / f"{digest(self.source, sdk.version_file.read_bytes())}.dill"
        if not target.is_file():
            self._build(sdk, target, offline=offline)
        self._programs[sdk.executable] = target
        return target

    def run(
        self, sdk: Sdk, command: str, request: Mapping[str, JsonValue], *, work: Path, cwd: Path
    ) -> Entry:
        """Run one helper command and return its checked response."""
        program = self.program(sdk)
        handle = helperio.write_request(work / "dart-requests", command, request)
        argv = (str(sdk.executable), str(program), command, str(handle))
        env = sdk.environ(work / "dart-home" / "helper")
        completed = run_tool(
            Command(argv, cwd=cwd, timeout=TIMEOUT, env=env, reap=True), name=TOOL, accepted={0}
        )
        return helperio.read_response(TOOL, command, completed)
