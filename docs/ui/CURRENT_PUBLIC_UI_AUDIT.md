# Current Public UI Audit

## Scope

Read-only audit of the static export and generated `site/`. No site, data, or
template was regenerated or changed. Findings describe the current Swedish
public product, not the internal FastAPI development views.

## 1. Current site map

| Page | Purpose and major content |
|---|---|
| `/index.html` | Dashboard: purpose line, four statistics, recent updates, installation/signal trend charts, and prioritised signals. |
| `/signals/index.html` | Signal/project discovery list with search, status/country filters, watched-only filter, grouped rows, and signal links. |
| `/signals/{id}.html` | One signal’s project/source presentation. |
| `/airports/index.html` | Searchable airport list with airport-code links. |
| `/airports/{id}.html` | Airport header, timeline, signals, legacy installations, incidents, runway sidebar, airport website link. |
| `/ordlista.html` | Glossary for source types and terms. |
| `/om.html` | About/disclaimer. |
| `/data.json` | Public structured airport and signal export, not a visitor-facing page. |

There is no generated public map page or Leaflet implementation in the static
export. The current marker semantics therefore cannot be audited; map work is
future work, not a current regression.

## 2. User journey and information architecture

Primary navigation is consistent: Overview, Signals, Airports, Glossary. About
is secondary, in the footer. Dashboard rows link into signals or airport detail;
signal rows link to signal detail and airport detail; airport lists link to
airport detail; installation/incident timeline links are same-page anchors.

Strengths: the main paths are short and code chips make airport links readable.
Potential context loss: airport detail offers both a real “Airports” breadcrumb
and a JavaScript “Back” button that relies on browser history; direct arrivals
may get a browser-history result unrelated to the product. Signal detail has no
visible evidence from this audit of a persistent return-to-context pattern.
Source links open externally where a public URL exists.

## 3. Visual system

The site follows the approved aviation-oriented dark system: Space Grotesk
headings, IBM Plex Sans body copy, IBM Plex Mono for codes/numbers; navy panels,
hairline borders, warm yellow airport-code signs, and green/amber/pink/slate
semantic accents. It uses cards, compact tables, category badges, three-bar
confidence gauges, status pills, vendor pills, timelines, and SVG trend charts.
Hover affordances exist for table rows, links, sign chips, source pills, charts,
and watch controls.

Semantics are mostly centralized: category and source-type display mappings live
in `build.py`, and confidence values bucket into one gauge. A weakness remains:
unmapped legacy source types fall back to raw, unlinked labels, so identical
source concepts can look more polished on one page than another.

## 4. Page-by-page hierarchy

- **Dashboard:** title/subtitle, then four statistics, then recent updates,
  trend, and prioritized signals. It reads as a research dashboard, but dense
  tables and several equal-weight cards compete for first attention.
- **Signals:** filters establish task intent; grouped table rows make it useful
  as an intelligence list. Project title and airport are primary; category,
  status, confidence, year, and score compete as dense metadata.
- **Airport list:** search-first table is scannable, but does not visibly frame
  why an airport is relevant before clicking.
- **Airport detail:** airport identity is strong. Timeline, signals, legacy
  installations, and incidents are stacked in the main column; runways sit in
  a sidebar. The many sections establish breadth, but a visitor must infer the
  difference between current fact, historical installation, and project.
- **Glossary/about:** supporting content is appropriately secondary.

Overall the structure feels closer to a public research product than an admin
tool because it has curated labels, external source links, charts, and no edit
controls. Its table density, raw fallback terms, internal-style scores, and
legacy installation presentation still create an analyst-database feel.

## 5. Signal category versus status

The UI presents category as a coloured badge and status as a visually similar
pill in the same table row. They are independent data dimensions, but equal
visual weight invites a reading such as “New installation / Completed” as a
contradiction. Preserve the model; present status as the primary temporal state
when relevant, with category explicitly labelled “Project type” or secondary
metadata. Completed should receive a clear finished treatment, while category
explains what was completed.

## 6. Physical-installation UI readiness

The airport-detail `Installationer` card is the natural future insertion point,
but it currently renders legacy `Installation` rows as if they were the public
installation list. A future “EMAS installations” section must be separate from
legacy rows and permit multiple identities at one airport/runway/end. CGF and
MDW demonstrate why it cannot assume one installation per airport.

A sound eventual order is: reviewed current physical installations; Signals/
projects; incidents; curated historical evidence; then repair/replacement and
unresolved history only when intelligibly curated. Internal SourceAssertion,
identity-link, review, raw locator, and reconciliation metadata must remain
private. This is readiness analysis only; no current identity data is exported.

