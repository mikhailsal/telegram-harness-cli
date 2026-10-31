"""JSON Schema export of the policy and scenario manifest models, kept in the repository."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import MappingProxyType

from pydantic import BaseModel

from quality.diagnostics import Rule
from quality.policy.models import Policy
from quality.policy.scenarios import ScenarioManifest

SCHEMA_PATH = "docs/reference/policy.schema.json"
SCENARIOS_SCHEMA_PATH = "docs/reference/scenarios.schema.json"

SCHEMA_STALE = Rule(
    id="policy.schema-stale",
    title="Committed policy schema differs from the models",
    requirement="R02",
    explanation=(
        "The committed JSON Schemas of quality.toml and of scenario manifests are "
        "generated from the models and must match them."
    ),
    remedy="Run 'quality policy schema --write' and commit the result.",
)
RULES = (SCHEMA_STALE,)


def _schema_text(model: type[BaseModel], title: str) -> str:
    schema = model.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def policy_schema_text() -> str:
    """Render the policy JSON Schema deterministically."""
    return _schema_text(Policy, "quality.toml")


def scenarios_schema_text() -> str:
    """Render the scenario manifest JSON Schema deterministically."""
    return _schema_text(ScenarioManifest, "scenario manifest")


SCHEMAS: MappingProxyType[str, Callable[[], str]] = MappingProxyType(
    {SCHEMA_PATH: policy_schema_text, SCENARIOS_SCHEMA_PATH: scenarios_schema_text}
)
