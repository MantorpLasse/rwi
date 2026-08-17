# Canonical Runway / Runway-End Foundation Design

**Status:** Design-only. No model, migration, script, template, or database
row was changed to produce this document. All factual claims below were
verified by reading current repository code, git history, and the already
preserved, hash-verified local NASR artifact — no network request was made.

## 1. Executive conclusion

RWI needs one small, generic addition: a canonical `RunwayEnd` entity
(Option A/C below — they converge), plus one new nullable foreign key on
`PhysicalInstallationIdentity`. Nothing else needs to change.

The concept already existed once — a `RunwayEnd` model was added on
2026-07-17 and removed five days later, not because the idea was wrong but
because the whole system was mid-simplification and the table held no real
data yet (see §2 History). Today the situation is the opposite: MDW and CGF
already have six reviewed `PhysicalInstallationIdentity` rows sitting on
`runway_id = NULL` specifically because there is no trustworthy canonical
runway row to point at, and the exact authoritative data needed to build
one — FAA NASR's `APT_RWY.csv` and `APT_RWY_END.csv` — is already sitting
on disk, already downloaded, already SHA-256-verified, and currently
**100% unused**. Restoring a narrow version of the old concept, fed from
data that already exists, is not restoring the old architecture — it is
finishing a step that was deliberately deferred (`docs/domain/reconciliation-physical-installation-design.md`
§6 says outright: *"Do not redesign Runway."* — this document is that
redesign, now that there's a real reason to do it).

The smallest correct model is: `Runway` unchanged, one new `RunwayEnd` table
(`runway_id` FK + normalized designation, nothing else mandatory), and one
new nullable `PhysicalInstallationIdentity.runway_end_id` FK. Everything
else — designation history, international importers, UI, Research Engine
resolution — builds on this without requiring it to grow.

## 2. Current state and data gap

### 2.1 Models today

- `Airport` (`app/models/airport.py`): unchanged, not in scope.
- `Runway` (`app/models/airport.py`): `id, airport_id FK, designation
  (string, e.g. "13L/31R" — covers *both* ends in one field), length_m,
  width_m, surface, notes`. No end-level entity exists. No airport in the
  live database has more than one `Runway` row (confirmed by direct query:
  `MAX(count) = 1` across all 86 airports) — the table is a thin,
  non-exhaustive placeholder, not an inventory.
- `PhysicalInstallationIdentity` (`app/models/physical_installation_identity.py`):
  `id, airport_id FK, runway_id FK nullable, runway_end (string, nullable),
  created_at`. The reviewed, human-approved canonical identity for one
  physical EMAS system. All 6 existing rows (4 MDW, 2 CGF) have
  `runway_id = NULL` **by deliberate design**, per
  `docs/domain/evidence-installation-identity-slice6g-mdw-current-presence-report.md`:
  *"Runway IDs are deliberately null. Existing runway history makes a
  canonical FK more speculative than useful; the reviewed runway-end
  identity is safer."*
- `Installation` (legacy, `app/models/installation.py`): pre-reconciliation
  evidence rows, still feeding the public "Historiska installationsuppgifter"
  disclosure. Has `runway_id` (nullable FK) and `runway_end` (free string).
  Same shape as `PhysicalInstallationIdentity`, same lack of a canonical
  end to point at.
- `SourceAssertion` (`app/models/source_assertion.py`): preserves one
  upstream record's claim before reconciliation. Its `assertion_type`
  enum **already** includes `"runway"` and `"runway_end"` as valid values,
  alongside `raw_runway_value`/`raw_runway_end_value` fields — this schema
  was already future-proofed for exactly this work; nothing populates
  those two assertion types today.
- `InstallationAssertionLink`: append-only `SAME_PHYSICAL_INSTALLATION /
  DIFFERENT_PHYSICAL_INSTALLATION / UNRESOLVED` reconciliation decision,
  linking a `SourceAssertion` to a `PhysicalInstallationIdentity`. Not
  runway-specific; unaffected by this design.

### 2.2 History: the RunwayEnd model that briefly existed

Git history (`git log --all --diff-filter=A|D -- '*runway_end*'`) shows:

- **`98aa96b`** (2026-07-17) *"feat: add runway end and EMAS bed models"*
  added `app/models/runway_end.py`:
  ```python
  class RunwayEnd(Base):
      __tablename__ = "runway_ends"
      __table_args__ = (UniqueConstraint("runway_id", "designation"),)
      id: Mapped[int] = mapped_column(primary_key=True)
      runway_id: Mapped[int] = mapped_column(ForeignKey("runways.id"), index=True)
      designation: Mapped[str] = mapped_column(String(20))
      heading: Mapped[Optional[int]] = mapped_column(Integer)
      resa_length_m: Mapped[Optional[int]] = mapped_column(Integer)
      notes: Mapped[Optional[str]] = mapped_column(Text)
  ```
- **`823cbde`** (2026-07-22, 5 days later) *"Simplify data model per
  PLAN_FORENKLING.md steps 1-3"* deleted it, in the same sweep that
  collapsed the Project/Observation/Verification/Fact/Intelligence review
  pipeline into a single `Signal` table and merged `EmasInstallation` +
  `EmasBed` into `Installation`. The commit message's stated reason for the
  whole sweep: *"Drop the observation/verification/fact/intelligence/
  finding_type models... since none of those tables held real data."*
  `Installation.runway_end` (a flat string) is what replaced it.

**Conclusion:** the shape was reasonable — `runway_id` FK + unique
normalized designation is exactly what real-world runway ends need. It was
removed for lifecycle reasons (five days old, unpopulated, caught in a
broad simplification pass), not because the concept was architecturally
wrong. Two of its extra fields deserve scrutiny before being brought back,
not blind restoration: `heading` is cheap and NASR-available
(`TRUE_ALIGNMENT`) and is genuinely useful later for Research Engine
language resolution, but `resa_length_m` has no obvious source in the NASR
files actually inspected for this design (see §4) — it looks like it was
never populated from real data. Neither is needed for the smallest correct
model; both are optional, deferrable additions (§15).

### 2.3 The gap, precisely classified

**A. Missing data.** MDW's `runways` table has one row (`13L/31R`, id 12).
NASR's 2026-08-06 cycle shows MDW actually has **four** physical runways
(§8). The `Runway` table's *schema* can represent all four (it's just
`airport_id + designation + length + width`); the *rows* simply were never
created beyond an initial one-per-airport hand-seed. This part of the gap
is pure data population, not a capability problem.

