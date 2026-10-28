# Quality summary — run `20261027T215439Z-7323912d`

- Source: **stac** (https://earth-search.aws.element84.com/v1, collections in priority order: `sentinel-2-c1-l2a`, `sentinel-2-l2a`)
- Study area: Wurzacher Ried (NSG 4.035), Bad Wurzach, DE-BW; MGRS tile 32UNU; period 2021-01-01 → 2026-10-26
- Thresholds: scene cloud ≤ 80 %, parcel valid fraction ≥ 0.8, valid SCL classes [4, 5, 6], max blue reflectance 0.12, water threshold NDWI > 0
- Config SHA-256: `7323912d27ac536694c331d9c08dbe4e422af64563cfaefe1b9a367f6bccbd63`

## Scene ledger

972 STAC items found, 263 scenes processed, 709 rejected.

| Scene rejection reason | Count |
|---|---|
| radiometric_offset_inconsistent | 1 |
| scene_cloud_cover_above_max | 211 |
| superseded_by | 497 |

Processed scenes by collection, processing baseline and applied BOA offset (reflectance units):

| Collection | Baseline | boa_offset_applied flag | Offset applied by pipeline | Scenes |
|---|---|---|---|---|
| sentinel-2-c1-l2a | 05.00 | – | -0.1 | 38 |
| sentinel-2-c1-l2a | 05.09 | – | -0.1 | 36 |
| sentinel-2-c1-l2a | 05.10 | – | -0.1 | 20 |
| sentinel-2-c1-l2a | 05.11 | – | -0.1 | 78 |
| sentinel-2-c1-l2a | 05.12 | – | -0.1 | 40 |
| sentinel-2-c1-l2a | 05.13 | – | -0.1 | 7 |
| sentinel-2-l2a | 03.01 | False | 0.0 | 5 |
| sentinel-2-l2a | 04.00 | False | -0.1 | 4 |
| sentinel-2-l2a | 04.00 | True | 0.0 | 34 |
| sentinel-2-l2a | 05.10 | True | 0.0 | 1 |

## Parcel observations

| Parcel | Area | Pixels | Observations | Accepted | Rejected (reason: n) | Accepted range | Median NDWI |
|---|---|---|---|---|---|---|---|
| ref-field-100m | 1.00 ha | 100 | 263 | 148 | no_valid_pixels: 102, valid_fraction_below_min: 13 | 2021-02-20 → 2026-10-09 | -0.713 |
| wr-bog-osm | 1166.17 ha | 116622 | 263 | 112 | no_valid_pixels: 46, valid_fraction_below_min: 105 | 2021-02-20 → 2026-10-07 | -0.668 |
| wr-core-100m | 1.00 ha | 100 | 263 | 144 | no_valid_pixels: 107, valid_fraction_below_min: 12 | 2021-02-20 → 2026-10-09 | -0.629 |
| wr-nsg-4035 | 1799.42 ha | 179943 | 263 | 110 | no_valid_pixels: 38, valid_fraction_below_min: 115 | 2021-02-20 → 2026-10-07 | -0.666 |

## Pixel status totals (all parcels × all processed scenes)

| Status | Pixels |
|---|---|
| nodata | 329 |
| scl_masked | 33889932 |
| bright_surface | 926647 |
| non_positive_reflectance | 13615 |
| zero_denominator | 0 |
| out_of_range | 0 |
| valid | 43218672 |

## Year-to-year comparison (accepted observations, months [6, 7, 8])

Median of per-scene parcel medians; n = accepted scenes. Only same-season values are compared.

| Parcel | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| ref-field-100m | -0.702 (n=6) | -0.733 (n=14) | -0.626 (n=8) | -0.698 (n=7) | -0.714 (n=8) | -0.577 (n=13) |
| wr-bog-osm | -0.706 (n=2) | -0.671 (n=10) | -0.677 (n=5) | -0.703 (n=7) | -0.667 (n=5) | -0.674 (n=10) |
| wr-core-100m | -0.676 (n=4) | -0.616 (n=12) | -0.613 (n=7) | -0.667 (n=8) | -0.622 (n=8) | -0.585 (n=12) |
| wr-nsg-4035 | -0.709 (n=2) | -0.677 (n=10) | -0.680 (n=5) | -0.702 (n=7) | -0.669 (n=5) | -0.673 (n=10) |
