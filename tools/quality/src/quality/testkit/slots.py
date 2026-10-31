"""A limit on the tests that run at once across pytest-xdist workers.

A test that runs whole gates of a project runs nested suites on every CPU, so
several at once can each outlast a scratch command's timeout. Each holder
locks one of a fixed number of files in a directory that the workers share;
the kernel releases the lock when the holder closes it or exits.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

POLL = 1.0


def _free(directory: Path, count: int) -> tuple[int, int] | None:
    """Return a free slot and its locked descriptor, or None while every slot is held."""
    for index in range(count):
        descriptor = os.open(directory / f"{index}.lock", os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            continue
        return index, descriptor
    return None


@contextmanager
def held(directory: Path, count: int, poll: float = POLL) -> Generator[int]:
    """Hold one of `count` slots in `directory` for the block, waiting until one is free."""
    directory.mkdir(parents=True, exist_ok=True)
    while (free := _free(directory, count)) is None:
        time.sleep(poll)
    index, descriptor = free
    try:
        yield index
    finally:
        os.close(descriptor)
