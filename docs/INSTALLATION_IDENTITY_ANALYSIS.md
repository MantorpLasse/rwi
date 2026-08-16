# RWI installation identity and lifecycle analysis

**Date:** 2026-08-16  
**Scope:** read-only inspection of models, scripts, CSV artifacts, sources, and `data/runway_safe.db`. No code, schema, data, migration, UI, route, or script was changed; no write-capable script was run.

## Executive summary

The current `Installation` table does not have one consistent real-world meaning. It is described as “what is installed today,” but the 149 rows combine (a) airport/type-level FAA Tableau assertions, (b) dated FAA fact-sheet assertions about systems and replacement history, and (c) manually researched physical/project assertions. The existing model can *store* multiple rows at an airport or runway, but its base importer cannot preserve multiple same-type systems at one airport.

The strongest conclusion is semantic rather than structural: **one Installation should mean one asserted physical EMAS system at a particular airport, at a runway end when that evidence is known.** It must not mean “all EMAS of this type at an airport.” When evidence only proves an airport has EMAS, that evidence should be retained as an airport-level inventory assertion, not inflated into an invented physical-system identity.

The existing FAA CSV cannot solve this alone. It has one row per airport, no duplicate airport/type rows, and no runway/end, system count, installation year, vendor, source-record ID, or system locator. Its import key `(airport_id, type)` accurately reflects that coarse artifact but is incompatible with system-level inventory identity.

## 1. Current installation inventory

### Counts

| Measure | Current count |
|---|---:|
| Total Installation rows | 149 |
| Airports with one or more Installation | 86 |
| Airports with multiple Installation rows | 66 |
| EMASMAX / greenEMAS / generic EMAS | 137 / 10 / 2 |
| Rows with a Runway FK | 52 |
| Rows with a runway-end value | 32 |
| Rows without a Runway FK | 97 |
| Rows with install year / without install year | 80 / 69 |
| Rows with replacement year | 0 |
| Rows with confirmed vendor | 14 |
| Rows with a source ID | 149 |
| Rows with recorded ambiguous/multiple-end NASR note | 23 |
| Rows with status `active` | 149 |

**FACT:** No Installation has a non-null `replacement_year`; all have `status='active'`. These fields do not currently represent lifecycle history.

### Installations per airport

**FACT:** 20 airports have a single Installation row; 63 have two; Chicago Midway, Chicago O’Hare, and Cuyahoga each have three. The model allows arbitrarily many rows per Airport.

**OBSERVATION:** Multiple rows do not automatically mean multiple independently identified physical systems. At many US airports, one row is a current airport-level FAA Tableau assertion with no year and one is a dated FAA-fact-sheet assertion. They could be corroborating evidence, distinct systems, replacement history, or a mixture; source data must decide.

### Focus examples

| Airport | Rows / evidence | What can and cannot be concluded |
|---|---|---|
| JFK | FAA Tableau EMASMAX row, no year, linked runway but no end; FAA fact-sheet EMASMAX 1996 row, no runway/end. | FAA fact-sheet script notes two systems (1996 with replacement 1999; 2007 with replacement 2014) in prose, but current DB has only one dated row and one aggregate row. The two DB rows cannot be safely mapped to JFK’s two physical systems. |
| BOS | FAA Tableau aggregate EMASMAX row; fact-sheet 2005 EMASMAX row; Signal evidence for current work. | Existing Installation data does not identify a runway/end. The signal/project record must not be treated as a distinct operational Installation without completion evidence. |
| Cuyahoga / CGF | FAA Tableau aggregate row linked to a runway but no end; two 2018 manual/news rows with ends `06` and `24`. | The two dated rows are strong evidence of distinct physical end-level systems, not a duplicate merely because type/year match. Their `runway_id` is currently null, so exact runway linkage is not represented. |
| Midway / MDW | Generic FAA greenEMAS aggregate; 2014 greenEMAS with end `22L`, vendor Runway Safe; 2006 fact-sheet EMASMAX. | The rows explicitly mix a generic inventory assertion, a later specific greenEMAS claim, and historical fact-sheet claim. They should not be mechanically deduplicated. |
| O’Hare / ORD | FAA EMASMAX aggregate, fact-sheet 2008 EMASMAX, and a greenEMAS researched assertion with unknown year. | Multiple product/history claims exist; exact system correspondence remains unproven. |

### Suspicious duplicates

**FACT:** The sole exact `(airport, type, install_year)` duplicate-looking group is Cuyahoga: two EMASMAX 2018 rows. Their documented ends differ (`06`, `24`) and source is the same completion announcement. **Conclusion:** this is evidence of two systems, not evidence of a duplicate.

