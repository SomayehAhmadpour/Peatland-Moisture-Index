# Quality summary — run `20261029T085700Z-f03b45a9`

- Source: **stac** (https://earth-search.aws.element84.com/v1, collections in priority order: `sentinel-2-c1-l2a`, `sentinel-2-l2a`)
- Study area: Hornsea Mere (JNCC check) (53.90400, -0.19000); MGRS tile 30UXE; period 2019-01-01 → 2023-12-31
- Thresholds: scene cloud ≤ 80 %, parcel valid fraction ≥ 0.8, valid SCL classes [4, 5, 6], max blue reflectance 0.12, water threshold NDWI > 0
- Config SHA-256: `f03b45a9b097f34b9482c7b74fe888d7be8deba1b0b109d74e8391a0e93f1f92`

## Scene ledger

1708 STAC items found, 308 scenes processed, 1400 rejected.

| Scene rejection reason | Count |
|---|---|
| radiometric_offset_inconsistent | 30 |
| scene_cloud_cover_above_max | 387 |
| superseded_by | 983 |

Processed scenes by collection, processing baseline and applied BOA offset (reflectance units):

| Collection | Baseline | boa_offset_applied flag | Offset applied by pipeline | Scenes |
|---|---|---|---|---|
| sentinel-2-c1-l2a | 05.00 | – | -0.1 | 172 |
| sentinel-2-c1-l2a | 05.09 | – | -0.1 | 56 |
| sentinel-2-c1-l2a | 05.10 | – | -0.1 | 3 |
| sentinel-2-c1-l2a | 05.11 | – | -0.1 | 1 |
| sentinel-2-l2a | 02.11 | False | 0.0 | 17 |
| sentinel-2-l2a | 03.01 | False | 0.0 | 7 |
| sentinel-2-l2a | 04.00 | False | -0.1 | 14 |
| sentinel-2-l2a | 04.00 | True | 0.0 | 38 |

## Parcel observations

| Parcel | Area | Pixels | Observations | Accepted | Rejected (reason: n) | Accepted range | Median NDWI |
|---|---|---|---|---|---|---|---|
| point-53p90400n-0p19000w-100m | 1.00 ha | 100 | 308 | 139 | no_valid_pixels: 145, valid_fraction_below_min: 24 | 2019-01-17 → 2023-12-17 | 0.453 |

## Pixel status totals (all parcels × all processed scenes)

| Status | Pixels |
|---|---|
| nodata | 8 |
| scl_masked | 15065 |
| bright_surface | 451 |
| non_positive_reflectance | 505 |
| zero_denominator | 0 |
| out_of_range | 0 |
| valid | 14771 |

## Year-to-year comparison (accepted observations, months [6, 7, 8])

Median of per-scene parcel medians; n = accepted scenes. Only same-season values are compared.

| Parcel | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|
| point-53p90400n-0p19000w-100m | 0.401 (n=6) | 0.443 (n=4) | 0.427 (n=1) | 0.939 (n=4) | 0.233 (n=2) |
