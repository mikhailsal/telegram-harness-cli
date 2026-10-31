"""Typed models of a component's scenario manifest (decision 0014).

A manifest lists the behaviors a component must prove, each mapped to native
test cases of one suite, and the stage at which the proof is mandatory.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from quality.policy.enums import Capability, Enhancement, Suite
from quality.policy.models import StrictModel
from quality.policy.types import Identifier, Name, RelPath, Text


class Stage(StrEnum):
    """When a scenario is mandatory: before every commit, or before a merge."""

    COMMIT = "commit"
    MERGE = "merge"


def _check_selector(value: str) -> str:
    if "*" in value[:-1] or value == "*":
        msg = "must be a test case name, or a prefix of one followed by a single trailing '*'"
        raise ValueError(msg)
    return value


Selector = Annotated[Text, AfterValidator(_check_selector)]


class Scenario(StrictModel):
    """One required behavior and the native test cases that prove it.

    `tests` are case names as the suite's results report them, or prefixes
    ending in `*` that must select at least one case. `boundary` names what the
    tests exercise, such as a browser route that simulates the backend; every
    scenario of a browser component names it (decision 0018).
    """

    id: Name
    summary: Text
    capability: Capability
    level: Suite
    stage: Stage
    tests: list[Selector] = Field(min_length=1)
    requirements: list[Name] = Field(default_factory=list[Name])
    operations: list[Text] = Field(default_factory=list[Text])
    enhanced: Enhancement | None = None
    boundary: Text | None = None


class NotApplicable(StrictModel):
    """A requirement of a declared capability that does not apply, and why."""

    requirement: Name
    reason: Text


class Retired(StrictModel):
    """A scenario ID that was deliberately removed, and why."""

    id: Name
    reason: Text


class ScenarioManifest(StrictModel):
    """The scenario manifest of one component."""

    schema_version: Literal[1]
    component: Identifier
    openapi: RelPath | None = None
    scenarios: list[Scenario] = Field(default_factory=list[Scenario])
    not_applicable: list[NotApplicable] = Field(default_factory=list[NotApplicable])
    retired: list[Retired] = Field(default_factory=list[Retired])