## 7. Sources and evidence

Sources appear in installation cards and signal views through title, publisher,
date, source-type pill/glossary link, and “Open source” action. This gives a
visitor a useful explanation of where many claims came from without exposing
the evidence model. Source-type mappings are understandable where mapped; raw
legacy values are not. “Details from source” notes can be helpful but may be
long and can overwhelm the main claim when rendered as a full nested card.

Public-export code deliberately excludes private `Signal.notes` and manual
estimates. It publishes `source_notes`, which are sourced public research. The
new internal evidence/reconciliation fields are absent from the export path.

## 8. Dashboard audit

A first-time visitor can identify the subject in seconds from the title and
subtitle, but “why this matters now” is less immediate. The statistics need
context: installation count, active signals, and high confidence are meaningful
only after understanding RWI’s research purpose. Recent updates offers activity,
but its “latest” labels mix different record kinds. The trend chart adds
analytical character but is secondary to an explanation of present change.

**Keep:** airport signs, concise subtitle, source-aware research framing, trend
distinction between confirmed installations and forecast signals.
**Improve:** one clear current-change story and explanatory labels around stats.
**Reconsider:** whether all four stats and two charts belong above the first
strong exploration action.

## 9. Airport list/detail and map

Airport search filters name/code/country client-side. This is fast and simple,
but the list does not expose current-installation versus project distinction.
Airport detail duplicates some timeline and table material; timeline should be
the navigation summary, not a second competing inventory. Empty states exist
for timeline, signals, installations, incidents, and runway data.

There is no public Leaflet map to audit. When introduced, markers must state
whether they represent airports, reviewed physical installations, projects, or
incidents. One airport marker cannot silently stand for multiple installations.

## 10. Responsive and accessibility observations

CSS has responsive breakpoints around 760px, 560px, and 480px: the detail
two-column grid becomes one column, stat cards become two columns, and tables
switch to labelled stacked rows. Trend charts are horizontally scrollable.
These are practical mobile safeguards.

Risks: native `title` tooltips are weak on touch; colour classes can carry
meaning without enough repeated text; hover states are not keyboard focus
states; the history-back button can be confusing; inline styles make later
accessibility consistency harder. HTML tables use headings and responsive
`data-label`s, and SVG charts carry accessible labels, which are positives.
This is not a formal WCAG assessment.

## 11. Bilingual readiness

The generated public UI is Swedish (`lang=sv`) with many hard-coded template
strings. Category and source-type labels are centralized, which is a good
starting point, but status labels, headings, empty states, chart copy, filter
copy, and JavaScript messages remain dispersed. Future English/Swedish support
needs centralized presentation dictionaries and parameterized phrases, not
translated database values. Avoid fixed text widths and concatenated sentences.

## 12. KEEP / IMPROVE / RECONSIDER

**KEEP**

- Aviation-specific dark visual language, airport-code signs, mono data, gauges.
- Static publishing, clear public/private export boundary, direct source actions.
- Lightweight client-side search/filtering and responsive table transformation.

**IMPROVE**

- Make current state, project type, confidence, and source provenance easier to
  distinguish at a glance.
- Establish a single curated presentation for source types and status labels.
- Make dashboard activity and airport detail hierarchy more purposeful.
- Centralize all public strings before adding English.

**RECONSIDER / REMOVE**

- History-dependent “Back” as a peer of breadcrumb navigation.
- Duplicate timeline/table inventory where it does not add a different task.
- Raw source-type fallback badges and technical score prominence.
- Excess nested source-detail panels when a short source summary/link is enough.

## 13. Top UX issues by impact

1. Category and status have equal badge weight and can read as contradictory.
2. Airport detail conflates legacy installations, timeline facts, and projects.
3. Dashboard lacks a single prominent current-change narrative.
4. No public distinction yet between reviewed physical identity and legacy data.
5. Source labels can fall back to raw technical terminology.
6. Dense tables make metadata compete with primary claims.
7. “Back” relies on browser history despite an existing airport breadcrumb.
8. No public map currently satisfies the map/navigation expectation.
9. Swedish strings are dispersed, increasing future localization cost.
10. Touch/keyboard accessibility is weaker than hover-oriented visual polish.

## 14. Recommended first UI implementation slice

Do **not** start with a map or a full physical-installation redesign. First
create a small public information-hierarchy slice: centralize public strings
and reframe Signal rows so **status** is visibly the current state and
**project type/category** is secondary. Apply it consistently to dashboard,
signal list/detail, and airport detail, retaining the existing visual identity.
That clarifies the most pervasive ambiguity without exposing internal evidence
or requiring physical-installation UI work.
