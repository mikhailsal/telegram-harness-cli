"""Loopback-only networking for contained suites (R06.9, decision 0008).

Unit tests and owned browser suites may use loopback for instrumentation,
such as the Dart VM service, and no other network. `isolated` puts a stub on
the core interpreter in front of a command. The stub enters a new user and
network namespace, maps the
caller's own user and group into it, brings the loopback interface up, and
replaces itself with the command. The command keeps the stub's process, so
timeouts and reaping work unchanged; it runs as the caller, without
capabilities, in a namespace whose only interface is loopback, so every other
address is unreachable. The stub runs with `-I`, so a module planted in the
snapshot's working directory is never imported.

Unprivileged user namespaces can be turned off, for example by Ubuntu's
AppArmor restriction. `unavailable` probes the stub once per process and says
why it cannot isolate; `contained` then leaves the command as it is and returns
that reason, which the engine reports as a warning instead of failing the gate
on such a machine.
"""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path

from quality.process.runner import Command, ProcessError, run

PROBE_TIMEOUT = 30.0
# SIOCGIFFLAGS and SIOCSIFFLAGS read and write an interface's flags through a
# `struct ifreq` (16-byte name, then the flags in a 24-byte union); IFF_UP is 1.
STUB = """\
import fcntl, os, socket, struct, sys
uid, gid = os.getuid(), os.getgid()
try:
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)
    maps = (("setgroups", "deny"), ("uid_map", f"{uid} {uid} 1"), ("gid_map", f"{gid} {gid} 1"))
    for name, text in maps:
        with open(f"/proc/self/{name}", "w") as file:
            file.write(text)
    with socket.socket() as handle:
        request = struct.pack("16sH22x", b"lo", 0)
        flags = struct.unpack("16sH22x", fcntl.ioctl(handle, 0x8913, request))[1]
        fcntl.ioctl(handle, 0x8914, struct.pack("16sH22x", b"lo", flags | 1))
except OSError as error:
    sys.exit(f"network isolation is unavailable: {error}")
if len(sys.argv) > 1:
    try:
        os.execvp(sys.argv[1], sys.argv[1:])
    except OSError as error:
        sys.exit(f"cannot run {sys.argv[1]}: {error}")
"""


def isolated(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Return `argv` started in a new network namespace with loopback only."""
    return (sys.executable, "-I", "-c", STUB, *argv)


@cache
def unavailable() -> str | None:
    """Return why this machine cannot isolate a suite, or None when it can."""
    try:
        completed = run(Command(isolated(()), cwd=Path("/"), timeout=PROBE_TIMEOUT))
    except ProcessError as error:
        return str(error)
    if completed.returncode == 0:
        return None
    lines = completed.stderr_text().strip().splitlines()
    return lines[-1] if lines else f"the probe exited with status {completed.returncode}"


def contained(argv: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
    """Return `argv` isolated when this machine allows it, else unchanged with the reason."""
    reason = unavailable()
    return (argv, reason) if reason else (isolated(argv), None)
