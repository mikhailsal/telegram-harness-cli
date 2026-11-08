"""Constrained scalar types and the strict base model of the policy models."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, Strict

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_RULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")
_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_WILDCARDS = frozenset("*?[]")


def _check_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        msg = "must be lowercase kebab-case starting with a letter"
        raise ValueError(msg)
    return value


def _check_rule_id(value: str) -> str:
    if not _RULE_ID.fullmatch(value):
        msg = "must be a dotted rule ID such as 'coverage.line'"
        raise ValueError(msg)
    return value


def _check_name(value: str) -> str:
    if not _NAME.fullmatch(value):
        msg = "must be lowercase words joined by '.' or '-', starting with a letter"
        raise ValueError(msg)
    return value


def _segments(value: str) -> list[str]:
    if not value or value.startswith("/") or "\\" in value or "\0" in value:
        msg = "must be a relative POSIX path"
        raise ValueError(msg)
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        msg = "must be normalized: no empty, '.', or '..' segments and no trailing '/'"
        raise ValueError(msg)
    return parts


def _check_relpath(value: str) -> str:
    if any(ch in _WILDCARDS for ch in value):
        msg = "must be an exact path without wildcards"
        raise ValueError(msg)
    _segments(value)
    return value


def _check_root(value: str) -> str:
    return value if value == "." else _check_relpath(value)


def _check_glob(value: str) -> str:
    parts = _segments(value)
    if any(ch in _WILDCARDS for ch in parts[0]):
        msg = "must start with a literal directory or file name; leading wildcards are too broad"
        raise ValueError(msg)
    return value


def _check_text(value: str) -> str:
    if not value.strip():
        msg = "must not be blank"
        raise ValueError(msg)
    return value


Identifier = Annotated[str, Strict(), AfterValidator(_check_identifier)]
RuleId = Annotated[str, Strict(), AfterValidator(_check_rule_id)]
Name = Annotated[str, Strict(), AfterValidator(_check_name)]
RelPath = Annotated[str, Strict(), AfterValidator(_check_relpath)]
RootPath = Annotated[str, Strict(), AfterValidator(_check_root)]
Glob = Annotated[str, Strict(), AfterValidator(_check_glob)]
Text = Annotated[str, Strict(), AfterValidator(_check_text)]
Percent = Annotated[int, Strict(), Field(ge=0, le=100)]
PositiveInt = Annotated[int, Strict(), Field(gt=0)]
Score = Annotated[float, Strict(), Field(ge=0, le=10)]
Flag = Annotated[bool, Strict()]
Date = Annotated[date, Strict()]


class StrictModel(BaseModel):
    """Base model: frozen, unknown keys forbidden; scalar types are strict."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
