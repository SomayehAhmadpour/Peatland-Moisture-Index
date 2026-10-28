# Quality and error report — IRIS-SAT-NDWI

Representative run: [`examples/runs/20261027T215439Z-7323912d/`](examples/runs/20261027T215439Z-7323912d/)
(real Sentinel-2 L2A, tile 32UNU, 2021-01-01 → 2026-10-26, config SHA-256 `7323912d…`).
The run's own generated summary is [`quality_summary.md`](examples/runs/20261027T215439Z-7323912d/quality_summary.md),
and [`examples/ndwi_timeseries.svg`](examples/ndwi_timeseries.svg) plots the accepted observations.

## 1. Coverage

| | Count |
|---|---|
| STAC items found (both collections) | 972 |
| Scenes processed after selection | **263** (219 Collection-1, 44 legacy fill) |
| Parcel observations (4 parcels × 263 scenes) | 1 052 |
| **Accepted observations** | **514** |

| Parcel | Pixels | Accepted / 263 | Accepted dates | Median NDWI (accepted) | Typical p75−p25 within parcel |
|---|---|---|---|---|---|
| `wr-core-100m` (open bog, 1 ha) | 100 | 144 | 2021-02-20 → 2026-10-09 | −0.629 | 0.014 |
| `ref-field-100m` (farmland, 1 ha) | 100 | 148 | 2021-02-20 → 2026-10-09 | −0.713 | 0.013 |
| `wr-bog-osm` (bog, 1 166 ha) | 116 622 | 112 | 2021-02-20 → 2026-10-07 | −0.668 | 0.083 |
| `wr-nsg-4035` (reserve, 1 799 ha) | 179 943 | 110 | 2021-02-20 → 2026-10-07 | −0.666 | – |

The bog core has accepted observations in every calendar month: 5–19 per month, with the fewest in
December and January. The large polygons are accepted less often than the 1 ha boxes because a single
cloud anywhere over 1 800 ha already pushes the valid fraction below 0.8.

**Summer (Jun–Aug) medians by year** (n = accepted scenes):

| Parcel | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| `wr-core-100m` | −0.676 (4) | −0.616 (12) | −0.613 (7) | −0.667 (8) | −0.622 (8) | −0.585 (12) |
| `ref-field-100m` | −0.702 (6) | −0.733 (14) | −0.626 (8) | −0.698 (7) | −0.714 (8) | −0.577 (13) |
| `wr-bog-osm` | −0.706 (2) | −0.671 (10) | −0.677 (5) | −0.703 (7) | −0.667 (5) | −0.674 (10) |

In every year except 2026, the open bog core is consistently **wetter (higher NDWI) than the reference
field**, by 0.01–0.12. Year-to-year differences in the bog core (≤ 0.09) are larger than the spread
within the parcel (about 0.014), but some years rest on only 4–8 scenes. Treat them as indications, not
trends. No accepted bog-core pixel ever exceeds NDWI 0 (water fraction 0), so open water is not
detected at 10 m. The big bog polygon does show it in 91 of 112 accepted scenes, from its pools and ditches.

## 2. Rejected data

**Scenes (709 of 972 items).** Every rejected item and its reason is listed in `scenes.csv`.

| Reason | n | Meaning |
|---|---|---|
| `superseded_by:<id>` | 497 | Same tile, platform and UTC day already present: mostly the same acquisition in both collections; also ESA reprocessing and split datastrips |
| `scene_cloud_cover_above_max` | 211 | Item-level cloud > 80 % (pre-filter only) |
| `radiometric_offset_inconsistent` | 1 | `S2B_32UNU_20220913_0_L2A`: 94.3 % of clear pixels below the advertised offset, so the offset was already applied in the data |

**Observations (538 of 1 052).**

| Reason | wr-core | ref-field | wr-bog-osm | wr-nsg |
|---|---|---|---|---|
| `no_valid_pixels` | 107 | 102 | 46 | 38 |
| `valid_fraction_below_min` (< 0.8) | 12 | 13 | 105 | 115 |

