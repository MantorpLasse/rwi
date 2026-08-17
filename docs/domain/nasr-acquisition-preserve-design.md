# FAA NASR Live Source Verification + Acquisition-and-Preserve Design

**Read-only design task. Nothing was implemented, downloaded-and-preserved,
committed, pushed, or deployed, and the database was not touched.** Live
network access to official FAA/NFDC hosts was used for verification only,
per explicit authorization for this task. No new NASR cycle archive was
downloaded or saved anywhere in the repository.

## 1. Live FAA verification result

`https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/`
is **still the official, live FAA NASR subscription index** as of
2026-08-17 (page `Last-Modified`: 2026-08-05). One caveat on tooling: the
built-in `WebFetch` tool received an HTTP 403 from this host (FAA's edge
protection appears to block WebFetch's fetch signature specifically); a
direct `curl` with a standard browser `User-Agent` succeeded with `200 OK`
on every request made in this audit. This is noted because it affects how
any future automated acquisition code must identify itself (see §9).

**Redirects observed:** exactly one, and it's benign — plain `http://` to
the same path on `https://` (`301 Moved Permanently` via Akamai). No other
redirect occurred anywhere in this verification (index page, cycle page,
or archive host all responded directly).

## 2. Current official NASR acquisition chain

Confirmed live, by fetching real content (not summarized/cached
third-party snippets):

- **Cycles are exposed as two sections on the index page**: `<h2>Preview</h2>`
  (the not-yet-effective next cycle) and `<h2>Current</h2>` (the effective
  cycle), each linking to `./../NASR_Subscription/<YYYY-MM-DD>`. As of now:
  Preview = `2026-09-03`, Current = `2026-08-06`.
- **Cycle URLs follow a stable, predictable pattern**:
  `https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/<YYYY-MM-DD>/`
  — exactly what `app/acquisition/faa_runway_ends.py`'s
  `NASR_CYCLE_URL_TEMPLATE` already assumes.
- **The APT CSV archive is exposed per-cycle**, not on the index page: the
  `2026-08-06/` cycle page lists ~24 per-product CSV archives (APT, ATC,
  AWY, ARB, AWOS, ...), each at
  `https://nfdc.faa.gov/webContent/28DaySub/extra/<DD_Mon_YYYY>_<PRODUCT>_CSV.zip`.
  The APT entry reads exactly:
  `<a href="https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip">Airports and Other Landing Facilities (APT)</a>`
  — byte-for-byte identical to our recorded `final_archive_url`.
- **`nfdc.faa.gov` remains the real archive host**, serving from an Apache
  origin (`Server: Apache/2.4.37 (Red Hat Enterprise Linux)`), not a
  redirect/proxy stub.
- **The latest/current available cycle right now is `2026-08-06`** — the
  same cycle already preserved locally. (`2026-09-03` exists as "Preview"
  but is not yet effective as of today, 2026-08-17, matching
  `discover_apt_csv_url()`'s own effective-date filter: it only accepts
  cycles with `date <= today`.)

An important distinction the index page itself makes clear: its own
`<h2>Archives</h2>` section only lists **full 28-day subscription bundles**
(`28DaySubscription_Effective_<date>.zip`) for *past* cycles — a different
URL/filename pattern from the per-product `.../extra/<date>_APT_CSV.zip`
files. The per-product archive (what this project actually uses) is only
discoverable by visiting the specific cycle's own page, exactly as the
existing discovery code already does. This confirms the two-step
index→cycle-page discovery is necessary, not an accidental extra hop.

## 3. Verification of the 2026-08-06 chain

| Check | Method | Result |
|---|---|---|
| Index page still resolves the `2026-08-06` cycle as "Current" | Live `curl` of the index page | **Confirmed** |
| Cycle page's APT CSV link matches recorded `final_archive_url` exactly | Live `curl` of the cycle page, string comparison | **Confirmed, byte-for-byte identical** |
| `discover_apt_csv_url()` (unmodified, existing repo code) reproduces the same URL | Executed live, right now, in-process | **Confirmed** — returned `https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip` exactly |
| Live archive byte size matches recorded/local `8034151` | A 1-byte `Range: bytes=0-0` GET (not a full download) returned `Content-Range: bytes 0-0/8034151` and `ETag: W/"8034151-1783539280548"` | **Confirmed, exact match, without downloading the archive** |
| Live archive SHA-256 matches recorded/local hash | **Not performed** — FAA/NFDC publishes no checksum anywhere on the index or cycle page (searched, found none), so the only way to independently re-verify the *content* hash live would be a full ~8 MB download. Per instruction ("do not download an 8 MB archive merely to prove a point unless necessary"), this was **not done**. | **Not independently re-verified live** — reported explicitly rather than assumed |

A plain `HEAD` request to the archive URL returned a misleading `503`
from an Akamai NetStorage edge layer in front of the real Apache origin —
this is a known-shape CDN quirk (some storage-backed static hosts mishandle
bare `HEAD`), not a real outage. The ranged `GET` above proved the real
origin is live and serving the correct byte count; a future acquisition
wrapper should use ranged/streaming `GET`, not `HEAD`, for pre-flight
checks against this host.

**Overall: the full chain — FAA index → `2026-08-06` cycle page → exact
archive URL → exact byte size — is PROVEN FROM LIVE VERIFICATION today**,
not merely internally coherent as the prior provenance audit could only
establish from local metadata alone. Content-hash identity (SHA-256)
remains **PROVEN FROM LOCAL METADATA** (the local file's computed hash
matches its own sidecar) but is **not independently re-confirmed against
the live remote bytes**, since that would require a full download this
task deliberately avoided.

## 4. Existing reusable repository code

`app/acquisition/faa_runway_ends.py` cleanly separates into two concerns,
confirmed by reading the code and its tests (`tests/test_faa_runway_ends.py`,
which mocks all network I/O via `httpx.MockTransport` and tests each piece
independently):

**Reusable discovery (no EMAS/domain coupling at all):**
- `NASR_INDEX_URL`, `NASR_CYCLE_URL_TEMPLATE`, `_CYCLE_DATE`, `_APT_CSV_HREF` — pure URL/regex constants.
- `discover_apt_csv_url(*, client, today, timeout=30.0) -> str` — fetches the index, picks the latest cycle `<= today`, fetches that cycle's page, extracts the APT CSV link. Returns a URL string; touches nothing else. **Verified live and working in §3 without any modification.**
- `RunwayEndsSourceError` — the exception type, currently named after the EMAS/runway-ends domain even though the discovery logic it guards is generic.

**Not reusable as-is (EMAS-specific parsing, tightly coupled):**
- `fetch_emas_arresting_system_rows(*, client, apt_csv_url, timeout=60.0) -> list[ArrestingSystemRow]` — downloads the archive **into memory only** (`io.BytesIO(response.content)`), opens it, immediately locates and parses `APT_ARS.csv` specifically, and filters to `ARREST_DEVICE_CODE == "EMAS"` rows. It never writes bytes to disk, never computes a hash, never preserves a copy, and is hard-wired to one specific CSV member and one specific business filter.
- `ArrestingSystemRow` — the EMAS row shape, domain-specific.

**Conclusion:** `discover_apt_csv_url()` can be reused completely unchanged
by a new acquisition-and-preserve module. Nothing else in this file is
reusable for a generic "preserve the raw archive" concern — that
capability doesn't exist yet anywhere in the repository (confirmed
separately by the prior provenance audit's exhaustive search).

## 5. Missing preservation capability

Restating precisely, now confirmed against live behavior: no code
anywhere downloads the archive to disk, computes its hash/size from the
written file, validates its ZIP structure/expected members, or writes a
provenance sidecar. §3 showed the *discovery* half of this already works
perfectly live; the *preserve* half must be built from nothing.

## 6. Proposed minimal module/script (design only — not implemented)

A new module, `app/acquisition/nasr_apt_csv.py`, following this
repository's existing acquisition-module convention (one file per FAA data
family, e.g. `faa_runway_ends.py`, `faa_aip_grants.py`, `faa_tableau.py`):