**OBSERVATION:** There are many same-airport/same-type rows where one has `install_year=NULL` (FAA aggregate) and another has a date (fact sheet/manual). These are suspicious *identity overlaps*, not proven duplicates. No row-level source locator makes automated merging defensible.

## 2. Origin and meaning of current rows

### Provenance totals

| Source type | Installation rows | Typical current row meaning |
|---|---:|---|
| `faa_tableau` | 69 | Airport/type-level current-inventory assertion imported from manual CSV. |
| `faa_fact_sheet` | 61 | Dated fact-sheet assertion; scripts contain system-count/replacement context in notes. |
| `news` | 19 | Curated airport/vendor/news/manual research assertion; often more specific, but granularity varies. |

### Representative origins

- **FAA Tableau (source 12):** `import_faa_csv.py` creates/fetches one Installation by airport + type and stores `status='active'` and a map-region note. It represents “FAA map says EMASMAX/greenEMAS is at this airport,” not a physical bed.
- **FAA fact sheets (sources 56/57/68):** `import_faa_fact_sheets_2011_2016.py` and `..._resten.py` create/find by airport + type + install year. Their hard-coded research mappings sometimes split years/systems and document replacements/retrofits in notes. They carry useful historical system knowledge, but runway/end identity is generally missing.
- **NASR APT_ARS:** `import_faa_runway_ends.py` does not create Installation rows. It adds/links Runway and `runway_end` evidence to existing EMASMAX/greenEMAS rows. It can resolve one end, identify a runway but leave end unknown, or record different-runway ambiguity in notes.
- **Manual/international research:** Brazil, Gadelius, Runway Safe shareholder material, PHL/PDK/Boca/Cuyahoga and other specific scripts create or enrich rows. They may provide a year, end, dimensions or confirmed vendor, but not always a runway FK/system ID.

### What row types currently represent

| Conceptual category | Evidence/example |
|---|---|
| Airport-level assertion | 69 FAA Tableau rows, each coming from a CSV with exactly one row per airport. |
| End-level physical-system assertion | Cuyahoga 2018 end 06 and end 24 rows; each has an explicit end. |
| Runway-level but end-unknown assertion | FAA/NASR-linked rows such as JFK aggregate row: runway known, end null. |
| Dated historical/system assertion | FAA fact-sheet rows, e.g. JFK 1996, ORD 2008, BOS 2005; notes often contain replacement history. |
| Specific researched installation/project assertion | MDW 2014 greenEMAS, PHL 2025, PDK 2018, international Gadelius/Runway Safe rows. |
| Proven duplicate | None found. The available evidence does not prove a duplicate Installation row. |

## 3. FAA Tableau granularity

### The checked-in files

`emas_airports_usa.csv` has **70 rows**, one distinct `ARPT_ID` per row, and fields:

```text
ARPT_ID
ATTR(ARPT_NAME)
ATTR(CITY)
ATTR(STATE)
TYPE
Latitud (genererad)
Longitud (genererad)
MAP_REGION
```

It has 70 unique `(ARPT_ID, TYPE)` pairs and only two type values. `emas_incidents_usa.csv` has 19 airport-level incident summary rows, not installation-system rows.

| Candidate distinguishing field | In airport CSV? | Current use |
|---|---|---|
| Airport identifier/name/city/state | Yes | Airport matching/creation. |
| EMAS type | Yes | Installation type. |
| Airport coordinates | Yes | Airport latitude/longitude. |
| Map region | Yes | Installation note. |
| Runway | No | Cannot be imported. |
| Runway end | No | Cannot be imported. |
| System count | No | Cannot be imported. |
| Installation year | No | Cannot be imported. |
| Manufacturer/vendor | No | Cannot be imported. |
| Stable installation/system/source-row ID | No | Cannot be imported. |

**FACT:** There are no unused CSV columns containing the requested system-level data. No script can restore fields that were never captured in this artifact.

### Current importer semantics

`get_or_create_installation()` in `import_faa_csv.py` searches only:

```text
Installation.airport_id == airport.id
Installation.type == installation_type
```

**FACT:** A second FAA EMASMAX system at the same airport will update/reuse the same row. This importer is safe only for its actual airport/type-level input assertion; it is not safe as a physical-system baseline importer.

## 4. Multiple installations at one airport

### Evidence already present

**FACT:** The database schema permits multiple rows on an Airport and multiple rows on one Runway; no unique constraints block either. In current data, 66 airports have multiple Installation rows. No single linked Runway currently has more than one Installation, so per-runway multiplicity is permitted but not represented in the live links.

