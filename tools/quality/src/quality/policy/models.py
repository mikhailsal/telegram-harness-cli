"""Typed policy models; unknown keys and invalid values are rejected."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from quality.policy.browser import BrowserSettings, Declaration
from quality.policy.ci import AuditPolicy, CiPolicy
from quality.policy.enums import (
    Bundler,
    Capability,
    Category,
    Enhancement,
    ExecutionTarget,
    Framework,
    Profile,
    Role,
    Suite,
)
from quality.policy.template import TemplatePolicy
from quality.policy.types import (
    Date,
    Flag,
    Glob,
    Identifier,
    Name,
    Percent,
    PositiveInt,
    RelPath,
    RootPath,
    RuleId,
    StrictModel,
    Text,
)


class Repository(StrictModel):
    """Repository-wide settings."""

    mode: Literal["project", "maintainer"] = "project"
    default_branch: Text = "main"


class Limit(StrictModel):
    """A warning and/or error threshold; values above the threshold trigger."""

    warning: PositiveInt | None = None
    error: PositiveInt | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.warning is None and self.error is None:
            msg = "at least one of 'warning' or 'error' is required"
            raise ValueError(msg)
        if self.warning is not None and self.error is not None and self.warning >= self.error:
            msg = "'warning' must be lower than 'error'"
            raise ValueError(msg)
        return self


class Thresholds(StrictModel):
    """Structural and repository thresholds (R04)."""

    source_file_lines: Limit
    function_code_lines: Limit
    cyclomatic_complexity: Limit
    nesting_depth: Limit
    parameters: Limit
    doc_page_lines: PositiveInt
    doc_page_words: PositiveInt
    agents_lines: PositiveInt
    agents_words: PositiveInt
    blob_bytes: PositiveInt


class CoveragePolicy(StrictModel):
    """Blocking unit coverage minima in percent (R06)."""

    line: Percent
    branch: Percent
    changed_lines: Percent
    new_file_must_be_covered: Flag


class Suites(StrictModel):
    """Test roots per suite, relative to the component root."""

    unit: list[RelPath] = Field(default_factory=list[RelPath])
    integration: list[RelPath] = Field(default_factory=list[RelPath])
    contract: list[RelPath] = Field(default_factory=list[RelPath])
    e2e: list[RelPath] = Field(default_factory=list[RelPath])
    acceptance: list[RelPath] = Field(default_factory=list[RelPath])

    def all_paths(self) -> list[str]:
        """Every declared test root of every suite."""
        return [*self.unit, *self.integration, *self.contract, *self.e2e, *self.acceptance]

    def of(self, suite: Suite) -> list[str]:
        """Return the test roots of one suite."""
        roots = {
            Suite.UNIT: self.unit,
            Suite.INTEGRATION: self.integration,
            Suite.CONTRACT: self.contract,
            Suite.E2E: self.e2e,
            Suite.ACCEPTANCE: self.acceptance,
        }
        return roots[suite]


class ModuleDecl(StrictModel):
    """A named module: file globs relative to the component root."""

    name: Identifier
    paths: list[Glob] = Field(min_length=1)


class Edge(StrictModel):
    """A forbidden dependency from one module to another."""

    source: Identifier = Field(alias="from")
    target: Identifier = Field(alias="to")


class Architecture(StrictModel):
    """Declared modules, layers, and forbidden dependency edges; cycles always fail.

    `layers` lists module names from the top down: a module may depend on modules
    listed after it, never on modules listed before it.
    """

    modules: list[ModuleDecl] = Field(default_factory=list[ModuleDecl])
    layers: list[Identifier] = Field(default_factory=list[Identifier])
    forbidden: list[Edge] = Field(default_factory=list[Edge])


class Component(StrictModel):
    """A unit with its own profile, roots, and gates.

    A TypeScript component runs on Node unless `target` is `"browser"`
    (decision 0017); `framework`, `bundler`, and `browser` belong to that target.
    """

    id: Identifier
    role: Role
    profile: Profile
    target: ExecutionTarget | None = None
    framework: Framework | None = None
    bundler: Bundler | None = None
    browser: BrowserSettings | None = None
    root: RootPath
    source: list[RelPath] = Field(min_length=1)
    tests: Suites
    capabilities: list[Capability] = Field(default_factory=list[Capability])
    no_boundary_reason: Text | None = None
    scenarios: RelPath | None = None
    enhanced: list[Enhancement] = Field(default_factory=list[Enhancement])
    depends_on: list[Identifier] = Field(default_factory=list[Identifier])
    architecture: Architecture = Field(default_factory=Architecture)

    @model_validator(mode="after")
    def _declared(self) -> Self:
        if not self.capabilities and self.no_boundary_reason is None:
            msg = "declare 'capabilities' or justify their absence in 'no_boundary_reason'"
            raise ValueError(msg)
        if self.capabilities and self.no_boundary_reason is not None:
            msg = "'no_boundary_reason' is only allowed when 'capabilities' is empty"
            raise ValueError(msg)
        problem = Declaration(
            self.profile,
            self.target,
            self.framework,
            self.bundler,
            self.browser,
            (*self.capabilities,),
        ).problem()
        if problem is not None:
            raise ValueError(problem)
        return self

    @property
    def in_browser(self) -> bool:
        """Whether the component is a browser application (decision 0017)."""
        return self.target is ExecutionTarget.BROWSER

    @property
    def browser_settings(self) -> BrowserSettings:
        """The declared browser settings, or their defaults."""
        return self.browser or BrowserSettings()


class Classification(StrictModel):
    """An explicit category for files matching a narrow glob."""

    pattern: Glob
    category: Category
    reason: Text


class PolicyException(StrictModel):
    """A scoped, owned, and reviewed exception to one rule."""

    rule: RuleId
    path: RelPath
    symbol: Text | None = None
    reason: Text
    owner: Text
    expires: Date | None = None
    review: Date | None = None

    @model_validator(mode="after")
    def _dated(self) -> Self:
        if self.expires is None and self.review is None:
            msg = "temporary exceptions need 'expires'; permanent ones need a 'review' date"
            raise ValueError(msg)
        return self


class Quarantine(StrictModel):
    """A flaky test whose failure does not fail its suite until the entry expires.

    A quarantined test never satisfies a scenario; the scenarios it lists are
    suspended instead of failing while the entry lasts.
    """

    component: Identifier
    suite: Suite
    test: Text
    reason: Text
    owner: Text
    expires: Date
    scenarios: list[Name] = Field(default_factory=list[Name])


class Policy(StrictModel):
    """The complete repository policy loaded from `quality.toml`."""

    schema_version: Literal[1]
    repository: Repository
    thresholds: Thresholds
    coverage: CoveragePolicy
    components: list[Component] = Field(min_length=1)
    classification: list[Classification] = Field(default_factory=list[Classification])
    exceptions: list[PolicyException] = Field(default_factory=list[PolicyException])
    quarantine: list[Quarantine] = Field(default_factory=list[Quarantine])
    ci: CiPolicy | None = None
    audit: AuditPolicy = Field(default_factory=AuditPolicy)
    template: TemplatePolicy = Field(default_factory=TemplatePolicy)