```
discover_latest_nasr_cycle(*, client, today) -> str
    # Thin wrapper reusing the existing NASR_INDEX_URL fetch + _CYCLE_DATE
    # parse + effective-date filter already inside discover_apt_csv_url() -
    # factored out so the cycle string itself (needed for the sidecar and
    # for the data/raw/nasr/<cycle>/ path) is available to the caller, not
    # just the final archive URL.

discover_apt_archive(*, client, cycle) -> tuple[str, str]
    # Returns (cycle_page_url, final_archive_url). Reuses the existing
    # cycle-page fetch + _APT_CSV_HREF regex from discover_apt_csv_url()
    # unchanged - just split so both URLs are individually available for
    # the sidecar, not only the final one.

acquire_and_preserve_nasr_apt(*, client, target_dir=Path("data/raw/nasr"), today=None) -> NasrAcquisitionResult
    # Orchestrates the full flow described below. The only function a
    # caller/script actually needs.
```

**Required behavior, mapped to the task's 12-point list:**

1. **Discover official FAA NASR cycle** — `discover_latest_nasr_cycle()`, reusing the existing effective-date logic verbatim.
2. **Resolve official APT CSV archive URL** — `discover_apt_archive()`, reusing the existing regex verbatim.
3. **Refuse unexpected/non-FAA source hosts unless explicitly reviewed** — a hardcoded allowlist of exactly two hostnames (`www.faa.gov`, `nfdc.faa.gov`); any URL discovered that resolves to a different host raises immediately, before any request to it is made. This is a fail-closed check on the *discovered* URLs, not just the hardcoded constants — protects against a future FAA page change silently pointing somewhere unexpected.
4. **Download to a temporary file first** — stream the response body directly to a `NamedTemporaryFile` (or `tempfile.mkstemp()`) in the same filesystem as the final destination (so the final move in step 9 can be an atomic rename, not a cross-device copy); never buffer the whole ~8 MB in memory (unlike the existing EMAS fetch path).
5. **Compute SHA-256** — streamed over the temp file after download completes, not over the in-flight response (matches this repository's existing verification convention in `nasr_apt_rwy.py`/`nasr_apt_ars.py`, which always hash the file that's actually on disk).
6. **Compute byte size** — `Path.stat().st_size` on the temp file, same reasoning.
7. **Validate ZIP structure** — open the temp file with `zipfile.ZipFile` and call `testzip()` (or equivalent) before trusting it further; a corrupt/truncated download must fail closed here, not silently produce a bad "preserved" artifact.
8. **Require expected members at minimum** — confirm `APT_ARS.csv`, `APT_RWY.csv`, `APT_RWY_END.csv` are all present in the archive's namelist (case-insensitive suffix match, matching the existing readers' convention); missing any one aborts before the move in step 9.
9. **Move into immutable raw storage only after validation** — `os.replace()`/`Path.rename()` the validated temp file into place; nothing is ever written directly to the final path, so a failure at any earlier step leaves the raw-storage directory untouched.
10. **Store under `data/raw/nasr/<YYYY-MM-DD>/`** — using the discovered cycle string, matching the existing convention exactly.
11. **Preserve original archive filename** — taken from the discovered URL's own path component (`06_Aug_2026_APT_CSV.zip`), never renamed or reformatted.
12. **Write a sidecar JSON next to it** — `<archive_filename>.metadata.json`, schema in §7 below.

