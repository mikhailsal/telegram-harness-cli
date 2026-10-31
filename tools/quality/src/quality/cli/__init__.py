"""Command-line interface of the quality gate."""

from __future__ import annotations

import argparse
from typing import Protocol


class Subcommands(Protocol):
    """The part of argparse's subparser action that commands use."""

    def add_parser(
        self,
        name: str,
        *,
        help: str,  # noqa: A002 - argparse keyword
        description: str,
    ) -> argparse.ArgumentParser:
        """Create a subcommand parser."""
        ...
