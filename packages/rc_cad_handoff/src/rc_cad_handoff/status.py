"""The review vocabulary.

These strings are the whole point of the review artifact: they keep an
authoritative producer verdict, an independent CAD measurement, and the
relationship between the two in three separate places. Collapsing any two of
them would let an unevaluated property be read as passing.
"""

from __future__ import annotations

from enum import StrEnum


class Provenance(StrEnum):
    """Who produced a number."""

    #: The producer's own verdict. Never recomputed, never overwritten.
    AUTHORITATIVE_STABILEO = "AUTHORITATIVE_STABILEO"
    #: This consumer's independent geometric measurement.
    CAD_OBSERVATION = "CAD_OBSERVATION"


class Comparison(StrEnum):
    """The relationship between an authoritative verdict and a CAD observation."""

    #: Both exist and fall inside the agreement band.
    AGREEMENT = "AGREEMENT"
    #: Both exist and fall outside the agreement band. A bug in one of the two.
    DISAGREEMENT = "DISAGREEMENT"
    #: A CAD observation exists but there is no verdict to compare it against.
    NOT_COMPARABLE = "NOT_COMPARABLE"
    #: The producer did not evaluate the property. Never a pass.
    NOT_EVALUATED = "NOT_EVALUATED"
    #: The producer forbids measuring this in this release.
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Realisation(StrEnum):
    """How faithfully a geometric datum was realised."""

    #: Built from the producer's exact data with no substitution.
    EXACT = "EXACT"
    #: The producer declared an approximation and this consumer preserved it.
    APPROXIMATED = "APPROXIMATED"
    #: The consumer cannot represent this datum at all.
    UNSUPPORTED = "UNSUPPORTED"


class IssueKind(StrEnum):
    """Issue kinds this consumer may raise.

    ``CROSS_CHECK_DISAGREEMENT`` is deliberately its own kind rather than a
    variant of a collision finding: it is a statement about the two
    implementations, not about the cage.
    """

    CROSS_CHECK_DISAGREEMENT = "crossCheckDisagreement"
    #: A declared blocker in the manifest. Never reported as a clean pass.
    UNSUPPORTED_CONDITION = "unsupportedCondition"
    #: Nominal bend parameters that do not equal the realised arc geometry.
    NOMINAL_BEND_DEVIATION = "nominalBendParameterDeviation"
    #: A measurement the producer's declared scope does not authorise.
    OBSERVATION_SCOPE_LIMITATION = "observationScopeLimitation"
    #: Achieved geometry measures below a producer requirement. An observation
    #: about geometry, never a breach verdict: the producer does not evaluate
    #: containment, so there is no verdict this could contradict.
    COVER_OBSERVATION_BELOW_REQUIREMENT = "coverObservationBelowRequirement"
    #: An OCCT operation whose result is not numerically trustworthy here.
    NUMERICAL_LIMITATION = "numericalLimitation"


class Collision(StrEnum):
    """Independent classification of a bar pair from CAD solids."""

    INTERSECTING = "INTERSECTING"
    CONTACT = "CONTACT"
    SEPARATED = "SEPARATED"


#: Consumer observation policies the producer may declare.
POLICY_MAY_CROSS_CHECK = "MAY_CROSS_CHECK"
POLICY_MAY_OBSERVE_NOT_COMPARABLE = "MAY_OBSERVE_NOT_COMPARABLE"
POLICY_OUT_OF_SCOPE = "OUT_OF_SCOPE"

KNOWN_POLICIES = frozenset(
    {
        POLICY_MAY_CROSS_CHECK,
        POLICY_MAY_OBSERVE_NOT_COMPARABLE,
        POLICY_OUT_OF_SCOPE,
    }
)
