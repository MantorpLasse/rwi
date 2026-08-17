# FAA NASR APT CSV Acquisition-and-Preserve — Implementation Slice

Implements exactly the slice scoped in
`docs/domain/nasr-acquisition-preserve-design.md`: discover → download to
temp → validate → hash → preserve immutable raw ZIP → write sidecar JSON.
**No ingestion, no reconciliation, no database write, no public export
work, and no real acquisition were performed in this task** — every test
uses `httpx.MockTransport`; the one live network use was a discovery-only
dry run (two small HTML page fetches, no archive download), reported in
§16 below.

## 1. Files changed

| File | Change |
|---|---|
| `app/acquisition/faa_runway_ends.py` | Minimal refactor — see §2 |
| `app/acquisition/nasr_apt_csv.py` | **New** — the acquisition module |
| `scripts/acquire_nasr_apt_csv.py` | **New** — CLI wrapper |
| `tests/test_nasr_apt_csv_acquisition.py` | **New** — 31 tests |

Nothing else was touched. `app/evidence/nasr_apt_rwy.py`,
`app/evidence/nasr_apt_ars.py`, and every existing script/test that reads
the already-preserved `2026-08-06` archive are unmodified.

## 2. Reuse of existing discovery code

`discover_apt_csv_url()` in `app/acquisition/faa_runway_ends.py` inlined
two pieces of logic — cycle selection and archive-link extraction — that
the new module also needs, but it only ever returned the final URL,
discarding the cycle string and cycle-page URL along the way. The minimal
refactor: those two pieces were extracted into
`_select_effective_cycle(index_html, today)` and
`_select_apt_csv_url(cycle_html, cycle)`, and `discover_apt_csv_url()` was
rewritten to call them — **byte-for-byte identical behavior**: same
requests in the same order, same exceptions with the same messages. All
16 pre-existing tests in `tests/test_faa_runway_ends.py` pass unchanged,
proving the refactor didn't alter behavior.
`app/acquisition/nasr_apt_csv.py::discover_nasr_apt_archive()` then reuses
those two functions directly, adding only the orchestration needed to
also capture `cycle_page_url` and `nasr_cycle` — nothing about the actual
selection/extraction logic was duplicated or forked.

## 3. Acquisition API

`app/acquisition/nasr_apt_csv.py`:

- `discover_nasr_apt_archive(*, client, today, timeout=30.0) -> NasrArchiveLocation` — read-only; `nasr_cycle`, `source_index_url`, `cycle_page_url`, `final_archive_url`.
- `acquire_and_preserve_nasr_apt(*, client, today=None, raw_dir=DEFAULT_RAW_DIR, timeout=60.0) -> NasrAcquisitionResult` — the full flow; `status` is `"preserved"` or `"already_preserved"`.
- `verify_preserved_artifact(archive_path, sidecar_path) -> dict` — standalone, no-network integrity re-check; returns the parsed sidecar on success, raises on any mismatch.
- `NasrAcquisitionError(ValueError)` — the module's single error type. Deliberately **not** `RunwayEndsSourceError` — this module's failures are about acquiring/preserving raw bytes, not EMAS-specific parsing, and keeping the types separate keeps the acquisition/parsing boundary legible to callers.

## 4. HTTP / User-Agent behavior

`USER_AGENT = "Mozilla/5.0 (compatible; RunwaySafeIntelligence/NASRAcquisition; +https://github.com/MantorpLasse/rwi)"`
— a Mozilla-compatible prefix plus explicit RWI identification, per the
governing design and the live-verification finding that FAA's edge
rejected an unidentified/tooling-shaped signature. **Observation, not a
change made in this task:** the existing `scripts/import_faa_runway_ends.py`
sets a different, bare `"RunwaySafeIntelligence/1.0"` (no Mozilla-compatible
prefix) for the same `discover_apt_csv_url()` call. That script's header
was never verified live in either provenance task and is out of scope
here — flagged for awareness, not modified.