## 7. Sidecar schema

Starting from the seven fields already proven in production use, adding a
`schema_version` and the fields discussed in the prior audit:

```json
{
  "schema_version": 1,
  "publisher": "Federal Aviation Administration",
  "dataset": "NASR 28-Day Subscription — Airport (APT) CSV",
  "source_index_url": "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/",
  "cycle_page_url": "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/<cycle>/",
  "final_archive_url": "https://nfdc.faa.gov/webContent/28DaySub/extra/<filename>",
  "nasr_cycle": "<YYYY-MM-DD>",
  "retrieved_at": "<UTC ISO-8601 with Z>",
  "sha256": "<64 lowercase hex chars>",
  "byte_size": 0,
  "local_archive_filename": "<filename>",
  "acquisition_method": "app.acquisition.nasr_apt_csv.acquire_and_preserve_nasr_apt"
}
```

`schema_version` lets a future reader distinguish today's seven-field
sidecars (implicitly version 0/unversioned) from this format without
guessing from field presence. `acquisition_method` records *what code*
produced the file (module path, optionally `:version` once the module has
one) — this directly closes the "no committed download-and-preserve
mechanism" gap the prior audit flagged, since every future sidecar will
name the exact reusable function that made it.

**Deliberately excluded, per instruction:** an acquisition-actor field.
The mechanism must stay valid whether invoked by a human, Claude, Codex,
CI, or a future WATCH job — baking in an actor identity would make the
schema's meaning depend on *who ran it* rather than *what was retrieved*.
If a future caller wants to record that separately, it can be layered on
as a genuinely optional field without changing this schema's contract.