**B. Missing schema capability.** There is no entity anywhere that
represents "one end of a runway" as a referenceable, constrained, canonical
thing. `Runway.designation` is one string spanning both ends together;
`runway_end` exists only as an unconstrained, un-normalized, FK-less free
string on three unrelated tables (`Installation`, `SourceAssertion`,
`PhysicalInstallationIdentity`). You cannot ask the database "what are the
valid ends of this runway" or "does a claimed end string actually match a
real one" — every consumer re-derives or guesses. This is a real,
structural gap, and it is what this design closes.

**C. Intentionally unresolved reconciliation.** The six existing
`PhysicalInstallationIdentity.runway_id = NULL` values are not a bug and
not (B) — the *column* already exists and is nullable by design. They are
a deliberate, evidence-driven refusal to link to a `Runway` row the
reviewer judged untrustworthy (the one legacy MDW row is itself mid-rename-
history; CGF's legacy row uses non-normalized `"6/24"` with no
length/width populated at all). This is correct, conservative behavior and
must not be "fixed" by force-linking. Once a trustworthy canonical
inventory exists, category (C) becomes *safely resolvable* for some rows —
but resolving it is a separate, later, human-gated step (§7), never an
automatic side effect of adding schema.

## 3. Real-world identity semantics

```
Airport (MDW)
├── Runway "04L/22R"  — one strip of pavement, two ends
│   ├── RunwayEnd "04L"
│   └── RunwayEnd "22R"
├── Runway "04R/22L"
│   ├── RunwayEnd "04R"  — has a PhysicalInstallationIdentity (EMAS)
│   └── RunwayEnd "22L"  — has a PhysicalInstallationIdentity (EMAS)
├── Runway "13L/31R"
│   ├── RunwayEnd "13L"  — has a PhysicalInstallationIdentity (EMAS)
│   └── RunwayEnd "31R"  — has a PhysicalInstallationIdentity (EMAS)
└── Runway "13R/31L"
    ├── RunwayEnd "13R"
    └── RunwayEnd "31L"
```