**Pixels (all parcels × processed scenes).**

| Status | Pixels |
|---|---|
| valid | 43 218 672 |
| `SCL_MASKED` (cloud, shadow, cirrus, snow, unclassified, dark area) | 33 889 932 |
| `BRIGHT_SURFACE` (fog, haze or snow missed by SCL) | 926 647 |
| `NON_POSITIVE_REFLECTANCE` | 13 615 |
| `NODATA` | 329 |
| `ZERO_DENOMINATOR`, `OUT_OF_RANGE` | 0, 0 (never occur in real data with the positive-reflectance rule; covered by unit tests) |

## 3. Problems found in the real data and how each was handled

1. **Mixed offsets in the legacy archive.** In Earth Search `sentinel-2-l2a`, items flagged
   `boa_offset_applied=true` have the −0.1 offset already removed from their DN, yet still advertise
   `offset: −0.1`. A first run that trusted the metadata produced negative reflectance on the bog, and
   only 1 of 56 summer observations was accepted. Evidence: for the same acquisition (2021-05-31) the
   original 03.00 product has green DN 474 and the reprocessed 05.00 product has 483, so no +1000 is
   present. **Fix:** use Collection-1 (`sentinel-2-c1-l2a`, consistent) first, and honour the flag in the legacy
   fill. *Test:* `test_parse_real_legacy_item_with_offset_already_applied`.
2. **The flag itself is sometimes wrong.** Some legacy items are flagged `false` but are harmonised
   (e.g. 2022-09-13). **Fix:** a scene guard rejects any scene where more than 50 % of clear pixels sit
   below the advertised offset. The threshold separates the real failure (94 %) from dark winter scenes
   with the sun at about 18°, where 25 % of green pixels are genuinely ≤ 0. Those pixels are then masked
   individually instead of discarding the whole scene. *Test:* `test_below_offset_fraction_*` and the synthetic double-offset scene.
3. **Collection-1 gap.** Earth Search's C1 has no 32UNU items for Jan–Nov 2022. **Fix:** fall back to the
   legacy collection by priority. This fills 2022 with 10–14 summer scenes per parcel.
4. **Duplicate acquisitions** (497). Three kinds turned up: ESA reprocessing (datetimes 1 ms apart);
   one pass split over two datastrips (2023-06-25, with 7 % vs 76 % tile nodata); and the same acquisition
   in both collections. **Fix:** group by tile, platform and UTC day, then keep by AOI coverage, collection
   priority, baseline, nodata % and generation time. *Tests* use the recorded real items.
5. **Fog mislabelled by SCL.** On 2021-11-12, valley fog over the bog was classed SCL 5 ("not vegetated")
   and gave the bog core an NDWI of −0.12 instead of about −0.65. In total, 10 accepted winter
   observations sat above −0.45. The item's cloud-probability layer reported 0 % for these. **Fix:** blue
   reflectance > 0.12 → `BRIGHT_SURFACE`. Clear summer scenes in the 1 ha boxes have a median of 0 bright pixels.
   After the fix, **no accepted observation exceeds −0.45**, and summer medians changed by ≤ 0.006.

## 4. Edge cases (tested)

| Edge case | Behaviour | Where tested |
|---|---|---|
| green + NIR = 0 | NaN plus status `ZERO_DENOMINATOR`, no ±inf and no numpy warning | `test_ndwi.py` |
| Reflectance ≤ 0 (dark water, low sun) | `NON_POSITIVE_REFLECTANCE` (configurable); allowing them can give \|NDWI\| > 1 → `OUT_OF_RANGE` | `test_ndwi.py` |
| Fully clouded or snow-covered parcel | rejected `no_valid_pixels`, statistics NULL | `test_zonal.py`, `test_pipeline.py` |
| Valid fraction exactly 0.8 | accepted (inclusive); statistics of rejected observations kept | `test_zonal.py` |
| Parcel outside the read window or tile edge | `parcel_has_no_pixels` / `NODATA` counts | `test_zonal.py`, synthetic tile-edge scene |
| Unreadable asset (HTTP error) | scene rejected `read_error`, the run continues | synthetic scene |
| Invalid, empty, line or zero-area geometry | repaired with `make_valid` and flagged, or rejected | `test_parcels.py` |
| Box in EPSG:25832 on the EPSG:32632 pixel grid | exactly 100 pixels | `test_parcels.py` |
| Empty date range | valid empty result and report | `test_pipeline.py` |