## 8. Immutability / collision rules

Fail-closed, checked in this order before any write:

1. **Target directory `data/raw/nasr/<cycle>/` doesn't exist yet** →
   proceed normally (steps 1–12 above).
2. **Target directory exists, archive + sidecar both present, and the
   freshly-downloaded temp file's SHA-256 matches the existing sidecar's
   `sha256`** → **idempotent no-op**: discard the temp file, do not
   overwrite anything, report "already preserved." This is the expected,
   common case for re-running acquisition against a cycle that hasn't
   changed.
3. **Target directory exists, but the freshly-downloaded temp file's
   SHA-256 differs from the existing sidecar's `sha256`** → **STOP,
   provenance collision.** Neither file is touched — the existing archive
   and sidecar stay exactly as they are, and the newly-downloaded temp
   file is discarded (or optionally preserved *alongside*, under a
   distinguishing name, for human inspection — a design choice to make at
   implementation time, not this task). Report the collision with both
   hashes so a human can decide what it means (FAA silently republished
   a cycle under the same date? A corrupted prior download? Something
   else?) — never guess.
4. **Sidecar exists but its own recorded `sha256`/`byte_size` don't match
   the archive file actually sitting next to it** → **STOP, integrity
   failure**, independent of any new download. This check should also run
   as a standalone verification (no network needed) so an existing local
   archive can be re-validated at any time without re-fetching anything —
   effectively formalizing what this and the prior audit task already did
   by hand into reusable code.

Nothing is ever silently overwritten in any of these paths — every branch
either writes to a brand-new location or refuses to write at all.

## 9. Security / source-host rules

- **Host allowlist**: exactly `www.faa.gov` (index + cycle pages) and
  `nfdc.faa.gov` (archive downloads). Any discovered URL resolving outside
  this pair aborts before the request is made — see §6 point 3.
- **HTTPS only** — both real hosts already enforce this (plain `http://`
  redirects to `https://`); the client should request `https://` directly
  rather than relying on the redirect, to avoid a silent request over
  plaintext with a middlebox in between.
- **User-Agent required** — confirmed necessary in this task: `WebFetch`'s
  default fetch signature received `403 Forbidden` from `www.faa.gov`,
  while an ordinary browser-shaped `User-Agent` succeeded on every request.
  Any acquisition code must set an explicit, honest `User-Agent` (e.g.
  identifying the project) rather than rely on a client library's default.
- **No credentials/authentication of any kind** — both hosts serve this
  content publicly; nothing here should ever carry secrets, and the
  acquisition module should not accept any.
- **Bounded timeouts and bounded download size** — both already present
  in the existing discovery code's `timeout` parameters; a preserve
  wrapper should additionally cap the total streamed bytes at a sane
  ceiling (e.g. comfortably above the largest observed NASR product
  archive) so a misbehaving/compromised host can't be used to fill disk
  via this path.
- **`HEAD` is unreliable against `nfdc.faa.gov`** — confirmed live in §3
  (misleading `503` from an Akamai NetStorage edge). Preflight checks
  should use a small ranged `GET`, not `HEAD`.

## 10. Acquisition / ingestion boundary

Confirmed by design, not just by policy: every function proposed in §6
only ever touches the filesystem under `data/raw/nasr/` and returns a
result object — none of them accept a database `Session` parameter at
all, which makes the boundary structurally enforced rather than merely
promised. This mirrors this repository's own established convention (the
existing `plan_*`/`evaluate_*` functions in `app/services/runway_inventory.py`
are pure-`SELECT`; `apply_plan()` is the only function that writes, and
it's always a separate, explicitly-invoked step). The acquisition module
introduces no analogous "apply" function at all — ingestion/comparison
against the already-ingested cycle (§13 below) is a wholly separate,
not-yet-designed future piece that would consume the sidecar +
already-preserved archive as its read-only input, exactly the way
`app/evidence/nasr_apt_rwy.py` already consumes today's preserved
`2026-08-06` archive for this project's canonical-runway work.

## 11. DB provenance-model decision

**Recommendation: A — sidecar JSON only, for now.**

