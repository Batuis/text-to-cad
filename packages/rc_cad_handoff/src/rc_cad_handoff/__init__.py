"""An independent consumer for ``RcCadHandoffV1`` reinforced-concrete handoffs.

The producing application owns every engineering meaning in the document —
reinforcement roles, required cover and spacing, regulatory classification, and
every verdict. This package realises the geometry that document describes,
exports it, and measures it independently so the two implementations can be
compared. It derives no rule and overrides no verdict.
"""

from __future__ import annotations

from .errors import ContractError, GeometryError, HandoffError, PolicyError, StructureError
from .manifest import CONTRACT, SUPPORTED_SCHEMA_VERSIONS, Handoff, load_handoff
from .pipeline import ConsumeResult, consume
from .status import Collision, Comparison, IssueKind, Provenance, Realisation

__all__ = [
    "CONTRACT",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Collision",
    "Comparison",
    "ConsumeResult",
    "ContractError",
    "GeometryError",
    "Handoff",
    "HandoffError",
    "IssueKind",
    "PolicyError",
    "Provenance",
    "Realisation",
    "StructureError",
    "consume",
    "load_handoff",
]

__version__ = "0.1.0"