**FACT:** FAA fact-sheet scripts contain richer *prose* about multiple systems than current rows encode. Examples include JFK two systems with replacement dates, Fort Lauderdale growth from two to four, LaGuardia multiple/expanded systems, Boston multiple systems and later construction, and Cuyahoga explicit ends. This knowledge is an important migration input, but prose and source documents must be re-read before creating physical identities.

**Never-guess conclusion:** A count in a fact sheet proves multiple systems existed/are described; it does not by itself assign each system to a current DB row, runway, or end. An airport-level Tableau row must not be split into physical rows from count alone.

## 5. Runway and runway-end semantics

The existing model supports the following evidence states:

| Evidence state | Current representation | Meaning |
|---|---|---|
| Airport proves EMAS exists | `airport_id`; `runway_id=NULL`; `runway_end=NULL` | Airport-level assertion, not missing data to be guessed. |
| Runway known, end unknown | `runway_id` set; end null | A physical runway association is supported; no end claim is made. |
| Runway/end known | `runway_id` + `runway_end` | A system can be located at a specific end. |
| Several ends on same runway | One row may link runway but leave end null and state ambiguity in notes | Correctly avoids choosing an end, but cannot represent two separately identified end systems without two rows. |
| Several candidate runways | no runway link; note lists candidates | Correctly preserves uncertainty, but no structured candidate relationship exists. |

**OBSERVATION:** `runway_end` is a free text field, not a runway-end entity or FK. The table has no rule that end belongs to linked runway. Cuyahoga has `runway_end=06/24` but no Runway FK; its physical identity is clearer in text than in relational linkage.

**Necessary domain semantics:** a physical EMAS system belongs at an airport; it may be located at one runway end. An airport may have several systems, including systems on different runways and, potentially, both ends of one runway. “Known end” must be optional evidence, not a requirement. One installation must never be presumed to cover all systems at the airport.

## 6. Lifecycle analysis

### Current behavior

- `Installation.install_year`: optional year, populated on 80 rows; it represents various source-derived installation/history claims.
- `Installation.replacement_year`: available but unused (0 rows).
- `Installation.status`: available but all 149 values are `active`.
- `Installation.notes`: public free text carrying source details, historical/replacement discussion, and some ambiguity.
- `Signal.category`: project kind (`new_installation`, replacement, maintenance, etc.).
- `Signal.status`: lifecycle-ish free text (`identified`, design, funded, procurement, under construction, completed, etc.).
- Graduation command: manually creates an active Installation from a completed Signal, copies airport/runway/source/vendor/notes, and links the Signal to the new row.

**FACT:** Current Installation rows cannot clearly distinguish planned, procurement, construction, operational, replacement, retired, or historical states. Signals provide most non-operational project lifecycle expression.

**OBSERVATION:** The distinction is required semantically. `new_installation` is a project category; `under construction` and `operational` are lifecycle stages. The current Signal model already demonstrates this distinction conceptually, although its stored status vocabulary is free-text. Installation status needs a different lifecycle vocabulary from Signal project stages if it is meant to represent actual systems.

## 7. Candidate identity definitions

### Candidate A — airport + runway + runway end + type

**Represents:** one physical system at a known end.  
**Handles well:** exact physical location, multiple systems on one airport/different runways, two ends on a runway.  
**Cannot handle:** unknown runway/end, multiple systems at the same end, renamed runways without identity history, and source evidence that only gives a count.  
**Evidence needed:** reliable runway/end evidence.  
**History/replacements:** requires a temporal key or separate lifecycle/history rule; otherwise a replacement collides with the original.  
**Assessment:** too strict as the universal identity because much current evidence lacks end data.

### Candidate B — airport + stable physical-system identity

**Represents:** one real EMAS system/bed, with runway/end as optional attributes.  
**Handles well:** incomplete physical location, runway renaming, source corroboration, multiple systems at the same airport/end, and historical replacements if lifecycle/history is explicit.  
**Cannot handle:** source-only airport aggregate assertions without a way to identify one system.  
**Evidence needed:** source-specific system ID, clearly distinct source locator, or a human-reviewed physical identity decision.  
**Assessment:** best semantic target, but it requires acquisition/provenance improvements.

### Candidate C — airport + source-specific installation assertion

**Represents:** what a particular source says, not necessarily a physical system.  
**Handles well:** airport-level FAA inventory, raw ingestion, multiple sources, and uncertainty.  
**Cannot handle:** a clean public system inventory without a later reconciliation layer.  
**Evidence needed:** source ID plus source row/locator.  
**Assessment:** correct for raw baseline evidence, but should not be called a physical Installation if visitors expect a system.

### Recommended semantic definition

