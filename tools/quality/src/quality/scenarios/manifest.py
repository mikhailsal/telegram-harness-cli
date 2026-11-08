"""Read a component's scenario manifest into a validated model or explain why it is invalid."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from pydantic import ValidationError

from quality.diagnostics import Diagnostic, Location
from quality.policy.load import key_path
from quality.policy.scenarios import ScenarioManifest
from quality.scenarios.rules import MANIFEST

MAX_MANIFEST_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ManifestLoad:
    """Either a manifest or the diagnostics explaining why there is none."""

    manifest: ScenarioManifest | None
    diagnostics: tuple[Diagnostic, ...] = ()


def _invalid(message: str, path: str, component: str, line: int | None = None) -> ManifestLoad:
    location = Location(path, line)
    return ManifestLoad(
        None, (MANIFEST.diagnostic(message, location=location, component=component),)
    )


def parse_manifest(data: bytes, path: str, component: str) -> ManifestLoad:
    """Parse and validate the manifest bytes of a component, read from a repository path."""
    if len(data) > MAX_MANIFEST_BYTES:
        return _invalid("the scenario manifest exceeds 1 MiB", path, component)
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        return _invalid(f"the scenario manifest is not UTF-8: {error.reason}", path, component)
    except tomllib.TOMLDecodeError as error:
        return _invalid(error.msg, path, component, error.lineno)
    except RecursionError:
        return _invalid("the scenario manifest nests too deeply to parse", path, component)
    try:
        manifest = ScenarioManifest.model_validate(document)
    except ValidationError as error:
        location = Location(path)
        return ManifestLoad(
            None,
            tuple(
                MANIFEST.diagnostic(
                    f"{key_path(item['loc'])}: {item['msg']}",
                    location=location,
                    component=component,
                )
                for item in error.errors(include_url=False)
            ),
        )
    if manifest.component != component:
        message = f"the manifest is for '{manifest.component}', not for '{component}'"
        return _invalid(message, path, component)
    return ManifestLoad(manifest)