Explicit rules:

- **One airport has multiple runways.** MDW has 4 (confirmed via NASR,
  §8), not the 1 currently in the legacy table.
- **One runway has exactly two ends,** in the scope RWI operates in
  (public, fixed-wing, EMAS-relevant airports). A runway's identity is the
  pavement; each end is a distinct directional threshold with its own
  designation, geometry, and (independently) its own possible EMAS system.
- **Each runway end may or may not have EMAS.** MDW's `04L`/`22R` and
  `13R`/`31L` currently have no reviewed EMAS evidence at all — that's
  normal, not a gap. EMAS presence is a sparse overlay on top of the dense
  runway/end inventory, never the other way around.
- **One airport may have multiple physical EMAS installations.** MDW
  already has 4 reviewed ones (one per equipped end). The model must not
  assume "at most one EMAS per airport" or "at most one per runway."
- **A runway designation change must not silently imply physical
  continuity.** MDW's current `13L/31R` was `13C/31C` until a 2025-06-12
  rename (already documented as free text on the legacy row). The
  *current* canonical `Runway`/`RunwayEnd` rows describe the pavement
  *as currently named*; whether a historical EMAS/Installation record filed
  under the old name refers to the same physical system is a reconciliation
  question with its own evidence bar (§7), never inferred from the rename
  alone.

## 4. Preserved authoritative source capability

