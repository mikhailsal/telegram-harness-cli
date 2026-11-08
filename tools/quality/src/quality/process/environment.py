"""Allowlisted child-process environments.

Only the variables below are inherited. Everything else, including `GIT_*`,
`PYTHONPATH`, `NODE_PATH`, `NODE_OPTIONS`, and `PHP_INI_SCAN_DIR`, is dropped so
that ambient developer state cannot redirect source resolution.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

INHERITED = frozenset(
    {
        "HOME",
        "LOGNAME",
        "USER",
        "TMPDIR",
        "TERM",
        "XDG_CACHE_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
)

SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin")

FIXED = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
    "NO_COLOR": "1",
}


def sanitized(
    base: Mapping[str, str] | None = None,
    *,
    path: Sequence[Path] = (),
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child environment from an allowlist plus explicit additions.

    `path` entries are placed before the system directories; the inherited
    `PATH` is not used, so tools resolve only from provisioned locations and
    standard system directories.
    """
    source = os.environ if base is None else base
    env = {key: value for key, value in source.items() if key in INHERITED}
    env.update(FIXED)
    env["PATH"] = os.pathsep.join([*(str(p) for p in path), *SYSTEM_PATH])
    if extra:
        for key, value in extra.items():
            if key == "PATH":
                msg = "PATH must be provided through the `path` argument"
                raise ValueError(msg)
            env[key] = value
    return env
