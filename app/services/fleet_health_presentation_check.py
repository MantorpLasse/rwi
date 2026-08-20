from __future__ import annotations

"""Fleet Health Check — FHC4 presentation/static-export cross-check adapter.

    Session (real DB or fixture) + output_dir (caller-owned, no default)
        -> build_fleet_presentation_snapshot()
        -> FleetPresentationSnapshot (from app.services.fleet_health_presentation_rules)
        -> evaluate_presentation_findings() (FHC4 rule core, unmodified)
        -> tuple[HealthFinding, ...]

    run_fleet_presentation_check(session, output_dir) does both steps in one call.

DELIBERATELY A SEPARATE MODULE FROM app.services.fleet_health_check (FHC2):
FHC2's own adapter is pure-DB-only by explicit, already-reviewed contract
(its own AST purity test forbids `pathlib`/filesystem imports entirely,
since every FHC1/FHC3 rule needs nothing but persisted state). FHC4 is
genuinely different - the mission's own framing states this explicitly
("This is intentionally a different boundary from FHC1-FHC3... FHC4 must
compare persisted/publication state with generated presentation output") -
it needs a real output directory and calls the real static exporter
(`app.static_export.build_site()`), which FHC2's own reviewed purity
guarantee was never meant to cover. Mixing the two into one file was tried
during this task's own implementation and reverted after it broke FHC2's
own already-committed `test_no_forbidden_imports_via_ast` test (which
correctly forbids `pathlib` for FHC2's pure-DB contract) - see the FHC4
report's own "defects/corrections" section. Keeping them separate preserves
FHC2's file, and its guarantee, completely byte-for-byte unchanged.

READ-ONLY WITH RESPECT TO THE DATABASE: every database call here is a
SELECT (`session.query()`); nothing is added, flushed, committed, or
deleted. `session.no_autoflush` covers this module's own read AND the
nested call into `build_site()` (which performs its own
`session.scalars(select(...))` reads) - the exact same review-checkpoint
safety boundary FHC2/FHC3 already established, extended here to cover the
one new read path this module adds.

THE ONLY FILESYSTEM WRITE ANYWHERE IN THIS MODULE is the caller-directed
static export itself, into whatever directory the caller explicitly names
via the required `output_dir` parameter (no default, on either public
function) - never a hardcoded or default path, and never the repository's
own canonical `site/` output. `build_site()` deletes and recreates
`output_dir` on every call (see `app.static_export.build.py`'s own
`_build()`); passing the wrong path would be destructive, which is exactly
why this parameter has no default and every caller must supply their own,
explicit, disposable directory.

BUILD_SITE IS ALWAYS CALLED WITH `session=session` EXPLICITLY - never
omitted. `app.static_export.build.build_site()`'s own default (`session=None`)
falls back to `app.database.SessionLocal()`, which is bound to this
project's own default `database_url` - the REAL production database
(`sqlite:///./data/runway_safe.db`, see `app/config.py`). Omitting the
explicit session here would silently make a "synthetic" test read real
production data instead of its own isolated fixture. Verified directly by
test (`test_build_site_always_called_with_explicit_session_not_default`).

FH-H1 ("run it and see"): any exception `build_site()` raises (e.g. a
null-required field a template cannot render) propagates unmodified from
this module - this IS FH-H1's own reviewed "run it and see" check
(NOT_CURRENTLY_DETECTABLE as a row-level rule per the design's own
classification), satisfied here as a necessary consequence of calling the
real exporter, not fabricated as a second detector. See
`app.services.fleet_health_presentation_rules`'s own module docstring for
the full reasoning.
"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Signal
from app.services.fleet_health_rules import HealthFinding
from app.services.fleet_health_presentation_rules import (
    FleetPresentationSnapshot,
    PublishedSignalFact,
    RenderedSignalPageFact,
    evaluate_presentation_findings,
)
from app.static_export import build_site

__all__ = [
    "build_fleet_presentation_snapshot",
    "run_fleet_presentation_check",
]


def _build_published_signal_facts(session: Session) -> "tuple[PublishedSignalFact, ...]":
    """One row per Signal with published == True, no joins. FH-H2 input."""
    rows = (
        session.query(Signal.id)
        .filter(Signal.published.is_(True))
        .order_by(Signal.id.asc())
        .all()
    )
    return tuple(PublishedSignalFact(signal_id=row[0]) for row in rows)


def _read_rendered_signal_page_facts(
    output_dir: Path,
) -> "tuple[RenderedSignalPageFact, ...]":
    """Reads the exact set of rendered `signals/{id}.html` files from a
    static-export output directory - filename-based identity only, no HTML
    content is ever parsed (FH-H2 needs nothing more). Fails loud, never
    silently: a missing `signals/` directory raises `FileNotFoundError`; any
    file in it that isn't `index.html` and isn't a bare-integer-named
    `.html` file raises `ValueError` - an unexpected artifact shape must
    never be silently dropped (which would understate the rendered count
    and could mask or fabricate an FH-H2 finding), and must never be
    silently treated as healthy.

    The filename stem must be the EXACT canonical decimal representation of
    its own integer value (`str(int(stem)) == stem`) - not merely
    `str.isdigit()`. `app.static_export.build.py`'s own `render()` calls
    always write `f"{signal.id}.html"` for a real Python int, which can
    never legitimately produce a zero-padded or otherwise non-canonical
    form (e.g. "01.html"); rejecting that shape outright (rather than
    accepting it as an alias for signal_id 1) closes a real found-and-fixed
    gap where two distinct filenames aliasing to the same integer id would
    otherwise silently collapse into one fact via set deduplication
    downstream in FH-H2, hiding a genuine duplicate/unexpected artifact
    (review-checkpoint fix - see the FHC4 report's own corrections
    section).
    """
    signals_dir = Path(output_dir) / "signals"
    if not signals_dir.is_dir():
        raise FileNotFoundError(
            f"expected static-export signals directory not found: {signals_dir}"
        )
    ids: "list[int]" = []
    for entry in sorted(signals_dir.iterdir(), key=lambda p: p.name):
        if entry.name == "index.html":
            continue
        if not entry.is_file() or entry.suffix != ".html":
            raise ValueError(
                f"unexpected non-page artifact in signals output directory: {entry.name}"
            )
        stem = entry.stem
        if not stem.isdigit() or str(int(stem)) != stem:
            raise ValueError(
                f"unexpected signal page filename (not a canonical numeric id): {entry.name}"
            )
        ids.append(int(stem))
    return tuple(RenderedSignalPageFact(signal_id=i) for i in sorted(ids))


def build_fleet_presentation_snapshot(
    session: Session, output_dir: Path
) -> FleetPresentationSnapshot:
    """Builds one FleetPresentationSnapshot by (1) reading current published-
    Signal state from the database and (2) running the REAL static exporter
    into `output_dir`, then reading back which signal detail pages it
    actually produced. See the module docstring for the full safety
    reasoning (no default output_dir, explicit session, no_autoflush scope,
    fail-loud behavior).
    """
    output_dir = Path(output_dir)
    with session.no_autoflush:
        published = _build_published_signal_facts(session)
        build_site(output_dir, session=session)
    rendered = _read_rendered_signal_page_facts(output_dir)
    return FleetPresentationSnapshot(
        published_signals=published,
        rendered_signal_pages=rendered,
    )


def run_fleet_presentation_check(
    session: Session, output_dir: Path
) -> "tuple[HealthFinding, ...]":
    """Builds a presentation snapshot (running the real static exporter into
    `output_dir`) and evaluates the one implemented FHC4 rule (FH-H2)
    against it, unmodified. `output_dir` has no default - see
    build_fleet_presentation_snapshot()'s own docstring for why."""
    snapshot = build_fleet_presentation_snapshot(session, output_dir)
    return evaluate_presentation_findings(snapshot)