Failure handling:
- **Timeout** — `httpx.HTTPError` (covers `httpx.TimeoutException`) is caught and re-raised as `NasrAcquisitionError` at both the discovery and download layers.
- **Non-2xx** — `response.raise_for_status()` on the resolved (non-redirect) response; wrapped the same way.
- **Interrupted/incomplete transfer** — a transport error raised mid-`iter_bytes()` is caught and wrapped; the temp file is discarded in the `finally` clause either way.
- **Redirect validation** — every redirect hop is resolved manually (`follow_redirects=False` on every request), each `Location` header validated against the host allowlist *before* being followed; a redirect with no `Location` header, or exceeding `MAX_REDIRECTS = 5`, raises.
- **No unbounded retry** — none of this repository's existing acquisition code retries automatically (confirmed by reading `faa_runway_ends.py`, `faa.py`, `faa_aip_grants.py`), so this module matches that convention: one attempt, fail closed, let the caller decide whether to retry.

## 5. Host allowlist

`ALLOWED_HOSTS = frozenset({"www.faa.gov", "nfdc.faa.gov"})`. Every URL
this module is about to request — the hardcoded index URL, the
cycle-page URL built from a discovered cycle string, the discovered
archive URL, and every redirect hop — passes through
`_validate_official_url()`, which requires `scheme == "https"` and
`hostname in ALLOWED_HOSTS`, raising `NasrAcquisitionError` otherwise.
This is enforced even though `_APT_CSV_HREF`'s regex already hardcodes
`nfdc\.faa\.gov` structurally (so a non-`nfdc.faa.gov` link literally
cannot match) — the allowlist check is explicit defense-in-depth that
doesn't depend on that regex never changing.

## 6. Temp-download flow

Exactly the 9-step sequence from the design, implemented in
`_download_to_temp()` + `acquire_and_preserve_nasr_apt()`:

1. Resolve target cycle/path (via discovery).
2. `tempfile.mkstemp()` inside the **destination cycle directory itself**
   (not a system temp dir) — the later "preserve" step is then a same-filesystem
   `Path.replace()`, i.e. an atomic rename, not a cross-device copy.
3. Stream the full response body into that file via `client.send(..., stream=True)` + `iter_bytes()` — never buffers the whole ~8 MB in memory.
4. File is closed (the `with temp_path.open("wb")` block exits) before anything else touches it.
5–6. Byte size (`Path.stat().st_size`) and SHA-256 (streamed, 1 MB chunks) are computed from the file **on disk**, not from the in-flight response — matching this repository's existing verification convention.
7–8. ZIP structure and required-member validation (§7).
9. Only after all of the above succeeds: `temp_path.replace(final_archive_path)`.

Every failure path (network error, non-2xx, interrupted transfer, corrupt
ZIP, missing member, provenance collision) reaches the same `finally:
temp_path.unlink(missing_ok=True)` in `acquire_and_preserve_nasr_apt()` —
proven directly by `test_network_failure_leaves_no_final_artifact` and
`test_interrupted_corrupt_download_leaves_no_final_artifact`, both of
which assert the destination directory contains **zero** files (not just
"the final files are absent" — genuinely empty, no stray `.partial`
either) after a failure.

## 7. ZIP validation

`_validate_zip()`:
- Opens the temp file with `zipfile.ZipFile`; a non-ZIP or truncated file raises `zipfile.BadZipFile`, caught and re-raised as `NasrAcquisitionError`.
- Calls `ZipFile.testzip()` — returns the name of the first corrupt member, if any; raises if so.
- Confirms all three required members are present via case-insensitive suffix match (`name.upper().endswith(member)`), matching the exact convention already used in `app/evidence/nasr_apt_rwy.py`/`app/evidence/nasr_apt_ars.py`. Missing any one — `APT_ARS.csv`, `APT_RWY.csv`, or `APT_RWY_END.csv` — raises, naming which.

This module **never opens or reads CSV content** — only ZIP structure and
member names. Ingestion/parsing stays entirely in the existing
`app/evidence/*` readers, untouched.

## 8. Sidecar schema

Exactly the design's schema, `schema_version: 1`:

```json
{
  "schema_version": 1,
  "publisher": "Federal Aviation Administration",
  "dataset": "NASR 28-Day Subscription — Airport (APT) CSV",
  "source_index_url": "...",
  "cycle_page_url": "...",
  "final_archive_url": "...",
  "nasr_cycle": "YYYY-MM-DD",
  "retrieved_at": "YYYY-MM-DDTHH:MM:SS.ffffffZ",
  "sha256": "<64 hex chars>",
  "byte_size": 0,
  "local_archive_filename": "...",
  "acquisition_method": "app.acquisition.nasr_apt_csv.acquire_and_preserve_nasr_apt"
}
```

`retrieved_at` uses `datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"`
— matches the existing `2026-08-06` sidecar's exact style
(`"2026-08-16T06:51:07.9581445Z"`), verified by
`test_retrieved_at_is_utc_with_explicit_z_suffix` round-tripping through
`datetime.strptime(..., "%Y-%m-%dT%H:%M:%S.%fZ")`. No acquisition-actor
field exists anywhere in the schema, per instruction — the mechanism's
output doesn't depend on who invoked it.

## 9. Immutability / collision rules

All six rules from the design, each with a passing test:

| Rule | Test |
|---|---|
| Neither exists → preserve normally | `test_first_preservation_succeeds_and_writes_expected_files` |
| Both exist, same hash → idempotent no-op, neither file rewritten | `test_second_acquisition_with_same_hash_is_idempotent` (asserts byte-identical file contents before/after, not just equal hashes) |
| Archive exists, different hash → **STOP**, collision, neither file touched | `test_acquisition_with_different_hash_raises_collision_and_preserves_original` |
| Sidecar exists without archive → **STOP**, integrity error | `test_sidecar_without_archive_is_rejected` |
| Archive exists without sidecar → **STOP**, integrity error | `test_archive_without_sidecar_is_rejected` |
| Existing sidecar hash/size disagrees with the archive next to it | `test_mismatched_sidecar_hash_is_rejected`, `test_mismatched_sidecar_size_is_rejected` (both via the standalone `verify_preserved_artifact()`, no network needed) |

Every one of these branches is checked **before** any temp download
begins (the existing-pair consistency check happens first) or, for the
hash-collision case, before any file at the final path is touched (the
new download goes to a temp file first; only its hash is compared).

## 10. Atomic preservation behavior

`Path.replace()` (POSIX `rename()`/Windows `ReplaceFile`-backed) is used
for both the archive and the sidecar, always within the same directory
the temp file was created in — no cross-filesystem copy anywhere. The one
**unavoidable** tiny window, stated plainly per instruction: between the
archive rename and the sidecar rename, a crash would leave an archive
present with no sidecar. This is not silently dangerous — it's exactly
the "archive exists without sidecar" state (§9), which the very next run
detects and refuses to proceed past, rather than silently trusting or
silently overwriting either file. True single-operation atomicity across
two separate files isn't achievable without a heavier mechanism (e.g. a
lockfile or a single combined archive+manifest container), which is more
machinery than this slice's stated scope calls for.

## 11. CLI behavior

`scripts/acquire_nasr_apt_csv.py`, mirroring this repository's existing
dry-run/explicit-flag convention:

- **Default (no flags): discovery-only dry run.** Prints source index,
  resolved cycle, cycle page URL, final archive URL, intended destination
  directory/archive path/sidecar path, and whether that cycle is already
  preserved locally. Makes exactly two small HTML `GET` requests (index +
  cycle page); no archive download, no file write, no DB access.
- **`--acquire`: real download-and-preserve.** Required explicitly; there
  is no way to trigger a real download without it.
- **`--raw-dir`**: overridable destination root, defaulting to `data/raw/nasr`.
- Both `dry_run()`/`acquire()` accept an optional `client` for test
  injection, matching `scripts/import_faa_runway_ends.py::run()`'s exact
  existing convention (`client: httpx.Client | None = None`, only closed
  if the function itself constructed it).
- **No DB access anywhere in the CLI** — it imports nothing from
  `app.database`/`app.models`/`SessionLocal`.

## 12. Compatibility with the existing `2026-08-06` artifact

