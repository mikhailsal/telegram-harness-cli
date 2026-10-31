"""Strict JSON parsing with a nesting bound that holds on every machine.

The standard decoder stops deep input when the C stack runs low, so the same
document can parse on one machine and fail on another. Documents that nest
containers deeper than `MAX_NESTING` levels fail everywhere instead, and the
non-standard constants NaN and Infinity are rejected.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

MAX_NESTING = 512
TOO_DEEP = f"containers nest deeper than {MAX_NESTING} levels"

type PairsHook = Callable[[list[tuple[str, object]]], object]


def _reject_constant(name: str) -> None:
    msg = f"non-standard JSON constant {name}"
    raise ValueError(msg)


def _members(value: object) -> list[object]:
    if isinstance(value, dict):
        return list(cast(dict[str, object], value).values())
    if isinstance(value, list):
        return cast(list[object], value)
    return []


def loads(text: str, pairs: PairsHook | None = None) -> object:
    """Parse strict JSON; raise ValueError when it is malformed or nests too deeply."""
    try:
        value: object = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=pairs)
    except RecursionError as error:
        raise ValueError(TOO_DEEP) from error
    level = [value]
    for _ in range(MAX_NESTING):
        level = [member for item in level for member in _members(item)]
    if any(isinstance(item, dict | list) for item in level):
        raise ValueError(TOO_DEEP)
    return value