Reasoning:
- The dormant `AcquisitionSource`/`AcquisitionRun`/`Snapshot` model
  (`app/models/acquisition.py`) is a real, well-built mechanism, but it's
  wired to a *different* FAA source (`app/scripts/capture_faa_emas.py`,
  via `AcquisitionService`) and currently holds **zero rows** for
  anything, confirmed in the prior audit. Wiring NASR into it would mean
  extending/reusing infrastructure that has no track record of working
  end-to-end for *any* source yet, which is a bigger, riskier first step
  than this task's stated goal of "the smallest reproducible mechanism."
- The sidecar-JSON convention already works, is already what every
  existing NASR reader in this repository expects
  (`_verify_artifact()` in both `nasr_apt_rwy.py` and `nasr_apt_ars.py`),
  and was independently proven trustworthy by both provenance-audit tasks
  in this session (hash/size verified clean, chain verified live).
  Changing nothing about *how downstream code reads provenance* while
  fixing *how it gets written* is the minimal-risk path.
- Nothing forecloses migrating to the DB model later — a future task could
  backfill `Snapshot` rows from existing sidecar JSONs (the `Snapshot`
  schema's fields are a superset of the sidecar's) once that model has an
  actual proven caller. Building that bridge now, before either side has
  more than one real use case, would be solving a problem RWI doesn't have
  yet.

## 12. Test plan (design only — no implementation)

All tests would mock network I/O via `httpx.MockTransport`, following
`tests/test_faa_runway_ends.py`'s existing pattern exactly, plus use
`tmp_path` for all filesystem assertions (never touching real
`data/raw/nasr/`):

- **Official host allowlist** — a discovered/injected archive URL on a
  non-`nfdc.faa.gov` host raises before any request to it is made; same
  for a non-`www.faa.gov` index/cycle URL.
- **Cycle discovery** — reuses `discover_apt_csv_url()`'s own existing
  coverage (effective-cycle selection, fails-closed with no effective
  cycle) — no new test needed here beyond confirming the new wrapper
  delegates to the same logic.
- **Archive discovery** — cycle page missing the APT CSV link raises
  `RunwayEndsSourceError`-equivalent (or the new module's own error type,
  see below), matching existing coverage.
- **Temp-download behavior** — download writes to a temp path first, not
  directly to the final destination; confirmed by asserting the final path
  doesn't exist until after a successful run, and does exist untouched
  after an aborted one.
- **SHA-256 calculation** — computed hash of a known synthetic ZIP fixture
  matches a precomputed expected value.
- **Byte-size calculation** — matches `len()` of the synthetic fixture bytes.
- **ZIP validation** — a deliberately truncated/corrupted synthetic ZIP is
  rejected before any move into raw storage; raw storage directory
  contains nothing afterward.
- **Required-member validation** — a synthetic ZIP missing `APT_RWY_END.csv`
  (but containing the other two) is rejected; same for each of the other
  two required members individually.
- **Sidecar generation** — resulting JSON contains exactly the §7 schema's
  fields, correct types, `schema_version == 1`.
- **Idempotent same-hash rerun** — running acquisition twice against
  identical mocked content leaves the second run reporting "already
  preserved," with the archive/sidecar file mtimes and bytes unchanged
  from the first run.
- **Different-hash collision** — second run's mocked content differs from
  the first; asserts a collision error is raised, and that **neither**
  file changed from the first run's state (byte-for-byte comparison).
- **Sidecar/archive mismatch** — a synthetic sidecar with a deliberately
  wrong `sha256` sitting next to a valid archive triggers the integrity-
  failure path on a standalone verify call, with no network request made
  at all for that check.
- **Network failure** — mocked transport raises/returns 5xx for the index,
  cycle page, or archive fetch individually; each aborts cleanly with no
  partial file left in `data/raw/nasr/`.
- **Incomplete download** — mocked response body shorter than its own
  declared `Content-Length` (or a deliberately truncated byte stream)
  fails ZIP validation (§6 point 7), not silently accepted.
