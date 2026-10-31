"""`quality policy`: validate the policy file and maintain its JSON Schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.diagnostics import ExitCode, Location, Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.policy import cross_validate, load_policy
from quality.policy.schema import (
    SCENARIOS_SCHEMA_PATH,
    SCHEMA_PATH,
    SCHEMA_STALE,
    SCHEMAS,
    policy_schema_text,
    scenarios_schema_text,
)

NAME = "policy"
ACTIONS = frozenset({"validate", "schema"})


def register(commands: Subcommands) -> None:
    """Add the `policy` subcommand group."""
    parser = commands.add_parser(
        NAME,
        help="validate quality.toml and maintain its schema",
        description="Validate the repository policy and maintain its generated JSON Schema.",
    )
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    validate = actions.add_parser(
        "validate",
        help="validate quality.toml",
        description="Validate quality.toml against the schema and cross-field rules. "
        "Exit 0 when valid, 2 when the policy is missing or invalid.",
    )
    validate.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    validate.add_argument("--json", action="store_true", help="print a JSON report")
    validate.set_defaults(handler=run_validate)
    schema = actions.add_parser(
        "schema",
        help="print, write, or check the policy and scenario manifest JSON Schemas",
        description=f"Print the policy JSON Schema (or with --scenarios the scenario "
        f"manifest schema), write both to {SCHEMA_PATH} and {SCENARIOS_SCHEMA_PATH}, or "
        "check that the committed copies are current (exit 1 when stale).",
    )
    schema.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    schema.add_argument(
        "--scenarios", action="store_true", help="print the scenario manifest schema"
    )
    mode = schema.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write both schemas")
    mode.add_argument("--check", action="store_true", help="fail if a schema is stale")
    schema.set_defaults(handler=run_schema)


def run_validate(args: argparse.Namespace) -> int:
    """Validate the schema and cross-field rules of quality.toml."""
    root = Path(args.root).resolve()
    result = load_policy(root)
    diagnostics = list(result.diagnostics)
    if result.policy is not None:
        diagnostics.extend(cross_validate(result.policy, build_registry().ids()))
    outcome = Outcome.of(diagnostics)
    text = (
        outcome_to_json(outcome, command="policy validate") if args.json else render_text(outcome)
    )
    sys.stdout.write(text)
    return int(outcome.exit_code())


def run_schema(args: argparse.Namespace) -> int:
    """Print, write, or check the generated JSON Schemas."""
    root = Path(args.root).resolve()
    if args.write:
        for path, render in SCHEMAS.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render(), encoding="utf-8")
        return int(ExitCode.OK)
    if args.check:
        stale = [
            SCHEMA_STALE.diagnostic("schema is missing or stale", location=Location(path))
            for path, render in SCHEMAS.items()
            if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != render()
        ]
        if not stale:
            return int(ExitCode.OK)
        outcome = Outcome.of(stale)
        sys.stdout.write(render_text(outcome))
        return int(outcome.exit_code())
    sys.stdout.write(scenarios_schema_text() if args.scenarios else policy_schema_text())
    return int(ExitCode.OK)
