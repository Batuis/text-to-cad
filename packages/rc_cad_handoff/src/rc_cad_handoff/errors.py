"""Refusals.

A consumer that guesses is worse than one that stops. Every refusal carries a
stable ``code`` so a caller branches on an identifier rather than on prose, and
a ``field`` naming where in the manifest the problem is.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HandoffError(Exception):
    """Base refusal. Never raised directly."""

    code: str
    message: str
    field: str | None = None

    def __str__(self) -> str:
        where = f" at {self.field}" if self.field else ""
        return f"[{self.code}]{where}: {self.message}"


class ContractError(HandoffError):
    """The document is not a contract this consumer accepts."""


class StructureError(HandoffError):
    """The document is the right contract but structurally invalid."""


class GeometryError(HandoffError):
    """The geometry cannot be realised exactly, and will not be approximated silently."""


class PolicyError(HandoffError):
    """A caller asked for something the manifest's observation policy forbids."""
