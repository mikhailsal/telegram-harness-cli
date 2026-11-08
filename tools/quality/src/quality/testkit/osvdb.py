"""A synthetic offline OSV database and lockfiles for audit tests that never use the network."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from quality.toolchain import cache_root, load_manifest, locate

SCANNER = "osv-scanner"
# CVSS 3.1 vectors with known base scores.
CRITICAL = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"  # 9.8
MEDIUM = "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N"  # 5.4


@dataclass(frozen=True, slots=True)
class Advisory:
    """One synthetic advisory: the package versions below `fixed` are affected."""

    id: str
    package: str
    fixed: str
    vector: str | None = CRITICAL
    ecosystem: str = "PyPI"
    aliases: tuple[str, ...] = ()

    def document(self) -> dict[str, object]:
        """Return the advisory in the OSV schema."""
        affected = {
            "package": {"ecosystem": self.ecosystem, "name": self.package},
            "ranges": [
                {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": self.fixed}]}
            ],
        }
        document: dict[str, object] = {
            "schema_version": "1.6.0",
            "id": self.id,
            "modified": "2026-01-01T00:00:00Z",
            "summary": f"Synthetic advisory {self.id}",
            "aliases": list(self.aliases),
            "affected": [affected],
        }
        if self.vector is not None:
            document["severity"] = [{"type": "CVSS_V3", "score": self.vector}]
        return document


def advisory_db(directory: Path, *advisories: Advisory) -> Path:
    """Write the advisories as osv-scanner's offline database and return its directory."""
    by_ecosystem: dict[str, list[Advisory]] = {"PyPI": []}
    for advisory in advisories:
        by_ecosystem.setdefault(advisory.ecosystem, []).append(advisory)
    for ecosystem, entries in by_ecosystem.items():
        archive = directory / "osv-scalibr" / ecosystem / "all.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as bundle:
            for entry in entries:
                bundle.writestr(f"{entry.id}.json", json.dumps(entry.document()))
    return directory


def uv_lock(**packages: str) -> str:
    """Return a uv.lock of a virtual project that depends on each package at its version."""
    dependencies = "".join(f'    {{ name = "{name}" }},\n' for name in packages)
    project = '[[package]]\nname = "demo"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
    lines = [
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n',
        f"{project}dependencies = [\n{dependencies}]\n",
    ]
    lines.extend(
        f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        for name, version in packages.items()
    )
    return "\n".join(lines)


def pinned_scanner() -> Path:
    """Return the pinned osv-scanner from the local cache; fail loudly when it is missing."""
    return locate(SCANNER, load_manifest().tools[SCANNER], cache_root())
