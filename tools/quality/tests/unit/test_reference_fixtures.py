from pathlib import Path

import pytest

from quality.scenarios.manifest import parse_manifest
from quality.scenarios.openapi import load_operations
from quality.scenarios.validate import Subject, manifest_findings
from quality.testkit import greetfixture, notesfixture
from quality.testkit.policies import ComponentSpec, build_policy

FIXTURES = [(greetfixture.SPEC, greetfixture.FILES), (notesfixture.SPEC, notesfixture.FILES)]


@pytest.mark.parametrize(("spec", "files"), FIXTURES, ids=["greet", "notes"])
def test_the_reference_manifests_meet_every_obligation(
    tmp_path: Path, spec: ComponentSpec, files: dict[str, str]
) -> None:
    component = build_policy(spec).components[0]
    loaded = parse_manifest(files["scenarios.toml"].encode(), "scenarios.toml", spec.id)
    assert loaded.manifest is not None, loaded.diagnostics
    operations = None
    if loaded.manifest.openapi is not None:
        document = tmp_path / loaded.manifest.openapi
        document.write_text(files[loaded.manifest.openapi], encoding="utf-8")
        operations = load_operations(document)
    subject = Subject(component, loaded.manifest, "scenarios.toml", operations)
    assert manifest_findings(subject, ()) == []