- **No DB writes** — every test asserts `session` is either never passed
  to any acquisition function at all (the strongest version of this
  guarantee, per §10's structural boundary) or, if a session fixture is
  present in a test for unrelated reasons, that it stays empty
  (`len(session.new) == 0`).

## 13. Future-cycle workflow

```
FAA index (www.faa.gov)
  -> discover latest effective cycle
FAA cycle page (www.faa.gov)
  -> discover the APT CSV archive URL (nfdc.faa.gov)
Download to temp file, validate, hash
  -> move into data/raw/nasr/<cycle>/ (immutable once written)
  -> write sidecar JSON (schema_version, hash, size, URLs, retrieved_at)
[SEPARATE, NOT YET DESIGNED, FUTURE STEP]
Parse the new cycle's APT_RWY.csv/APT_RWY_END.csv
  -> compare against the currently-ingested cycle (2026-08-06 today)
  -> propose canonical Runway/RunwayEnd changes (reusing
     app.services.runway_inventory's existing plan_airport_inventory()/
     classify_airport_batch() unchanged - same pattern as this session's
     clean-batch work, just against a newer cycle's rows)
  -> human review of the proposed diff
  -> approved, backed-up, all-or-nothing database apply
     (reusing apply_plan()/the clean-batch apply script's discipline unchanged)
```

Everything left of "[SEPARATE...]" is what this task designs (not yet
implemented). Everything right of it is explicitly **out of scope** for
this task, per instruction, and would need its own separate approval —
the acquisition module this task designs produces exactly the same shape
of input (a preserved archive + sidecar under `data/raw/nasr/<cycle>/`)
that `app/evidence/nasr_apt_rwy.py` and this session's clean-batch work
already consume for `2026-08-06` today, so no downstream code would need
to change to support a future cycle once acquisition exists.

## 14. Recommended implementation slice

The smallest slice that closes the gap without overbuilding:

1. `app/acquisition/nasr_apt_csv.py` — the three functions in §6, reusing
   `discover_apt_csv_url()`'s constants/regexes but returning the
   intermediate URLs too (cycle page URL, not just the final archive URL),
   since the sidecar needs both.
2. A CLI wrapper, `scripts/acquire_nasr_apt_csv.py`, mirroring this
   repository's existing dry-run/`--apply`-flag convention even though
   there's no database involved — `--apply` here means "actually write to
   `data/raw/nasr/`," default is a dry run that only reports what *would*
   be discovered/downloaded/preserved, matching the same
   confirm-before-you-write discipline used everywhere else in this
   project.
3. The test suite in §12, isolated to `tmp_path`, never touching real
   `data/raw/nasr/` or the network.
4. **Explicitly deferred**: DB provenance-model integration (§11), the
   comparison/diff/apply pipeline (§13's right-hand side), and any change
   to `app/acquisition/faa_runway_ends.py` itself (it stays exactly as-is;
   the new module only reuses its already-proven pieces, per §4).

---

## Explicit answers

**A. Is the FAA source chain live and verified?**
**Yes.** Every link in the chain was independently confirmed live today:
the index page still lists `2026-08-06` as "Current," that cycle's own
page's APT CSV link is byte-for-byte identical to the recorded
`final_archive_url`, the archive host is real and serving, and its live
byte size (`8034151`, via a 1-byte ranged request, not a full download)
matches the recorded/local value exactly. Content-hash identity was not
independently re-verified against the live remote bytes (would require a
full download, deliberately not performed).

**B. Is the existing discovery logic still valid?**
**Yes, unmodified.** `discover_apt_csv_url()` was executed live, right
now, with zero code changes, and returned exactly the recorded archive
URL.

**C. Can RWI automatically discover future NASR cycles?**
**Yes**, using the exact same unmodified discovery code — it's already
cycle-agnostic (always resolves "latest effective cycle as of today," not
a hardcoded date).

**D. What code is missing to preserve them reproducibly?**
A ~150-line acquisition-and-preserve module (§6) plus its CLI wrapper and
tests (§14) — download-to-temp, hash, validate ZIP structure and required
members, atomic move into `data/raw/nasr/<cycle>/`, write the sidecar
(§7). None of this exists anywhere in the repository today.

**E. Should sidecar JSON or DB provenance be used first?**
**Sidecar JSON** — it's the already-proven, already-consumed convention;
the DB-level `AcquisitionRun`/`Snapshot` model is real but currently
unused by anything (zero rows for any source) and shouldn't be the first
thing this gets coupled to. See §11.

**F. What is the smallest safe implementation slice?**
The single new module + CLI wrapper + isolated test suite in §14 —
nothing else. No database model change, no comparison/ingestion pipeline,
no change to any existing acquisition code.

---

No database modification. No acquisition implementation. No commit. No
push. No deployment.