**Recommendation:** Define **Installation** as one *physical EMAS system* (a distinct installed arresting system/bed) at an Airport. It may be linked to a Runway and Runway End only when supported by evidence. Multiple Installation rows may exist at an Airport and on a Runway. A replacement is a new physical system or a lifecycle event linked to its predecessor; it must not overwrite/erase the prior system. Retired/historical records remain identifiable as such.

When evidence only proves “EMAS exists at airport,” retain it as a source-backed **airport-level installation inventory assertion** until it can be reconciled to one or more physical systems. Do not create arbitrary physical Installation rows or invent ends from a count.

## 8. Conceptual classification of the existing 149 rows

This is deliberately conservative. The categories are about evidence semantics, not a proposed data migration, and several facts overlap.

| Classification | Count / confidence | Explanation/examples |
|---|---:|---|
| Airport-level inventory assertion | **69 confident** | All FAA Tableau-source rows: one airport/type CSV row, no system locator/runway/end/year. |
| Dated historical/system assertion | **61 confident as dated assertions** | FAA fact-sheet rows. Many likely describe distinct systems or replacements, but the present row does not always identify a physical system/end. |
| Curated/researched installation assertion | **19 confident as source category** | News/manual rows. Some are clearly physical/end-level (Cuyahoga); others are airport/project-level. |
| Distinct physical systems proven at row level | **at least 2** | Cuyahoga 2018 end 06 and 24. Other rows may be physical systems but lack equally explicit discriminators. |
| Proven historical/retired records | **0** | Notes describe retrofits/replacements, but no row is marked retired/historical and `replacement_year` is unused. |
| Proven duplicates | **0** | No source evidence supports calling any row duplicate. |
| Ambiguous/requires reconciliation to physical system | **at least 147** | Every row other than the two explicit Cuyahoga end rows lacks enough row-level identity evidence to establish a unique physical system with confidence. This does not say the data is wrong; it says its physical-system mapping is not proven. |

**Important:** The 69/61/19 source categories add to 149 and are the safest exhaustive classification. The physical-system/historical categories cannot be made mutually exclusive without inventing facts.

## 9. Future FAA baseline acquisition requirements

### What exists now

- Airport identity, airport coordinates, type, and map region from a 70-row manual CSV.
- A separate airport-level incident CSV.
- Later NASR APT_ARS runway/end hints, not source-linked in the DB.
- Selected fact-sheet/manual enrichment in free-text and targeted rows.

### What a system-level baseline importer must preserve

1. Raw source artifact and retrieval timestamp/hash.
2. Source record/mark identifier and a stable source locator (workbook/view/sheet/row or equivalent).
3. Exact raw source values, including airport code/name, product/type, system count and any installation date/vendor fields.
4. Whether a record is an airport aggregate or an individual system.
5. Direct runway/end evidence and source of that evidence; no inferred assignment treated as authoritative.
6. Parsed/canonical airport/runway/end values alongside raw values.
7. A repeatable idempotency key based on upstream record identity, not airport/type.
8. A source-to-physical-system reconciliation record when multiple sources describe one system.
9. Lifecycle/historical evidence: installation/replacement/removal dates and relationship to predecessor where supported.

## 10. Recommended domain semantics and next architecture step

### Answers to the required design questions

1. **Installation meaning:** one distinct physical EMAS system/bed, not an airport aggregate or source assertion.
2. **Multiple rows per airport:** yes; required and expected.
3. **Multiple systems per runway:** yes; allowed when evidence supports them, including both ends.
4. **Runway-end specificity:** optional but structured evidence; absence means unknown, never absent.
5. **Historical/replaced systems:** preserve history. A replacement should be a distinct system/event with an evidence-backed predecessor relation, not a mutation that loses the old record.
6. **Lifecycle:** keep project category separate from lifecycle. Signal tracks opportunity/project lifecycle; Installation lifecycle should distinguish operational, replaced, retired/removed, and unknown when supported.
7. **Airport-only evidence:** store/reconcile it as an airport-level source assertion; do not make up a system count, end, or physical row.
8. **Two sources, same physical system:** retain both source assertions and reconcile them to one system only with enough evidence; source count is not system count.
9. **Minimum provenance:** source, exact URL/artifact, retrieved date, source locator/record ID, raw identifying values, and confidence/interpretation boundary.
10. **FAA importer output:** source-record-level assertions keyed by FAA record identity, plus physical-system records only where the source supplies enough identity or a separately reviewed reconciliation supports it.

### Recommended next implementation step

Before a migration or importer rewrite, produce and approve a small evidence/identity specification: define (1) physical Installation, (2) airport-level inventory assertion, (3) source-record identity, and (4) evidence rules for reconciliation and replacement history. Then perform a separate, approved mapping exercise for the 149 rows—starting with airports that have multiple documented systems—without guessing from airport-level CSV rows.