The full FAA NASR 2026-08-06 cycle package is already on disk and
integrity-checked: `data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`
(SHA-256 verified against `...zip.metadata.json` by
`app/evidence/nasr_apt_ars.py::rows()` every time it's read). It contains:

| File | Currently used by | Contains |
|---|---|---|
| `APT_BASE.csv` | not runway-related | 19,426 airport master rows; `ARPT_ID` is nationally unique across the whole file (checked: 0 collisions) |
| `APT_RWY.csv` | **nothing** | one row per **runway pair**: `ARPT_ID, RWY_ID, RWY_LEN, RWY_WIDTH, SURFACE_TYPE_CODE, COND, ...` |
| `APT_RWY_END.csv` | **nothing** | one row per **runway end**: `ARPT_ID, RWY_ID, RWY_END_ID, TRUE_ALIGNMENT, lat/long, elevation, lighting, ...` |
| `APT_ARS.csv` | `app/evidence/nasr_apt_ars.py`, `app/acquisition/faa_runway_ends.py` | arresting-system presence: `ARPT_ID, RWY_ID, RWY_END_ID, ARREST_DEVICE_CODE` |

**`APT_RWY.csv` and `APT_RWY_END.csv` are completely unused today.** Every
existing script (`scripts/import_faa_runway_ends.py`,
`app/evidence/nasr_apt_ars.py`) only ever reads `APT_ARS.csv` for EMAS
*presence* evidence — none of them establish a physical runway/end
inventory. That inventory is sitting in the same already-downloaded zip.

Verified against real rows for MDW and CGF (extracted read-only from the
preserved zip for this design, no network call):

```
MDW APT_RWY.csv:
  04L/22R  5507 ft x 150 ft  ASPH        GOOD
  04R/22L  6445 ft x 150 ft  ASPH-CONC   EXCELLENT
  13L/31R  6522 ft x 150 ft  ASPH-CONC   EXCELLENT
  13R/31L  3859 ft x  60 ft  ASPH        EXCELLENT

MDW APT_RWY_END.csv: 04L, 22R, 04R, 22L, 13L, 31R, 13R, 31L  (8 ends)

MDW APT_ARS.csv (EMAS): 04R, 22L, 13L, 31R  (4 of the 8 ends — exactly
  matching the 4 existing reviewed PhysicalInstallationIdentity rows)

CGF APT_RWY.csv:   06/24  5502 ft x 100 ft  ASPH
CGF APT_RWY_END.csv: 06, 24
CGF APT_ARS.csv (EMAS): 06, 24  (both ends)
```

(`6522 ft × 0.3048 = 1988.0 m`, `150 ft × 0.3048 ≈ 46 m` — matches the
existing legacy MDW row's `length_m=1988, width_m=46` exactly, confirming
the DB's existing unit convention is meters, converted from NASR feet.)

**Source separation, kept deliberate even though both come from the same
NASR cycle/zip:**

- **Canonical runway inventory** = `APT_RWY.csv` + `APT_RWY_END.csv`.
  Answers "what runways/ends physically exist and what are their
  dimensions." Governs `Runway`/`RunwayEnd` rows.
- **EMAS presence evidence** = `APT_ARS.csv`. Answers "what does the FAA
  currently report as equipped." Governs `SourceAssertion`s and, after
  review, `PhysicalInstallationIdentity` — never the runway/end rows
  themselves.

This mirrors `SourceAssertion.assertion_type`'s existing distinction
between `"runway"`/`"runway_end"` and other types — the schema already
expects these to be separate claims, just never fed.

## 5. International design

Nothing in the recommended model is FAA-specific:

- `Runway.designation` and the new `RunwayEnd.designation` are plain
  normalized strings — no `ARPT_ID`, `SITE_NO`, or any NASR field is a
  canonical key.
- Source-specific identifiers (`ARPT_ID`, a future Brazilian ANAC/DECEA
  designator, a New Zealand AIP designator, EASA/national-CAA codes,
  Japan's JCAB or Korea's MOLIT identifiers) belong on `SourceAssertion`'s
  existing `raw_runway_value`/`raw_runway_end_value`/`source_record_identifier`
  fields — never on the canonical rows. This is exactly the same pattern
  `Airport` already uses (`iata_code`/`icao_code`/`faa_code`, all optional,
  none mandatory, none canonical-identity-defining on their own).
- Country-specific *import* logic is where source-specificity lives, same
  as today: `app/evidence/nasr_apt_ars.py` is FAA-only; a future
  `app/evidence/brazil_decea_runways.py` or equivalent would feed the same
  generic `Runway`/`RunwayEnd` tables through the same
  `SourceAssertion(assertion_type='runway'|'runway_end')` preservation step.

No new work is needed now to "support" other countries — the constraint is
just: don't let any FAA-specific field leak onto the canonical tables.

## 6. Design options

**Option A — keep `Runway`, add canonical `RunwayEnd`.**
Correctness: high — matches NASR's own two-table shape, matches real-world
runway/end structure, matches `PhysicalInstallationIdentity`'s existing
`(runway_id, runway_end)` shape (makes the end referenceable instead of
free text). Complexity: one new table + one new nullable FK column.
Migration impact: additive only, zero risk to existing rows. Reconciliation
safety: doesn't touch `InstallationAssertionLink` or existing links at all;
only adds a new optional target. Compatibility with
`PhysicalInstallationIdentity`: excellent, its shape already anticipates
this. Import simplicity: 1:1 with `APT_RWY.csv`/`APT_RWY_END.csv`. Public
UI usefulness: directly enables the Runway → RunwayEnd → EMAS-ends render
requested in §13.

**Option B — extend `Runway` only, keep ends indirect** (e.g. two nullable
string columns on `Runway`, or continue with the bare free-text
`runway_end` string that already exists on three tables today).
Correctness: weak — can't validate a claimed end against real ones, can't
join "which ends have EMAS," an asymmetric two-columns-on-one-row shape is
awkward for anything end-specific later. Complexity: lower short-term, but
this is functionally what already exists (`runway_end` strings scattered
across `Installation`/`SourceAssertion`/`PhysicalInstallationIdentity`)
and it demonstrably isn't enough — it's the reason "Banor" is currently
suppressed. Import simplicity: `APT_RWY_END.csv`'s one-row-per-end shape
would need a lossy pivot into two-columns-per-runway with no natural key
for which end is "column A." **Rejected** — it relocates the existing
free-text problem rather than solving it, and fights the source data's own
shape.

**Option C — a different minimal design.** After inspecting the historical
model, the current `PhysicalInstallationIdentity` shape, and the NASR
file structure, no repository evidence supports a structurally different
third shape. Option C, as evaluated, converges on Option A scoped as small
as possible: no `heading`, no `resa_length_m`, no geometry/lighting fields
from the historical model or from NASR's much larger `APT_RWY_END.csv`
column set — just identity. See §15 for what's optional-later vs.
excluded-now.

**Recommendation: Option A, scoped per Option C's restraint.**

```
RunwayEnd (new table)
  id                PK
  runway_id         FK -> runways.id, indexed, NOT NULL
  designation       VARCHAR(10), normalized (e.g. "04R")
  UNIQUE(runway_id, designation)

PhysicalInstallationIdentity (existing table, additive change)
  + runway_end_id    FK -> runway_ends.id, nullable, indexed
    (runway_id and runway_end strings stay exactly as they are —
     nothing existing is removed or repointed automatically)
```

`Runway` itself needs no schema change — `id, airport_id, designation,
length_m, width_m, surface, notes` already fits `APT_RWY.csv` completely.

## 7. PhysicalInstallationIdentity linking rules

Future canonical inventory does **not** retroactively mutate the 6
existing reviewed identities. Linking is a separate, explicit,
human-gated step, following the exact same reviewed-decision discipline
`docs/domain/evidence-installation-identity-spec.md` §4 already
establishes for reconciliation generally.

**Safe to link automatically-proposed-but-human-approved:**
Exact airport match + the reviewed `runway_end` string matches, verbatim
after normalization, exactly one `RunwayEnd.designation` under exactly one
`Runway` belonging to that airport, sourced from the *current* authoritative
inventory (not a hand-seeded or ambiguous legacy row). Example: MDW
identity id 3 (`runway_end="04R"`) against a canonical `RunwayEnd("04R")`
under canonical `Runway("04R/22L")` at MDW — airport certain, designation
exact, no competing candidate.

**Unsafe — remains human-only, same as today:**
- Historical designation mapping (was this "04R" the same physical
  threshold before some historical rename affecting that airport?).
- Replacement/continuity claims (is the *current* EMAS at this end the
  same physical system, or a successor, or unrelated to whatever a legacy
  `Installation` row described?).
- A renamed runway where the reviewed identity's `runway_end` string was
  recorded against an old name.
- Ambiguous old source wording (a free-text `runway_end` that doesn't
  cleanly match a single normalized designation).
- Any airport whose canonical inventory itself is incomplete or
  unauthoritative (no source data, or only a hand-seeded legacy row).

The linking step is a proposal-then-approve dry run, identical in shape to
`scripts/apply_mdw_current_presence_pilot.py`'s existing `dry_run()`/`run(apply=...)`
split — never a bulk `UPDATE`.

## 8. MDW pilot design (future work, not implemented here)

Using the NASR evidence already extracted in §4:

1. Parse `APT_RWY.csv` + `APT_RWY_END.csv` for `ARPT_ID == airport.faa_code`
   (same matching precedent as every existing FAA script), preserving each
   row as a `SourceAssertion` (`assertion_type='runway'` /
   `'runway_end'`), reusing the already-existing NASR `Source` row
   (`external_id='faa_nasr:airport_csv:2026-08-06:...'`), with an
   idempotency key of `source_id + deterministic locator (e.g.
   "APT_RWY.csv:line=N") + raw_fragment_hash` — the exact discipline
   `app/evidence/nasr_apt_ars.py` already uses for `APT_ARS.csv`.
2. From those governed assertions, upsert canonical `Runway` rows for MDW.
   Expected result: 4 rows (`04L/22R`, `04R/22L`, `13L/31R`, `13R/31L`).
   Reuse, don't duplicate, the existing legacy row (id 12, `13L/31R`) via
   normalized-designation matching — the exact `_get_or_create_runway`
   pattern `scripts/import_faa_runway_ends.py` already uses and already
   proved safe (it deduplicated MHT/HYA this way). The legacy row's
   existing rename-history note is preserved automatically, since the row
   is enriched (length/width added), not replaced. The other 3 runways get
   newly created rows.
3. Create canonical `RunwayEnd` rows for all 8 MDW ends.
4. Separately — as a proposal, not an automatic action — evaluate the 4
   existing MDW `PhysicalInstallationIdentity` rows (ids 3–6: `04R`, `22L`,
   `13L`, `31R`) against §7's safe-linking rule. Expected: all 4 qualify
   (exact airport, exact designation match, unambiguous), but the actual
   `runway_end_id` write happens only via a separate, explicitly approved
   `--apply` step — same two-phase pattern as
   `scripts/apply_mdw_current_presence_pilot.py`.
5. Backup-before-apply, full test suite, `git diff --check`, row-by-row
   before/after comparison — same verification discipline used for every
   prior slice in `data/backups/`.

## 9. CGF second control case (not implemented, evaluated only)

CGF (`airport_id=57`) has a legacy `Runway` row already: `id=58,
designation="6/24"` — no leading zero, `length_m`/`width_m` both `NULL`.
NASR reports `06/24`, 5502 ft × 100 ft, ends `06`/`24`, both EMAS-equipped.

The model requires **no special case**: the existing leading-zero
normalization (`_normalize_designation` in
`scripts/import_faa_runway_ends.py`, already proven in production) matches
`"6/24"` to canonical `"06/24"`, so the legacy row is reused (not
duplicated) and enriched with length/width from NASR. Two `RunwayEnd` rows
(`06`, `24`) get created. The 2 existing CGF `PhysicalInstallationIdentity`
rows (ids 1–2) both qualify for the same safe-link evaluation as MDW's —
exact airport, exact designation, single unambiguous runway. CGF being the
"boring," single-runway case is exactly the point: it validates the model
doesn't need airport-size-dependent special-casing.

## 10. Migration / backfill strategy

**Phase 1 — schema.** Add `RunwayEnd` (new table) and
`PhysicalInstallationIdentity.runway_end_id` (new nullable FK). Purely
additive; no existing row, column, or relationship changes; no data
migration.

**Phase 2 — capability.** Build and test the narrow `APT_RWY.csv`/
`APT_RWY_END.csv` → `SourceAssertion` preservation parser, and the
assertion → canonical `Runway`/`RunwayEnd` upsert logic. No airport is
processed yet — this phase produces tested, reviewable code, not data.

**Phase 3 — MDW pilot.** Run Phase 2's code scoped to MDW only (§8). Dry
run first; canonical inventory creation and identity-linking are separate,
independently approved applies.

**Phase 4 — CGF control.** Same code, scoped to CGF (§9), validating the
simple single-runway case needs no special-casing.

**Phase 5 — broader U.S. backfill.** Run the same already-validated code
across the rest of RWI's 86 tracked U.S. airports using the same preserved
NASR extract. No new design work — purely widening the scope of Phases 2–4.

**What happens to existing legacy `Runway` rows:** always preserved. Reused
(not duplicated) wherever normalized designation matches a canonical
inventory entry — proven-safe, already used to deduplicate MHT/HYA.
Left untouched and flagged for human review wherever a legacy row's
designation does **not** match anything in the current canonical inventory
(could be a closed/historical runway, a data-entry variant, or a real
discrepancy) — never force-merged or deleted. Existing free-text notes
(e.g. MDW's rename history) are never overwritten.

No unnecessary phases were added beyond what the evidence and the pilot/
control structure already require.

## 11. Identity / normalization rules

**Runway canonical key:** `(airport_id, normalized_pair_designation)`.
**RunwayEnd canonical key:** `(runway_id, normalized_end_designation)`.

Tested against real repository/NASR data, not assumed:

- **Zero-padding:** `"06/24"` (NASR) vs `"6/24"` (CGF's legacy seed) must
  normalize identically. Already solved and already proven in production
  by `scripts/import_faa_runway_ends.py::_normalize_designation` (strips a
  single leading zero per heading component) — reuse it, don't reinvent it.
- **L/C/R suffix:** preserved as-is (uppercased only); it's part of the
  end's identity, not noise (`04R` and `04L` are different ends).
- **Reciprocal pair ordering:** `"04R/22L"` vs `"22L/04R"` name the same
  physical runway. Checked against every real MDW/CGF row extracted for
  this design: NASR itself is internally consistent and always orders the
  lower heading-number end first (`04R/22L`, `13L/31R`, `06/24`) — matching
  the existing legacy-seeded rows exactly, so **zero existing rows would
  need reordering**. Canonical rule: split on `/`, normalize each token,
  order by ascending numeric heading (ignoring the L/C/R suffix for
  ordering purposes only).
- **Display form vs. canonical form:** preserve the source's literal
  written form separately from the normalized canonical form — this
  doesn't require new capability, `SourceAssertion.raw_runway_value` /
  `raw_runway_end_value` already exist precisely for this and are already
  used by other assertion types.

## 12. Runway designation change

MDW already demonstrates this: the pavement now named `13L/31R` was
`13C/31C` until a documented 2025-06-12 rename. Distinguish:

- **Current runway identity** = the canonical `Runway`/`RunwayEnd` row, as
  named by the current authoritative source (NASR for the U.S. today).
  This is what §6–§11 define.
- **Historical designation** = a naming fact about the past ("this pavement
  used to be called X"). Already captured today as free text on the
  legacy `Runway.notes` field, exactly as MDW's rename is recorded now.
  No new subsystem needed to keep doing this.
- **Alias** = a different simultaneous way of referring to the same
  current thing (not observed in the current data; not needed now).
- **Redesignation relationship / "same physical pavement over time"** = a
  formal link between an old canonical row and its current successor.
  Genuinely useful eventually (does a historical `Installation` note filed
  under `13C/31C` describe the same EMAS system now reviewed under
  `13L/31R`?), but building a formal history/alias subsystem now would
  repeat exactly the mistake the 2026-07-22 simplification (§2.2)
  corrected: adding structure before there's enough real, varied data to
  justify its shape. **This can safely remain future work.** Today's
  free-text note on the canonical `Runway` row is sufficient, and nothing
  in §7–§11 depends on it existing.

## 13. Public UI implication

"Banor" stays suppressed for an airport (or a source scope) until its
canonical `Runway`/`RunwayEnd` rows are attributably sourced from a
governed *current inventory* lineage (Phase 2+ of §10) — not merely
*present*. A hand-seeded legacy row existing is not sufficient; what
matters is provenance, not row count, so the eventual gate is "this
airport's runways came from an authoritative current-inventory import,"
tracked per airport (or per import batch), not a single global flag —
otherwise the first non-NASR-covered country would either wrongly stay
suppressed forever or wrongly show an incomplete list as if it were
complete.

Once that threshold is met for an airport, the section can render exactly
the structure the task described:

```
Banor
  04R/22L                          6445 × 150 ft
    EMAS: 04R, 22L
  13L/31R                          6522 × 150 ft
    EMAS: 13L, 31R
  04L/22R                          5507 × 150 ft
    (no reviewed EMAS evidence)
  13R/31L                          3859 × 60 ft
    (no reviewed EMAS evidence)
```

"No reviewed EMAS evidence" must never be rendered as "confirmed absent" —
same non-negation discipline the UI already applies elsewhere (e.g. BGM's
"Aktuell EMAS-status ej verifierad"). No UI work is being done in this
task; this section only defines the future gate and shape.

## 14. Research Engine implication

Canonical `RunwayEnd` gives future WATCH/DISCOVER work a deterministic
resolution target instead of free text. Example: an article says *"Runway
27 will receive EMAS"* at BOS. Native-language discovery (Swedish,
Portuguese, Japanese, Korean, etc.) only needs to extract the runway
reference (`"27"`) and the airport; resolution against BOS's already-
canonical `RunwayEnd` rows (is there a `RunwayEnd` designated `27`, under
which `Runway`?) is airport-scoped, language-independent, and
deterministic — the canonical identity model never needs to know what
language the claim was written in. A confident match lets a proposal cite
`airport_id` + `runway_id` + `runway_end_id`; anything short of an exact,
unambiguous match stays a `SourceAssertion` with raw text preserved,
awaiting human review — exactly the same "propose, never silently apply"
boundary `docs/domain/evidence-installation-identity-spec.md` §9 already
sets for AI/automation generally. No Research Engine work is being done
here; this section only explains why the foundation helps it later.

## 15. What we need now

- One new table: `RunwayEnd (id, runway_id FK, designation, unique(runway_id, designation))`.
- One new nullable column: `PhysicalInstallationIdentity.runway_end_id`.
- A normalization function for pair/end designations (reuse the existing,
  already-proven `_normalize_designation` logic).
- A narrow parser preserving `APT_RWY.csv`/`APT_RWY_END.csv` rows as
  `SourceAssertion`s (`assertion_type='runway'`/`'runway_end'` — already
  valid enum values, zero model change needed for that part).
- A narrow upsert step building canonical `Runway`/`RunwayEnd` rows from
  those governed assertions (reuse-not-duplicate against existing legacy
  rows via normalized-designation matching).
- A narrow, dry-run-first MDW pilot script, mirroring
  `scripts/apply_mdw_current_presence_pilot.py`'s exact discipline.
- The same for CGF as the second control case.

## 16. What we explicitly defer

- No designation-history / alias / redesignation subsystem (§12) — a
  free-text note remains sufficient.
- No automatic retroactive linking of any existing
  `PhysicalInstallationIdentity` or legacy `Installation` row — every link
  stays a separate, human-approved step (§7), regardless of how "obvious"
  it looks.
- No RESA/lighting/geometry/elevation/heading fields on `RunwayEnd` yet —
  `heading` is cheap and available (NASR `TRUE_ALIGNMENT`) and can be
  added later without disruption if the Research Engine needs it;
  `resa_length_m` (from the historical model) has no confirmed source in
  the files actually inspected here and should not be restored on faith.
- No broader-than-MDW/CGF backfill yet (Phase 5, explicitly last).
- No international importers (Brazil/NZ/Europe/Japan/Korea) — only the
  guarantee (§5) that the canonical model doesn't block them later.
- No UI implementation, no public data regeneration.
- No live network fetch anywhere in this design — everything is sourced
  from the already-preserved, already-hash-verified local NASR zip.
- No generic workflow/approval-state engine, no lifecycle event platform,
  no universal airport schema, no global identifier registry. The linking
  discipline (§7) reuses the exact dry-run/apply, human-actor,
  append-only-decision pattern `PhysicalInstallationIdentity` +
  `InstallationAssertionLink` already established — nothing new is
  invented at that layer.

## 17. Proposed next implementation slice

The smallest slice that is independently valuable and independently safe:
**Phase 1 (schema) + Phase 2 (parser/upsert capability) + an MDW-scoped dry
run only, no apply.** Concretely: add the `RunwayEnd` table and the
`PhysicalInstallationIdentity.runway_end_id` column; write and test the
`APT_RWY`/`APT_RWY_END` → `SourceAssertion` → canonical-`Runway`/`RunwayEnd`
pipeline; run it in dry-run mode against MDW only and report what it would
create and what it would propose linking — without applying anything. This
mirrors exactly how the MDW current-presence pilot itself started, is fully
reversible, requires no destructive step, and directly produces the report
needed for a human to approve the first real `--apply`.

---

## Explicit answers

**A. Do we need a canonical RunwayEnd entity?**
Yes. `runway_end` currently exists only as an unconstrained free string on
three unrelated tables; there is no way to validate, enumerate, or safely
link against it. A minimal `RunwayEnd(runway_id, designation)` table closes
this, matches NASR's own two-table structure, and matches the shape
`PhysicalInstallationIdentity` already anticipates.

**B. Should existing Runway be retained or replaced?**
Retained, unchanged in schema. It already correctly represents "one
physical runway pair" — the gap is missing rows and a missing end-level
child, not a wrong parent shape. Existing legacy rows are preserved and
reused (matched via normalization), never replaced or deleted.

**C. Can FAA NASR populate current U.S. runway inventory deterministically?**
Yes, from data already preserved locally. `APT_RWY.csv` and
`APT_RWY_END.csv` in the already-downloaded, SHA-256-verified
`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip` give one authoritative
row per runway pair and per runway end for every U.S. airport in the NASR
extract (verified concretely for MDW: 4 runways/8 ends; CGF: 1 runway/2
ends) — no new network access is required.

**D. Can existing MDW physical identities eventually be linked safely?**
Yes, for all 4 — but only through the explicit, human-approved linking
step in §7 (exact airport + exact normalized designation match against a
governed canonical inventory), never automatically as a side effect of
adding the schema or importing NASR data. The same holds for CGF's 2
identities.

**E. What is the smallest next implementation slice?**
Schema (Phase 1) + parser/upsert capability (Phase 2) + an MDW-scoped dry
run with no apply (§17) — fully reversible, no data mutation, produces the
concrete report a human needs to approve the first real MDW pilot apply.