## 5. Acceptance criteria → evidence

| Criterion | Evidence |
|---|---|
| Denominator-zero behaviour | NaN plus a counted status; tests above. 0 occurrences in real data |
| Temporal comparability | Per-item offset harmonisation; the same surface before and after baseline 04.00 gives the same NDWI (test). **Cross-collection check:** different files from two archives give identical parcel medians for **31/31** matched observations in 2023 and **28/28** in 2021 (`examples/cross_collection_check_*.csv`). Year comparisons are limited to the same season |
| Acquisition dates | UTC `acquired_at` on every scene and observation; duplicates resolved per UTC day; sun elevation recorded |
| Threshold assumptions | All in config and documented: cloud pre-filter 80 %, valid fraction 0.8, SCL {4,5,6}, blue 0.12, water NDWI > 0 (McFeeters, not calibrated), offset guard 0.5 |
| Deterministic / auditable | Two live runs of the same config ~35 min apart (before the blue test was added) gave identical scene, observation and acceptance counts and identical summary tables; repeated runs give identical rows (test); config hash, software versions and exact COG URLs in `manifest.json` and `scenes.csv`; every excluded pixel has a status |
| Spatial output, EPSG:25832 | Parcels stored as valid `MULTIPOLYGON` SRID 25832 (CHECK `ST_IsValid`); validation SQL query 3 |

## 6. Known gaps and uncertainty

- **No independent check for the 2022 fill.** C1 has no accepted 2022 acquisitions that overlap the
  legacy ones, so there is no direct comparison for that year. The 2021 and 2023 matches share processing
  lineage with C1 (05.00/05.09), so they validate the offset handling, not independent radiometry. The
  2022 summer values sit within the range of neighbouring years.
- **What NDWI measures here.** Green/NIR NDWI reflects surface wetness and canopy state, not soil moisture.
  There is no field data to calibrate against. NDMI (B08/B11) would be the natural companion index.
- **Thresholds.** The 0.12 blue threshold and the 0.5 guard were set from this site's data and could
  need tuning elsewhere. Thin dark haze can still pass.
- **Parcels.** The boundaries come from OSM, not the official LUBW data. About 1 % of the bog polygon
  (12 ha) lies outside the NSG polygon.

## 7. Docker verification (Apple Silicon, Colima)

`docker compose run --rm tests`: **91/91 passed**, including the PostGIS round trip. The full live run
inside Docker (`docker compose run --rm pipeline`) reproduced the example run exactly: 263 scenes,
1 052 observations, 514 accepted. That was on a different platform (Linux arm64) and with a different
library stack from the one used to create the example (rasterio 1.5.1 / GDAL 3.12.4 vs 1.4.3 / 3.9.3).
A value-by-value comparison of a summer-2025 run across the two library versions found 0 differing
values in 56 observations. The validation queries pass against the populated database, and the dashboard
container serves the page. Running the Docker stack for the first time uncovered, and led to fixes for:

- rasterio 1.4.3 has no Linux ARM wheel, so it was upgraded to 1.5.1 (which requires Python ≥ 3.12) and
  `libexpat1` was added to the image.
- The official PostGIS image is amd64-only, so it is pinned to `platform: linux/amd64` (emulated on ARM).
- The database search path lacked `public`, where PostGIS defines `geometry`, so the migration failed.
- Synthetic scene ids used the real Sentinel-2 id format and could have upserted over real rows. They are
  now prefixed `SYNTHETIC_`, the database test deletes its rows, and the validation queries exclude synthetic runs.
