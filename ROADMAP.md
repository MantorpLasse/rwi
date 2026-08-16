# RWI Roadmap

RWI is an evidence-led, single-user research system for understanding EMAS
installations, incidents, projects, and market-relevant change. The goal is
steady discovery and trustworthy public presentation—not a heavyweight
intelligence workflow.

## Direction

```text
Foundation (current)
  -> Physical identity & reconciliation
  -> Lifecycle / replacement
  -> Automated research & discovery
  -> Lightweight research review
  -> Public site, bilingual and map improvements
  -> Newsletter / alerts
  -> Advanced analysis and querying
```

## CURRENT

### Evidence and physical installation identity

- `SourceAssertion` preserves source claims and provenance before inference.
- `PhysicalInstallationIdentity` represents reviewed canonical physical systems.
- Reconciliation is human-reviewed, auditable, and reversible.
- The CGF control pilot established distinct identities for ends `06` and `24`.
- Legacy `Installation` remains useful but separate and non-canonical.
- Public export excludes internal evidence and reconciliation metadata.

### Core product

- SQLite, SQLAlchemy, Python, and static export remain appropriate for the
  current single-user research scale.
- Existing discovery/import work covers FAA/NASR, FAA grants and construction
  material, USAspending, and curated research sources.
- The public site is primarily static; the development server is not the public
  product contract.

## NEXT

### Physical identity, difficult cases, and lifecycle

- Stress-test reconciliation at ambiguous airports such as MDW.
- Add evidence-backed lifecycle and replacement relationships without treating
  repair, funding, or maintenance as a replacement by assumption.
- Gradually reconcile useful legacy material when direct evidence supports it;
  do not block progress on cleaning every legacy row.
- Preserve multiple systems at an airport, runway, or runway end; never use
  airport/type/year as a physical identity key.

### Discovery engine

- Move from periodic manual searching toward broad, repeatable discovery—not
  merely monitoring known airports.
- Search authoritative and useful public sources: FAA/NASR, grants, master
  plans, CIP/ALP material, construction reports, procurement/bids, airport and
  government sites, incident reports, news, and partner/manufacturer sources.
- Detect new installations, projects, replacements, repairs, starts,
  completions, incidents, runway redesignations, funding, and new airports.
- Preserve original artifacts and source-language evidence before creating
  candidate assertions.

### Native-language research

- Use country and local-language terms where they improve international
  discovery.
- Keep original-language evidence intact; generate English and Swedish
  summaries separately when useful.
- Treat AI translation/search expansion as discovery assistance, never proof.

## LATER

### Review by exception

Human review is required **by exception, not by default**. Automation may
discover sources, preserve evidence, extract candidates, classify information,
and propose matches with confidence. Explicit deterministic rules may safely
process strong cases over time. Ambiguous physical identity, lifecycle, and
replacement decisions must surface for review.

A lightweight queue should make this practical: for example, “7 findings; 5
safely processed; 2 need review,” with evidence, reasoning, confidence, and
accept/reject/unresolved actions. RWI must not require the owner to approve
every incoming item.

### Public website, bilingual delivery, and map

- Improve airport pages, evidence presentation, timelines, signals/projects,
  and multi-installation representation.
- Clarify category versus status: a “New installation” category with a
  “Completed” status may be technically correct but visually confusing.
- Add English and Swedish through shared presentation mappings, not duplicated
  sites. Do not implement localization prematurely.
- Evolve the map from airport markers toward reviewed physical installations,
  while distinguishing projects, replacements, completed work, incidents, and
  uncertain candidates without false precision.

### Newsletter and alerts

- Offer subscriptions for important updates, all published updates, digests,
  and watched airports.
- Publish only reviewed/public information, triggered by meaningful published
  change—not arbitrary database writes.
- Never expose private Signal notes, estimates, unreviewed AI candidates, or
  internal evidence/reconciliation data.

### Automation, resilience, and optional capabilities

- n8n or agents may orchestrate scheduled searches, monitoring, retrieval,
  extraction, language-aware discovery, change detection, and review-queue
  creation. They are optional orchestration, not RWI's domain authority.
- Add automated backups, retention, documented restore, and optional
  Git-diffable JSON/CSV history snapshots. Keep databases outside the public
  web root.
- Optional later work: natural-language querying, richer watchlists/alerts,
  analytics/trends, international datasets, and source-health monitoring.

## Guardrails

- Evidence before inference; never fabricate facts or URLs.
- AI discovery is not automatically accepted truth.
- Private assessment never enters public export.
- Reconciliation must be auditable and reversible.
- Keep the architecture suitable for a single-user research system.
- Avoid restoring Observation -> Verification -> Fact -> Intelligence.
- Keep static publishing where practical and add infrastructure only for a
  concrete need.

## Planning documents to revisit later

`PLAN_FORENKLING.md` was valuable for the simplified-Claude direction, but
several implementation prescriptions are superseded: it describes `Installation`
as the sole current-installation identity, rejects migrations, and frames
human review more broadly than the current evidence/identity contract. Retain
it as historical context, then archive or clearly mark it superseded later.

`docs/domain/reconciliation-physical-installation-design.md` is an earlier
design iteration. Slice 6B/6C/6D refined its direct-to-legacy-Installation
proposal into the safer `PhysicalInstallationIdentity` layer. Retain it for
decision history; update/archive it only in a dedicated documentation pass.

`DESIGN_BRIEF.md` remains a visual direction, not an architectural roadmap.
Use it for later public-site work; it does not conflict with the evidence
boundaries above.
