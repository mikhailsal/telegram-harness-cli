"""The operations of an OpenAPI document, which HTTP scenarios must cover (R08.5).

An operation is named by its `operationId`, or by its method and path, such as
`GET /notes/{id}`, when it has none. Only the path items of the document
count; references to other files are not followed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from quality.adapters.settings import Table, load_json_object, load_yaml_mapping, table
from quality.boundedio import ReportError

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
READERS = {".json": load_json_object, ".yaml": load_yaml_mapping, ".yml": load_yaml_mapping}


def _operation(route: str, method: str, operation: object, name: str) -> str:
    if not isinstance(operation, Mapping):
        msg = f"{name}: '{method}' of '{route}' is not an operation object"
        raise ReportError(msg)
    identifier = cast(Table, operation).get("operationId")
    if isinstance(identifier, str) and identifier.strip():
        return identifier
    return f"{method.upper()} {route}"


def operations(document: Table, name: str) -> tuple[str, ...]:
    """Return the operations of a parsed document, in document order."""
    paths = table(document, "paths")
    if not ({"openapi", "swagger"} & document.keys()) or paths is None:
        msg = f"{name} is not an OpenAPI document with a 'paths' object"
        raise ReportError(msg)
    found: list[str] = []
    for route, item in paths.items():
        if not isinstance(item, Mapping):
            msg = f"{name}: the path item '{route}' is not an object"
            raise ReportError(msg)
        entries = cast(Table, item)
        found.extend(
            _operation(route, method, entries[method], name)
            for method in METHODS
            if method in entries
        )
    repeated = sorted(key for key, count in Counter(found).items() if count > 1)
    if repeated:
        msg = f"{name} names several operations {', '.join(repeated)}"
        raise ReportError(msg)
    return tuple(found)


def load_operations(path: Path) -> tuple[str, ...]:
    """Read a JSON or YAML OpenAPI document; raise ReportError when it is unusable."""
    reader = READERS.get(path.suffix)
    if reader is None:
        msg = f"{path.name} must be a .json, .yaml, or .yml OpenAPI document"
        raise ReportError(msg)
    return operations(reader(path), path.name)
