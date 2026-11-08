"""Configuration syntax: TOML, JSON, and YAML files must parse (R04)."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Iterator
from pathlib import PurePosixPath

import yaml

from quality import strictjson
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Category
from quality.rules.model import CandidateFile

SYNTAX = Rule(
    id="config.syntax",
    title="Configuration file does not parse",
    requirement="R04",
    explanation=(
        "TOML, JSON, and YAML configuration must parse with a strict parser; JSON objects "
        "must not repeat keys. Files that tools read as JSON with comments (tsconfig, "
        "jsconfig, .jsonc, editor settings) are left to their own tools."
    ),
    remedy="Fix the syntax error at the reported position.",
)
RULES = (SYNTAX,)

JSONC_NAMES = re.compile(r"^(?:tsconfig|jsconfig)(?:\.[\w-]+)*\.json$|^\.eslintrc\.json$")
JSONC_DIRECTORIES = frozenset({".vscode", ".devcontainer"})
# TOML and YAML parsers recurse in Python and stop at its recursion limit.
TOO_DEEP = "the document nests too deeply to parse"


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            msg = f"duplicate key '{key}'"
            raise ValueError(msg)
        result[key] = value
    return result


def _json(text: str) -> tuple[int | None, str] | None:
    try:
        strictjson.loads(text, _unique_pairs)
    except json.JSONDecodeError as error:
        return error.lineno, error.msg
    except ValueError as error:
        return None, str(error)
    return None


def _toml(text: str) -> tuple[int | None, str] | None:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return error.lineno, error.msg
    except RecursionError:
        return None, TOO_DEEP
    return None


def _yaml(text: str) -> tuple[int | None, str] | None:
    try:
        for _ in yaml.safe_load_all(text):
            pass
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark
        return (mark.line + 1 if mark is not None else None), str(error.problem)
    except yaml.YAMLError as error:
        return None, str(error)
    except RecursionError:
        return None, TOO_DEEP
    return None


type Parser = Callable[[str], tuple[int | None, str] | None]
PARSERS: dict[str, Parser] = {".json": _json, ".toml": _toml, ".yaml": _yaml, ".yml": _yaml}


def _parser(path: str) -> Parser | None:
    pure = PurePosixPath(path)
    if JSONC_NAMES.match(pure.name) or JSONC_DIRECTORIES.intersection(pure.parts[:-1]):
        return None
    return PARSERS.get(pure.suffix.lower())


def check_config(files: Iterable[CandidateFile]) -> Iterator[Diagnostic]:
    """Parse every TOML, JSON, and YAML configuration file of the candidate."""
    for file in files:
        parser = _parser(file.path) if file.category is Category.CONFIG else None
        text = file.text() if parser is not None else None
        if parser is None or text is None:
            continue
        problem = parser(text)
        if problem is not None:
            line, message = problem
            yield SYNTAX.diagnostic(
                message, location=Location(file.path, line), component=file.record.component
            )
