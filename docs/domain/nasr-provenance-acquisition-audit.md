# FAA NASR Provenance and Acquisition Audit

**Read-only audit. Nothing was modified, downloaded, committed, pushed, or
deployed.** Every claim below is either (a) computed directly from the
local file bytes right now, or (b) quoted/paraphrased from tracked
repository content (git history, committed docs, committed code), with the
two kinds kept explicitly separate throughout.

## 1. Local artifacts found

| Path | Tracked in git? |
|---|---|
| `data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip` | **No** — `data/raw/*` is gitignored (`.gitignore:9`), only `data/raw/.gitkeep` is exempted (`.gitignore:10`) |
| `data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip.metadata.json` | **No** — same gitignore rule covers it; it is a filesystem sidecar, never committed |

Both files exist only on local disk, preserved outside git by design — the
`.gitignore` rule is deliberate raw-data hygiene (a multi-megabyte FAA
archive doesn't belong in version control), not an oversight. This means
git history can prove **what code was written to consume/produce such an
archive**, but cannot itself prove **which specific archive bytes were
retrieved when** — that provenance lives only in the sidecar JSON and the
archive itself.

**Directly relevant tracked material found:**

- `docs/domain/evidence-installation-identity-slice5-report.md` — committed
  as part of `3cadbae` ("Add FAA NASR EMAS evidence acquisition", 2026-08-16
  09:00:14 +02:00). This is the single most load-bearing document for this
  audit: it names the exact same cycle, byte size, and SHA-256 that are on
  disk today, and narrates the acquisition in first-person project-report
  style.
- `app/acquisition/faa_runway_ends.py` — the FAA NASR subscription-index
  discovery/parse code (index → cycle page → archive URL).
- `app/evidence/nasr_apt_ars.py` / `app/evidence/nasr_apt_rwy.py` —
  read-only, SHA-256-verifying readers of the **already-downloaded**
  archive (EMAS-presence and runway-inventory CSVs respectively). Neither
  downloads anything.
- `scripts/dry_run_nasr_apt_ars.py` — reads the sidecar metadata and the
  archive to propose a `Source`/`SourceAssertion` dry run; does not
  download or write metadata.
- `tests/test_nasr_source_provenance.py` — one deterministic-identity unit
  test for the proposed `Source` row, unrelated to file acquisition itself.
- Git history: `6d8ccfc` ("slice 5"), `3cadbae` ("Add FAA NASR EMAS evidence
  acquisition") — no earlier or later commit in `git log --all` mentions
  NASR, acquisition, or Slice 5 beyond these.

**Not found anywhere in the repository (tracked or untracked, searched by
content):** any script that downloads the FAA NASR archive to disk,
computes its SHA-256, and writes a metadata sidecar. See §6/§8.

## 2. Metadata contents

```json
{
    "source_index_url":  "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/",
    "cycle_page_url":  "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-08-06/",
    "final_archive_url":  "https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip",
    "nasr_cycle":  "2026-08-06",
    "retrieved_at":  "2026-08-16T06:51:07.9581445Z",
    "sha256":  "dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1",
    "byte_size":  8034151
}
```

The file has a UTF-8 BOM (`EF BB BF`) preceding the `{` — confirmed by a
raw byte dump. This is already handled correctly everywhere the file is
read in this repository: every reader
(`app/evidence/nasr_apt_rwy.py`, `app/evidence/nasr_apt_ars.py`,
`scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py`, etc.) opens it
with `encoding="utf-8-sig"`, which strips the BOM. Not a defect — noted
only because a naive `utf-8` read would silently corrupt the first key
name.

## 3. Actual ZIP size/hash verification

Computed just now, directly from the local file's bytes (not from any
cached or prior report):

| | Recorded (metadata JSON) | Actual (computed now) | Match |
|---|---|---|---|
| **SHA-256** | `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1` | `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1` | **YES** |
| **Byte size** | `8034151` | `8034151` | **YES** |

**RECORDED HASH:** `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1`
**ACTUAL HASH:** `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1`
**MATCH: YES**

**RECORDED SIZE:** `8034151`
**ACTUAL SIZE:** `8034151`
**MATCH: YES**

The recorded hash string was checked character-by-character: exactly 64
characters, every character a valid lowercase hex digit (`0-9a-f`) — no
truncation, no extra character, no stray whitespace. Whatever "two
slightly different textual hash renderings" were seen in prior
conversation/reporting, **the value actually stored in the sidecar JSON on
disk right now is well-formed and matches the local file's true hash
exactly**. This audit treats the freshly-computed local hash as
authoritative, per instruction, and it agrees with the recorded value —
there is no discrepancy to resolve in the file itself.

## 4. Archive-member verification

Read-only inventory listing (no extraction beyond listing):

- Archive: `06_Aug_2026_APT_CSV.zip`, cycle `2026-08-06`, 10 members total.
- **`APT_ARS.csv`** — present, 36,220 bytes (EMAS/arresting-system evidence — Slice 5's source).
- **`APT_RWY.csv`** — present, 3,009,879 bytes (runway-pair inventory — this session's canonical-runway source).
- **`APT_RWY_END.csv`** — present, 11,559,350 bytes (runway-end inventory — this session's canonical-runway source).
- Also present: `APT DATA LAYOUT.pdf`, `APT_ATT.csv`, `APT_BASE.csv`, `APT_CON.csv`, `APT_CSV_DATA_STRUCTURE.csv`, `APT_RMK.csv`, `CSV_README.pdf`.

All three CSVs this project actually depends on are present and intact in
the archive.

## 5. Acquisition actor determination

Repository evidence establishes a clear **method** but not a provable
**actor**:

- `docs/domain/evidence-installation-identity-slice5-report.md` states:
  *"Approved controlled acquisition retrieved cycle 2026-08-06 from the FAA
  discovery flow"* and separately: *"The next live request would first
  fetch the exact index URL above, then only the cycle page and the archive
  URL it explicitly publishes... It must not write the development
  database."* This is the same dry-run-then-explicitly-approved pattern
  used throughout this repository's own task history (including this
  session's own canonical-runway work) — an approval gate, not a
  fully-autonomous or purely-manual download.
- The `retrieved_at` timestamp (`2026-08-16T06:51:07.9581445Z`) is roughly
  9 minutes before commit `3cadbae`'s timestamp (`2026-08-16 09:00:14
  +02:00` = `07:00:14 UTC`) — tight temporal proximity to the Slice 5 work,
  consistent with the artifact being acquired as an immediate precursor to
  that commit, not at some unrelated earlier time.
- **No script that performs the download-and-preserve step is committed
  anywhere in the repository** (confirmed by content search for
  `urlopen`/`requests.get`/`httpx` combined with archive-writing logic, and
  for any script that *writes* — as opposed to *reads* — a
  `*.metadata.json` sidecar; see §6). The Slice 5 report itself
  acknowledges this gap explicitly, calling the missing piece "this small
  acquisition wrapper" needed because "the existing in-memory fetcher does
  not yet preserve artifact bytes/metadata." Whatever wrapper actually
  performed the download was therefore either a manual one-off command
  sequence or a throwaway script — and it was never checked into version
  control either way.

Given A–E:

- **A (user manually downloaded it):** possible, not provable or excludable.
- **B (Claude acquired it):** consistent with the project's established
  agentic-task pattern and the approval-gated language in the Slice 5
  report, but not provable — no committed script, log, or agent
  attribution exists.
- **C (Codex acquired it):** no evidence either way.
- **D (a repository script acquired it):** **no** — no such script exists
  in the repository, committed or otherwise found on disk.
- **E (unknown actor, reproducible path documented):** **best fit.** The
  acquisition *method* (index → cycle page → archive URL → download → hash
  → sidecar) is clearly documented and reproducible from
  `app/acquisition/faa_runway_ends.py`'s discovery logic plus the Slice 5
  report's narrative, but the *specific execution* that produced this exact
  file is not preserved as auditable code or logs.

**ORIGINAL_ACQUISITION_ACTOR: UNKNOWN**

## 6. Acquisition method determination

The metadata's three URLs form a coherent, internally consistent chain
matching the documented FAA NASR discovery flow exactly:

1. `source_index_url` — the NASR subscription index page.
2. `cycle_page_url` — that index's dated cycle page for `2026-08-06`
   (`source_index_url` + `2026-08-06/`, matching the pattern).
3. `final_archive_url` — an `nfdc.faa.gov` date-stamped `*_APT_CSV.zip`
   link, matching exactly the shape `app/acquisition/faa_runway_ends.py`'s
   `_APT_CSV_HREF` regex is built to extract from a cycle page
   (`https://nfdc\.faa\.gov/webContent/28DaySub/extra/[^"]*_APT_CSV\.zip`).

This is **PROVEN FROM LOCAL METADATA**: the three URLs are structurally
coherent with each other and with the only NASR-discovery code that exists
in this repository. This is **NOT VERIFIED LIVE AGAINST FAA** — no network
request was made in this task, per instruction; whether these URLs are
still the current/official FAA paths as of today (2026-08-17) was not
checked and would require explicit authorization for new network access.

## 7. Existing acquisition code

Checked against the 11 capabilities listed in the task:

| # | Capability | Status |
|---|---|---|
| 1 | Find the FAA NASR subscription index | **Have it** — `app/acquisition/faa_runway_ends.py::discover_apt_csv_url()` fetches `NASR_INDEX_URL`. |
| 2 | Determine the current/latest cycle | **Have it** — same function parses all cycle dates from the index and picks the latest one `<= today`. |
| 3 | Locate the APT CSV archive for that cycle | **Have it** — same function fetches the cycle page and regex-extracts the `*_APT_CSV.zip` link. |
| 4 | Download the archive | **Partially** — `fetch_emas_arresting_system_rows()` downloads it, but only **in memory** (`io.BytesIO(response.content)`), immediately parses `APT_ARS.csv`, and discards the bytes. Nothing else in the repo downloads the archive at all. |
| 5 | Preserve the original archive under `data/raw/nasr/<cycle>/` | **Missing** — no code path writes archive bytes to disk anywhere in the repository. |
| 6 | Compute SHA-256 | **Missing** (write-side) — SHA-256 is computed only by *readers* verifying an already-present archive (`_verify_artifact()` in both `nasr_apt_rwy.py` and `nasr_apt_ars.py`); no code computes it at acquisition time. |
| 7 | Store byte size | **Missing** (write-side), same reasoning. |
| 8 | Store retrieval timestamp | **Missing** (write-side). |
| 9 | Preserve source index URL | **Missing** (write-side). |
| 10 | Preserve cycle page URL | **Missing** (write-side). |
| 11 | Preserve final archive URL | **Missing** (write-side). |

In short: **discovery (steps 1–3) is fully implemented and reusable as-is.
Preservation (steps 4–11) is not implemented anywhere in the repository** —
the only thing that ever produced steps 4–11's output was whatever
one-off, uncommitted mechanism created the file currently on disk.

**A separate, unrelated provenance mechanism already exists at the
database level** and is worth distinguishing from the above so it isn't
conflated: `app/models/acquisition.py` defines `AcquisitionSource`
/`AcquisitionRun`/`Snapshot` — a fully-built, immutability-enforced,
SHA-256/byte-size/retrieved-timestamp-tracking schema, wired to a live
service (`app/services/acquisition.py::AcquisitionService.acquire()`) and
used by `app/scripts/capture_faa_emas.py` for a *different* FAA source (an
EMAS construction/Tableau report, not NASR). Read-only inspection of the
real database confirms `acquisition_sources`, `acquisition_runs`, and
`snapshots` all currently hold **zero rows** — this mechanism has never
been used for the NASR artifact and is effectively dormant for this
purpose. It is a plausible foundation to build on (§8), not something
already wired to NASR.

## 8. Missing automation pieces

To close the gap identified in §7, RWI would need one new, narrowly-scoped
"preserve" wrapper that:

- calls the existing `discover_apt_csv_url()` unchanged (no rewrite needed);
- streams the response to `data/raw/nasr/<cycle>/<published-filename>`
  instead of into memory;
- computes SHA-256 and byte size from the written file (not from the
  in-flight response, to guarantee the hash matches what's actually on
  disk);
- writes the seven-field sidecar JSON (§10) next to it, matching the
  format already in use;
- never writes to the development database (matching every other
  acquisition/apply script's discipline already established in this
  repository — dry-run-first, explicit `--apply`, backup-before-write where
  applicable).

This is a small, additive script — no existing code needs to change, and
no existing behavior needs to be broken to add it.

## 9. Recommended minimal provenance format

The seven fields already present in the sidecar JSON are sufficient and
should be kept exactly as-is — no larger provenance subsystem is
warranted:

| Field | Present today? |
|---|---|
| `source_index_url` | Yes |
| `cycle_page_url` | Yes |
| `final_archive_url` | Yes |
| `nasr_cycle` | Yes |
| `retrieved_at` (UTC) | Yes |
| `sha256` | Yes |
| `byte_size` | Yes |
| local archive path | Implicit (the sidecar's own location, `<archive>.metadata.json` next to `<archive>`) — could be made explicit as an `archive_filename` field, but is not strictly necessary since the convention is self-describing. |
| `publisher` | **Not present** — always "Federal Aviation Administration" for this source; cheap to add for cross-source consistency if RWI ever ingests non-FAA data the same way. |
| `dataset`/`product` | **Not present** — e.g. `"NASR 28-Day Subscription — Airport (APT) CSV"`; useful once RWI has more than one NASR product cached side-by-side (it does not yet). |
| acquisition mechanism/script version | **Not present** — worth adding once a real acquisition wrapper exists (§8), so a future audit like this one doesn't have to guess at method from doc narrative. |
| acquisition actor/tool | **Not present**, marked optional in the task — would have made §5 of this audit conclusive instead of "UNKNOWN" had it existed. |

**Recommendation:** keep the sidecar-JSON-next-to-the-archive convention
(it already matches current practice and every reader already expects
it) and add three fields going forward — `publisher`, `dataset`, and
`acquisition_mechanism_version` — the next time a real acquisition wrapper
is built. Nothing needs to change about the seven fields already in use.

## 10. Future-cycle reuse assessment

**Yes, conceptually reusable.** The discovery logic
(`discover_apt_csv_url()`) is cycle-agnostic by construction — it always
resolves "the latest effective cycle as of today," not a hardcoded date —
so it needs no modification to support a future NASR cycle. Only the
missing "preserve to disk + hash + sidecar" step (§8) needs to be built;
once it exists, running it again later naturally produces
`data/raw/nasr/<new-cycle>/<new-filename>` with its own sidecar, alongside
(not replacing) `2026-08-06`'s.

## 11. Remaining uncertainty

- The specific human/agent/tool that performed the original download
  cannot be determined from repository evidence — see §5.
  `ORIGINAL_ACQUISITION_ACTOR: UNKNOWN` is final for this audit.
- Whether `source_index_url`/`cycle_page_url`/`final_archive_url` are
  still FAA's current, live paths was **not checked** — that requires new
  network access, explicitly out of scope for this task per instruction.
  Treat §6's "coherent chain" finding as internal-consistency-only, not
  live confirmation.
- Whether any *other*, non-repository record of the original acquisition
  exists (e.g. shell history, an external agent's own logs outside this
  repository) is unknowable from repository evidence and was not
  investigated, since it isn't repository evidence.

---

## Explicit answers

**A. Is the local ZIP byte/hash verified against its metadata?**
**Yes.** Recomputed just now directly from the file: SHA-256 and byte size
both match the sidecar JSON exactly. No truncation or malformed characters
found in the recorded hash (64 valid hex characters).

**B. Can we prove it came from the recorded FAA archive URL?**
**Not from repository evidence alone.** The three recorded URLs are
internally coherent with each other and with the only NASR-discovery code
in the repo (`app/acquisition/faa_runway_ends.py`) — that's proof of
*consistency*, not proof of *provenance*. Confirming the URLs against the
live FAA site would require new network access, which this task explicitly
did not perform.

**C. Can we prove who downloaded it?**
**No.** `ORIGINAL_ACQUISITION_ACTOR: UNKNOWN`. The method is well-narrated
in a committed report (`docs/domain/evidence-installation-identity-slice5-report.md`)
and matches this project's standard approval-gated pattern, but no
committed script, log, or attribution exists to identify the specific
actor or exact commands used.

**D. Do we already have a reusable acquisition mechanism?**
**Half of one.** Discovery (index → cycle → archive URL) is fully built
and reusable as-is. Preservation (download-to-disk, hash, sidecar) is not
implemented anywhere in the repository — it was performed once, by an
unpreserved mechanism, and never committed. A separate, dormant
DB-level provenance model (`AcquisitionRun`/`Snapshot`) exists but is wired
to a different FAA source and holds zero rows for NASR.

**E. Can this become the source path for future automatic NASR updates?**
**Yes, in principle**, once the missing preservation wrapper (§8) is
built — the discovery logic needs no changes and is already cycle-agnostic.

**F. What, if anything, must be fixed before we trust the provenance record?**
Nothing is *broken* — the hash/size verify cleanly, the archive members
are present and intact, and the URL chain is internally coherent. What's
*missing*, before this becomes a repeatable, auditable process rather than
a one-off: (1) a committed acquisition-and-preserve script (§8), so the
next cycle's provenance doesn't again depend on an unpreserved manual
step; (2) live confirmation against the actual FAA site, which was
explicitly out of scope here and would need separate authorization.

---

No modification. No download. No commit. No push. No deployment.
