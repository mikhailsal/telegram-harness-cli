"""The registry of every rule known to this build of the gate."""

from __future__ import annotations

from quality import ci, discovery, engine, gitio, hooks, reports, template, toolchain
from quality.adapters import markdown, native, secrets
from quality.adapters.dart import profile as dart
from quality.adapters.php import profile as php
from quality.adapters.python import profile
from quality.adapters.typescript import profile as typescript
from quality.diagnostics import RuleRegistry
from quality.policy import load, schema, validate
from quality.rules import (
    architecture,
    config,
    coverage,
    docs,
    exceptions,
    external,
    files,
    quarantine,
    regressions,
    structure,
    suppressions,
    testing,
)
from quality.scenarios import rules as scenarios


def build_registry() -> RuleRegistry:
    """Collect the rules declared by every feature module."""
    return RuleRegistry(
        [
            *load.RULES,
            *validate.RULES,
            *schema.RULES,
            *gitio.RULES,
            *discovery.RULES,
            *files.RULES,
            *config.RULES,
            *suppressions.RULES,
            *docs.RULES,
            *structure.RULES,
            *architecture.RULES,
            *exceptions.RULES,
            *testing.RULES,
            *quarantine.RULES,
            *scenarios.RULES,
            *coverage.RULES,
            *regressions.RULES,
            *external.RULES,
            *markdown.RULES,
            *secrets.RULES,
            *native.RULES,
            *toolchain.RULES,
            *engine.RULES,
            *profile.RULES,
            *typescript.RULES,
            *dart.RULES,
            *php.RULES,
            *hooks.RULES,
            *reports.RULES,
            *ci.RULES,
            *template.RULES,
        ]
    )
