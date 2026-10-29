# Quality summary — run `20261029T085700Z-21efb7e2`

- Source: **stac** (https://earth-search.aws.element84.com/v1, collections in priority order: `sentinel-2-c1-l2a`, `sentinel-2-l2a`)
- Study area: Thorne Moors (JNCC check) (53.63200, -0.90500); MGRS tile 30UXE; period 2019-01-01 → 2023-12-31
- Thresholds: scene cloud ≤ 80 %, parcel valid fraction ≥ 0.8, valid SCL classes [4, 5, 6], max blue reflectance 0.12, water threshold NDWI > 0
- Config SHA-256: `21efb7e29a2d3febf13bfdd63f6e6ce32cce9f272b18dbdd930b69d4ee3b7747`

## Scene ledger

1706 STAC items found, 331 scenes processed, 1375 rejected.

| Scene rejection reason | Count |
|---|---|
| radiometric_offset_inconsistent | 6 |
| scene_cloud_cover_above_max | 388 |
| superseded_by | 981 |

Processed scenes by collection, processing baseline and applied BOA offset (reflectance units):

| Collection | Baseline | boa_offset_applied flag | Offset applied by pipeline | Scenes |
|---|---|---|---|---|
| sentinel-2-c1-l2a | 05.00 | – | -0.1 | 187 |
| sentinel-2-c1-l2a | 05.09 | – | -0.1 | 64 |
| sentinel-2-c1-l2a | 05.10 | – | -0.1 | 3 |
| sentinel-2-c1-l2a | 05.11 | – | -0.1 | 2 |
| sentinel-2-l2a | 02.11 | False | 0.0 | 16 |
| sentinel-2-l2a | 03.01 | False | 0.0 | 7 |
| sentinel-2-l2a | 04.00 | False | -0.1 | 14 |
| sentinel-2-l2a | 04.00 | True | 0.0 | 38 |

## Parcel observations

| Parcel | Area | Pixels | Observations | Accepted | Rejected (reason: n) | Accepted range | Median NDWI |
|---|---|---|---|---|---|---|---|
| point-53p63200n-0p90500w-100m | 1.00 ha | 100 | 331 | 134 | no_valid_pixels: 170, valid_fraction_below_min: 27 | 2019-01-30 → 2023-12-17 | -0.636 |

## Pixel status totals (all parcels × all processed scenes)

| Status | Pixels |
|---|---|
| nodata | 0 |
| scl_masked | 18644 |
| bright_surface | 203 |
| non_positive_reflectance | 0 |
| zero_denominator | 0 |
| out_of_range | 0 |
| valid | 14253 |

## Year-to-year comparison (accepted observations, months [6, 7, 8])

Median of per-scene parcel medians; n = accepted scenes. Only same-season values are compared.

| Parcel | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|
| point-53p63200n-0p90500w-100m | -0.664 (n=6) | -0.650 (n=6) | -0.649 (n=4) | -0.694 (n=5) | -0.688 (n=7) |
