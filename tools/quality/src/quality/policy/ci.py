"""Policy of the CI wrapper and the dependency audit of merge CI (decision 0015)."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from quality.policy.types import Glob, Identifier, RelPath, Score, StrictModel

WORKFLOW = re.compile(r"\.github/workflows/[^/]+\.ya?ml")


class CiPolicy(StrictModel):
    """The workflow whose aggregate job is the required status, and the owned control files.

    `required_jobs` are the jobs the aggregate job must find successful;
    `control` adds paths to those derived from the policy that need owner
    review in `codeowners`.
    """

    workflow: RelPath
    aggregate_job: Identifier = "required"
    required_jobs: list[Identifier] = Field(min_length=1)
    codeowners: RelPath = ".github/CODEOWNERS"
    control: list[Glob] = Field(default_factory=list[Glob])

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if WORKFLOW.fullmatch(self.workflow) is None:
            msg = (
                "'workflow' must be a file directly under .github/workflows ending in .yml or .yaml"
            )
            raise ValueError(msg)
        if len(set(self.required_jobs)) != len(self.required_jobs):
            msg = "'required_jobs' must not repeat a job"
            raise ValueError(msg)
        if self.aggregate_job in self.required_jobs:
            msg = "the aggregate job cannot be one of the jobs it requires"
            raise ValueError(msg)
        return self


class AuditPolicy(StrictModel):
    """Which advisories block: a CVSS score at or above `blocking_score`.

    Lower scores are warnings. An advisory without a score is an error unless
    `unscored` is "warning".
    """

    blocking_score: Score = 7.0
    unscored: Literal["error", "warning"] = "error"
