from __future__ import annotations

"""Fleet Health Check — FHC4 presentation/static-export cross-check.

Implements the reviewed design's remaining two rules
(docs/architecture/fleet-health-check-design-and-real-db-reconnaissance.md
§6's 32-row catalogue), FH-H1 and FH-H2 — the only two rules whose evidence
requires comparing persisted state against GENERATED presentation output
(`app.static_export.build_site()`), not persisted state alone. This is why
FHC4 is a materially different architectural boundary from FHC1-FHC3: those
three inspect only database state; this one also reads a static-export
output directory.

FH-H2 (DETERMINISTIC_ERROR): "Published Signal count vs. rendered signal
detail-page count mismatch" — implemented below, exactly as reviewed. A
direct, mechanical comparison of `COUNT(*) WHERE published=1` against the
count of rendered `signals/{id}.html` files. Nothing more: the reviewed
design's own evidence column is exactly these two counts, not per-Signal
reference/link correctness, not page content correctness, not staleness. A
richer per-Signal presentation-consistency rule was NOT invented here (the
mission's own Phase 1 instruction: "Do not invent additional presentation
rules merely because they sound useful") — see the FHC4 report's own
"deferred work" section for what a future, separately-reviewed rule could
cover.

FH-H1 (NOT_CURRENTLY_DETECTABLE): the reviewed design classifies this rule
itself as NOT_CURRENTLY_DETECTABLE, with the design's own reasoning stated
verbatim: "The export itself is the only accurate oracle for 'would this
crash a real build'; a static SQL check would either under- or
over-approximate its exact null-handling branches... this is inherently a
'run it and see' check, folded into Phase 8/§10 practice, not a row-level
rule." Matching that classification exactly, and matching the precedent
already established for every other NOT_CURRENTLY_DETECTABLE rule (H1
itself, I1, I2 in FHC3's own doc-only treatment): THERE IS NO
`evaluate_fh_h1()` FUNCTION HERE, and no `HealthFinding` this module can
ever emit claims to detect H1's own predicate. H1's own "run it and see"
check is satisfied structurally by the FHC2-extension adapter's fail-loud
call to the real `build_site()` exporter (see
`app.services.fleet_health_check.build_fleet_presentation_snapshot()`): if
any Signal's null/malformed state would crash template rendering,
`build_site()` raises and the adapter propagates that exception unmodified
— exactly the "run it and see" check the design describes, performed by
the adapter's own necessary export call, not fabricated as a second,
row-level detector here.

DATA_ANOMALY vs. PRESENTATION_ANOMALY boundary (critical, see the FHC4
report's own dedicated section): FH-H2 can only ever detect a
PRESENTATION_ANOMALY — a case where the *generated output* disagrees with
*persisted, already-governed* state (the `published` flag). It structurally
cannot, and must never be extended to, flag a case where persisted data is
itself questionable but faithfully rendered (e.g. a bad Airport.name
appearing verbatim on a page) — that remains a DATA_ANOMALY, already fully
owned by FHC3's FH-A4 (deliberately not automated) and is out of scope for
this module entirely. `PublishedSignalFact`/`RenderedSignalPageFact` below
carry only a bare `signal_id` each — structurally incapable of comparing
anything about a page's *content* (no title, no airport name, no rendered
text of any kind), which is what makes this boundary a type-level
guarantee, not merely a documented intention.

PURITY (same discipline as fleet_health_rules.py/fleet_health_review_rules.py):
no SQLAlchemy, no ORM, no Session, no filesystem access, no network, no
clock, no random/UUID identity, no provider-specific logic, no
scoring/ranking. `HealthClassification`/`HealthFinding` are reused,
unmodified, from `app.services.fleet_health_rules`.
"""

from dataclasses import dataclass

from app.services.fleet_health_rules import HealthClassification, HealthFinding

__all__ = [
    "PublishedSignalFact",
    "RenderedSignalPageFact",
    "FleetPresentationSnapshot",
    "PRESENTATION_RULE_IDS",
    "evaluate_fh_h2",
    "evaluate_presentation_findings",
]


@dataclass(frozen=True)
class PublishedSignalFact:
    """FH-H2 input: one Signal currently eligible for public export
    (`Signal.published == True`). Deliberately just an id - no title, no
    airport, no category - this rule can only ever compare identity/count,
    never content."""

    signal_id: int


@dataclass(frozen=True)
class RenderedSignalPageFact:
    """FH-H2 input: one rendered `signals/{id}.html` file, identified
    purely by its filename - never by parsing the file's HTML content
    (FH-H2's own reviewed definition needs nothing more than the count of
    these files and their ids)."""

    signal_id: int


@dataclass(frozen=True)
class FleetPresentationSnapshot:
    published_signals: tuple[PublishedSignalFact, ...] = ()
    rendered_signal_pages: tuple[RenderedSignalPageFact, ...] = ()


def evaluate_fh_h2(snapshot: FleetPresentationSnapshot) -> tuple[HealthFinding, ...]:
    """Published Signal count vs. rendered signal detail-page count
    mismatch - the exact reviewed FH-H2 predicate, nothing more.

    Deduplicates each side by signal_id first (defensive against a
    duplicate input row, matching the discipline established at the FHC1
    review checkpoint) before comparing sets, so a duplicated fact can
    never itself manufacture a false mismatch. Emits at most one finding:
    zero when the two id sets are identical, one DETERMINISTIC_ERROR
    finding otherwise, with the specific missing/extra signal ids as
    structured evidence for *why* the counts differ (not a new rule - just
    evidence supporting the one, single count-mismatch trigger).
    """
    published_ids = {fact.signal_id for fact in snapshot.published_signals}
    rendered_ids = {fact.signal_id for fact in snapshot.rendered_signal_pages}
    if published_ids == rendered_ids:
        return ()

    missing = tuple(sorted(published_ids - rendered_ids))  # published but not rendered
    extra = tuple(sorted(rendered_ids - published_ids))  # rendered but not published
    affected_ids = tuple(sorted(published_ids ^ rendered_ids))

    return (
        HealthFinding(
            rule_id="FH-H2",
            classification=HealthClassification.DETERMINISTIC_ERROR,
            entity_type="Signal",
            entity_ids=affected_ids,
            airport_id=None,
            summary=(
                f"Published Signal count ({len(published_ids)}) does not match "
                f"rendered signal detail-page count ({len(rendered_ids)}): "
                f"{len(missing)} published Signal(s) have no rendered page, "
                f"{len(extra)} rendered page(s) do not correspond to a "
                "published Signal"
            ),
            structured_evidence={
                "published_count": len(published_ids),
                "rendered_count": len(rendered_ids),
                "missing_signal_ids": missing,
                "extra_signal_ids": extra,
            },
        ),
    )


PRESENTATION_RULE_IDS: tuple[str, ...] = ("FH-H2",)


def evaluate_presentation_findings(
    snapshot: FleetPresentationSnapshot,
) -> tuple[HealthFinding, ...]:
    """Runs the one implemented FHC4 rule against a snapshot. Never calls,
    and is never called by, evaluate_hard_invariants() or
    evaluate_review_findings() - all three rule tiers remain independently
    evaluable and independently testable."""
    return evaluate_fh_h2(snapshot)
