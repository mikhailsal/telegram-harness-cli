"""Load `quality.toml` into a validated `Policy` or explain why it is invalid."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from quality.diagnostics import Diagnostic, Kind, Location, Rule
from quality.policy.models import Policy

POLICY_FILE = "quality.toml"
MAX_POLICY_BYTES = 1024 * 1024

POLICY_SYNTAX = Rule(
    id="policy.syntax",
    title="Policy file is not valid TOML",
    requirement="R02",
    explanation="The policy file must be readable UTF-8 TOML no larger than 1 MiB.",
    remedy="Fix the TOML syntax at the reported position.",
    kind=Kind.USAGE,
)
POLICY_INVALID = Rule(
    id="policy.invalid",
    title="Policy value is invalid",
    requirement="R02",
    explanation="Every policy value is validated against the generated policy JSON Schema.",
    remedy="Correct the value at the reported key path; see docs/reference/policy.schema.json.",
    kind=Kind.USAGE,
)
POLICY_UNKNOWN_KEY = Rule(
    id="policy.unknown-key",
    title="Policy contains an unknown key",
    requirement="R02",
    explanation="Unknown keys are rejected so that misspelled settings cannot be silently ignored.",
    remedy="Remove the key or correct its spelling.",
    kind=Kind.USAGE,
)
POLICY_MISSING = Rule(
    id="policy.missing",
    title="Policy file is missing",
    requirement="R02",
    explanation="Every repository using the gate has a quality.toml at its root.",
    remedy="Restore quality.toml from the template.",
    kind=Kind.USAGE,
)
RULES = (POLICY_SYNTAX, POLICY_INVALID, POLICY_UNKNOWN_KEY, POLICY_MISSING)


@dataclass(frozen=True, slots=True)
class LoadResult:
    """Either a policy or the diagnostics explaining why there is none."""

    policy: Policy | None
    diagnostics: tuple[Diagnostic, ...]


def key_path(loc: Sequence[int | str]) -> str:
    """Render a validation location such as `components[0].root`."""
    rendered = ""
    for part in loc:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else part
    return rendered or "<root>"


def _validation_diagnostics(error: ValidationError, path: str) -> tuple[Diagnostic, ...]:
    found: list[Diagnostic] = []
    for item in error.errors(include_url=False):
        key = key_path(item["loc"])
        if item["type"] == "extra_forbidden":
            found.append(
                POLICY_UNKNOWN_KEY.diagnostic(f"unknown key '{key}'", location=Location(path))
            )
        else:
            found.append(
                POLICY_INVALID.diagnostic(f"{key}: {item['msg']}", location=Location(path))
            )
    return tuple(found)


def parse_policy(data: bytes, path: str = POLICY_FILE) -> LoadResult:
    """Parse and validate policy bytes."""
    if len(data) > MAX_POLICY_BYTES:
        return LoadResult(
            None, (POLICY_SYNTAX.diagnostic("policy file exceeds 1 MiB", location=Location(path)),)
        )
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        message = f"policy file is not UTF-8: {exc.reason}"
        return LoadResult(None, (POLICY_SYNTAX.diagnostic(message, location=Location(path)),))
    except tomllib.TOMLDecodeError as exc:
        location = Location(path, exc.lineno, exc.colno)
        return LoadResult(None, (POLICY_SYNTAX.diagnostic(exc.msg, location=location),))
    except RecursionError:
        message = "policy file nests too deeply to parse"
        return LoadResult(None, (POLICY_SYNTAX.diagnostic(message, location=Location(path)),))
    try:
        policy = Policy.model_validate(document)
    except ValidationError as exc:
        return LoadResult(None, _validation_diagnostics(exc, path))
    return LoadResult(policy, ())


def load_policy(root: Path) -> LoadResult:
    """Load the policy file from a repository or snapshot root."""
    path = root / POLICY_FILE
    if not path.is_file():
        message = f"{POLICY_FILE} not found at the repository root"
        return LoadResult(
            None, (POLICY_MISSING.diagnostic(message, location=Location(POLICY_FILE)),)
        )
    with path.open("rb") as stream:
        return parse_policy(stream.read(MAX_POLICY_BYTES + 1))
