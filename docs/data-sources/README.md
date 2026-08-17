# RWI Data Sources Handbook

Permanent reference for where RWI's data comes from, how it is acquired,
and how it moves from raw external evidence to canonical, publishable
facts. This handbook documents *sources*, not individual acquisition
tasks — detailed investigation/implementation reports live under
`docs/domain/` and are linked from each source document rather than
duplicated here.

## Lifecycle stages

Every external data source RWI uses passes through some or all of these
stages, in this order. Keeping the stages named and separated is the
handbook's main organizing idea — a source document should say, for each
stage, what happens and where the code/evidence for it lives.

1. **Discovery** — finding the current official location of a dataset
   (an index page, a cycle/version listing, a specific download link).
   Read-only; produces URLs, not files.
2. **Acquisition** — retrieving the raw artifact (a file, a page, an API
   response) from its official location and validating that what arrived
   is structurally sound (right format, expected members/fields present,
   not truncated).
3. **Preservation** — writing the raw artifact to immutable local storage
   exactly as retrieved, alongside a provenance sidecar recording where it
   came from, when, and its hash/size. Nothing is transformed at this
   stage.
4. **Ingestion** — parsing the preserved raw artifact into planned
   domain-model changes (e.g. "these runway rows would create/enrich these
   `Runway`/`RunwayEnd` records"). Read-only planning; proposes, does not
   write.
5. **Reconciliation** — deciding how a proposed change relates to existing
   RWI data (new vs. duplicate vs. conflicting vs. requires human
   judgment), and, once approved, applying it.
6. **Public presentation** — deciding what, if anything, derived from a
   source becomes visible in the public static export. Separate from and
   downstream of every stage above.

## Core principle: preserve before transforming

Raw source artifacts are always preserved, byte-for-byte, before any
parsing or transformation happens. A future re-parse, a bug fix in a
parser, or an audit should never need to re-fetch the original source —
the preserved artifact is the evidence of record. This is why
preservation is its own stage, separate from ingestion, even though for a
small source it might be tempting to fetch-and-parse in one step.

## Provenance requirements

Every preserved raw artifact should have a sidecar recording, at minimum:

- what publisher/dataset it is
- exactly where it was retrieved from (index URL, and the specific final
  URL that was actually fetched)
- when it was retrieved (UTC)
- its byte size and a content hash (SHA-256)
- the local filename/path it was preserved under
- what acquisition mechanism produced it (module/function, so a future
  reader knows what code to trust and what to re-run)

Acquisition-actor identity (human, a specific tool, CI, a future
scheduled job) is **not** a required provenance field. Trustworthy
provenance rests on verifiable facts about the artifact itself — its
source, timing, and hash — not on who happened to invoke the acquisition.
An actor field may be added optionally by a caller; it is never load-
bearing for trust.

## Evidence immutability / fail-closed behavior

Once preserved, a raw artifact is never overwritten. Re-running
acquisition against an already-preserved target must behave as follows:

- **Same hash** → idempotent no-op; nothing is rewritten.
- **Different hash for the same target** → stop, report a provenance
  collision, touch neither the old nor the new file.
- **Sidecar and archive disagree with each other** → stop, report an
  integrity error; this is checked independent of any new acquisition
  attempt, so it can also be used as a standalone health check.

Silent overwrite of raw evidence is never acceptable at any stage.

## Human-review boundaries

Acquisition and preservation are mechanical and safe to automate: they
never touch the database and never change what's publicly visible.
Ingestion may propose changes deterministically, but turning a proposal
into an actual database write is a separate, explicitly approved step —
never automatic, even when the proposal itself is fully deterministic.
Each source document should state plainly where its own automation
currently stops and a human decision is currently required.

## Source document template

Each file under `docs/data-sources/` documenting one source should use
these sections, skipping any that don't apply rather than padding them:

1. Source overview
2. Publisher
3. Dataset/product
4. Why RWI uses it
5. Discovery endpoint
6. Acquisition method
7. Raw storage location
8. Provenance metadata
9. Validation rules
10. Parsing/ingestion use
11. Refresh/update behavior
12. Human-review boundary
13. Known limitations
14. Relevant code
15. Relevant reports/history

## Documented sources

- [`faa-nasr.md`](faa-nasr.md) — FAA NASR 28-day subscription (airport/runway inventory, EMAS presence evidence). First fully documented source; template for the rest.

## Future sources (not yet documented)

Listed for awareness only — no source document exists yet for any of
these:

- FAA EMAS / arresting-system material (beyond what NASR's `APT_ARS.csv` already covers)
- FAA construction/project reports
- USAspending.gov grant data
- Airport authority procurement/project pages
- International civil aviation / airport authority sources (for non-U.S. airports, which FAA NASR does not cover)
- Manufacturer/project announcements