`verify_preserved_artifact()` was run, read-only, directly against the
real files:
`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip` and its
`.metadata.json` — **succeeded**, correctly parsing the sidecar's
UTF-8 BOM (via `encoding="utf-8-sig"`, matching every other reader in this
repository) and confirming `sha256`/`byte_size` both match the real file's
actual bytes. The existing sidecar has no `schema_version` field (it
predates this slice) — this was **not** backfilled or mutated; the
verifier only reads the fields it needs (`sha256`, `byte_size`) and
doesn't require `schema_version` to be present, so old and new sidecars
both parse cleanly. `tests/test_nasr_apt_csv_acquisition.py::test_verify_preserved_artifact_understands_the_real_existing_sidecar`
additionally asserts the real archive/sidecar bytes are unchanged
before and after the test, proving the check is genuinely read-only.

## 13. Acquisition/ingestion separation

Structural, not just promised: **no function in `app/acquisition/nasr_apt_csv.py`
accepts a database session parameter, and the module has no import of
`app.database`, `SessionLocal`, or `sqlalchemy` anywhere** — confirmed by
`test_module_has_no_database_imports` (parses the module's AST and asserts
none of those names appear in any import statement) and
`test_full_acquisition_makes_no_database_writes` (runs a full mocked
acquisition alongside an open isolated session and asserts it stays
empty). This mirrors the existing `plan_*`/`apply_*` separation already
established in `app/services/runway_inventory.py`.

## 14. Tests and results

- **New tests**: `tests/test_nasr_apt_csv_acquisition.py` — **31 passed**, covering every scenario in the task's list (host allowlist accept/reject, redirect rejection, User-Agent, temp-download bytes, SHA-256, byte-size, valid/corrupt/incomplete ZIP, each of the three required members individually, sidecar content/UTC timestamp, first preservation, same-hash idempotency, different-hash collision, archive-without-sidecar, sidecar-without-archive, mismatched hash/size, network failure, interrupted download, CLI dry-run-only default, explicit `--acquire` requirement, no-DB-dependency, and real-artifact sidecar compatibility).
- **Existing discovery/acquisition tests**: `tests/test_faa_runway_ends.py` (16) + `tests/test_faa_acquisition.py` — all pass unchanged, confirming the refactor in §2 didn't alter behavior.
- **Focused canonical-runway tests** (11 files): **94 passed**.
- **Full suite**: **470 passed** (439 before this task + 31 new).
- **Python compilation**: `app/acquisition/nasr_apt_csv.py`, `app/acquisition/faa_runway_ends.py`, `scripts/acquire_nasr_apt_csv.py`, the new test file — all clean.
- **`git diff --check`**: exit 0 (only the pre-existing benign LF→CRLF notice).
- **Real preserved archive**: byte-identical mtime/size before and after this entire task (`8034151` bytes, sidecar `538` bytes, both mtimes unchanged).
- **Real development DB**: byte-identical size/mtime before and after (`651264` bytes) — this module has no code path capable of touching it.

## 15. Remaining risks

- **`import_faa_runway_ends.py`'s bare User-Agent is unverified live** (§4) — not this task's concern, but worth a future check before relying on that script for a real run.
- **The tiny archive-then-sidecar rename window (§10)** is a real, if narrow, crash-window; acceptable for this slice's scope, documented rather than engineered away.
- **No automatic retry** — a transient FAA/NFDC hiccup fails the whole attempt; matches existing repository convention but means a real future acquisition run might need a manual re-invocation (which is safe and idempotent by design — see §9's same-hash rule).
- **This module was never run against the live archive end-to-end** — only discovery was exercised live (§16); the download/hash/validate/preserve path has only been proven against mocked transports. The design and this task both deliberately withheld a real download pending explicit approval.

## 16. Exact command for a future real acquisition

```
.venv\Scripts\python.exe -m scripts.acquire_nasr_apt_csv --acquire
```

Default `--raw-dir` is `data/raw/nasr`, matching the existing convention;
override with `--raw-dir <path>` if ever needed. Running today, this
command would resolve cycle `2026-08-06` again (still the live "Current"
cycle as of this task) and — since that cycle is already preserved
locally with a matching hash — would hit the `"already_preserved"` branch
and write nothing, per §9's idempotency rule.

---

No database modification. No commit. No push. No deployment.
