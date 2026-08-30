# world_land_110m.path.txt — provenance and license

("RWI - Juicy Design Mission #2 - V2.4 Global Intelligence Map WOW Pass"
mission — geo-asset recon required this be sourced from a well-established,
redistributable geometry source with documented license, never downloaded
and committed silently.)

## Source

Upstream data: **Natural Earth**, 1:110m scale Admin 0 land boundaries
(<https://www.naturalearthdata.com/downloads/110m-physical-vectors/>).
Natural Earth's own terms: *"No permission is needed to use Natural Earth.
Crediting the authors is unnecessary."* — public domain.

Redistribution used: **`world-atlas`** (`land-110m.json`), maintained under
the `topojson` GitHub organization, built by Michael Bostock — the
canonical, widely-used TopoJSON redistribution of Natural Earth data in the
D3/topojson ecosystem.

- Repository: <https://github.com/topojson/world-atlas>
- File fetched: <https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json>
  (fetched 2026-08-30, 55,207 bytes, single merged `land` `GeometryCollection`
  → one `MultiPolygon`, 130 arcs)
- License: ISC (`https://raw.githubusercontent.com/topojson/world-atlas/master/LICENSE`),
  "Copyright 2013-2019 Michael Bostock" — permissive, redistribution and
  modification explicitly allowed.

## Processing applied (this repository only, not upstream)

`land-110m.json` is TopoJSON (delta-encoded, quantized arcs in spherical
lon/lat degrees). It was decoded and reprojected locally, once, by a
throwaway script (not committed — pure data transformation, no external
service, no runtime dependency):

1. Standard TopoJSON arc decoding (cumulative delta-sum, `transform.scale`/
   `transform.translate` applied) — the same algorithm `topojson-client`
   implements, reproduced directly rather than adding that package as a
   dependency for a one-time, offline conversion.
2. Ring reconstruction from arc indices (forward/reversed via the standard
   `i >= 0 ? i : ~i` encoding), dropping each subsequent arc's first point
   (shared with the previous arc's last point), per the TopoJSON spec.
3. Simple equirectangular projection to this file's own flat SVG coordinate
   space: `x = (lon + 180) * (900/360)`, `y = (83 - lat) * (900/360)` —
   viewBox `0 0 900 360`, spanning longitude -180..180 and latitude
   83..-61 (Antarctica's southernmost extent and the high Arctic ocean are
   cropped from the visible band - a standard, common convention for a wide
   dashboard-style world strip; 1 degree of longitude and 1 degree of
   latitude are given equal pixel weight, so no shape is stretched
   disproportionately in one axis).
4. Antimeridian-crossing split: any ring whose consecutive projected points
   jump by more than half the viewBox width has a new SVG subpath (`M`)
   started instead of a line (`L`) drawn across the whole map - fixes a
   handful of real landmasses that straddle ±180° longitude (verified:
   Fiji and one small Russian Far East island were the only two real
   features affected at this resolution) without altering their real shape,
   only how the seam is drawn.

No coordinate was invented, guessed, or geocoded from an RWI airport/
country name - every point in this file traces directly back to Natural
Earth's own published land-boundary vertices, transformed only by the
lossless/standard operations above.

## What this file is NOT

- Not a political map (no country border lines - a single merged land
  silhouette only, matching `land-110m.json`'s own "land" object, not its
  sibling "countries" object).
- Not RWI airport or Signal data of any kind.
- Not used to place any per-airport marker - only whole-country activity
  markers (see `app/static_export/build.py`'s own `_COUNTRY_MAP_POSITION`
  docstring for that separate, small, non-exhaustive lookup).
