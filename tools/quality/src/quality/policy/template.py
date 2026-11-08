"""What `quality template export` leaves out of a project copy (decision 0016)."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from quality.policy.types import Glob, RelPath, StrictModel


class TemplatePolicy(StrictModel):
    """Maintainer content that a project made from this repository does not receive.

    `exclude` lists the paths the export leaves out. Each file in `fenced`
    loses its maintainer-only regions, the lines between a line containing
    `maintainer-only:begin` and one containing `maintainer-only:end`.
    """

    exclude: list[Glob] = Field(default_factory=list[Glob])
    fenced: list[RelPath] = Field(default_factory=list[RelPath])

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.fenced)) != len(self.fenced):
            msg = "'fenced' must not repeat a file"
            raise ValueError(msg)
        return self
