"""Shared fixture access for the consumer tests.

Building fourteen swept solids is not free, so the realised model is built once
per process and shared. Every assertion is still made against that real
geometry — nothing here substitutes a recorded value for a measurement.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = FIXTURE_DIR / "rc-footing-cad-poc.handoff.json"

#: The document the tests are written against, pinned so a silently swapped
#: fixture fails loudly instead of quietly changing what is being verified.
MANIFEST_SHA256 = "795e9de26f2eb8ce8d51f2ac7130336702fc534588f390071e3bd40bc03aa0e7"
MANIFEST_SIZE_BYTES = 88101

# What the producer declares this document contains. Asserted rather than
# assumed, so a fixture change cannot quietly weaken the suite.
EXPECTED_BAR_COUNT = 14
EXPECTED_DOWEL_COUNT = 8
EXPECTED_TIE_COUNT = 6
EXPECTED_ARC_COUNT = 38
EXPECTED_APPROXIMATED_ARCS = 0
EXPECTED_MARK_COUNT = 2
EXPECTED_BODY_COUNT = 2
EXPECTED_INTERFACE_COUNT = 1
EXPECTED_PROHIBITED_OVERLAPS = 12

FOOTING_BODY_ID = "body:footing:1"
COLUMN_BODY_ID = "body:column:1"
INTERFACE_ID = "iface:footing:1:column:1"
DOWEL_FAMILY_ID = "family:columnDowel:footing:1:column:1"
TIE_FAMILY_ID = "family:starterTie:footing:1:column:1"


def raw_manifest() -> dict[str, Any]:
    """A fresh mutable copy of the manifest, for mutation tests."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def mutated(mutate) -> dict[str, Any]:
    document = raw_manifest()
    mutate(document)
    return document


def parse_object(document: dict[str, Any]):
    from rc_cad_handoff.manifest import parse_handoff_object

    payload = json.dumps(document).encode("utf-8")
    return parse_handoff_object(
        copy.deepcopy(document),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


@functools.lru_cache(maxsize=1)
def handoff():
    from rc_cad_handoff.manifest import load_handoff

    return load_handoff(MANIFEST_PATH)


@functools.lru_cache(maxsize=1)
def model():
    from rc_cad_handoff.geometry import realise

    return realise(handoff())


@functools.lru_cache(maxsize=1)
def cross_check_result():
    from rc_cad_handoff.crosscheck import cross_check

    return cross_check(handoff(), model())


def collision_check():
    check = handoff().check_of_kind("barCollision")
    assert check is not None
    return check


def footing_extent() -> tuple[float, float]:
    """(z_min, z_max) of the footing pad, read from the manifest."""
    body = handoff().body(FOOTING_BODY_ID)
    half = body.shape.height / 2.0
    return (body.shape.centre.z - half, body.shape.centre.z + half)
