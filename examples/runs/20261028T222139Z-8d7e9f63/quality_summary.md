# Quality summary — run `20261028T222139Z-8d7e9f63`

- Source: **stac** (https://earth-search.aws.element84.com/v1, collections in priority order: `sentinel-2-c1-l2a`, `sentinel-2-l2a`)
- Study area: P1-pond (48.76884, -102.94572); MGRS tile 13UFQ; period 2017-01-01 → 2018-12-31
- Thresholds: scene cloud ≤ 80 %, parcel valid fraction ≥ 0.8, valid SCL classes [4, 5, 6], max blue reflectance 0.12, water threshold NDWI > 0
- Config SHA-256: `8d7e9f63d58714ac920743e1f75f03d736aba0b15af738bd6fba626bdda73f4f`

## Scene ledger

128 STAC items found, 62 scenes processed, 66 rejected.

| Scene rejection reason | Count |
|---|---|
| read_error | 1 |
| scene_cloud_cover_above_max | 16 |
| superseded_by | 49 |

Processed scenes by collection, processing baseline and applied BOA offset (reflectance units):

| Collection | Baseline | boa_offset_applied flag | Offset applied by pipeline | Scenes |
|---|---|---|---|---|
| sentinel-2-c1-l2a | 05.00 | – | -0.1 | 17 |
| sentinel-2-l2a | 00.01 | False | 0.0 | 41 |
| sentinel-2-l2a | 02.11 | False | 0.0 | 4 |

## Parcel observations

| Parcel | Area | Pixels | Observations | Accepted | Rejected (reason: n) | Accepted range | Median NDWI |
|---|---|---|---|---|---|---|---|
| point-48p76884n-102p94572w-20m | 0.04 ha | 4 | 62 | 10 | no_valid_pixels: 52 | 2017-08-20 → 2018-10-19 | -0.047 |

## Pixel status totals (all parcels × all processed scenes)

| Status | Pixels |
|---|---|
| nodata | 0 |
| scl_masked | 196 |
| bright_surface | 12 |
| non_positive_reflectance | 0 |
| zero_denominator | 0 |
| out_of_range | 0 |
| valid | 40 |

## Year-to-year comparison (accepted observations, months [6, 7, 8])

Median of per-scene parcel medians; n = accepted scenes. Only same-season values are compared.

| Parcel | 2017 | 2018 |
|---|---|---|
| point-48p76884n-102p94572w-20m | 0.179 (n=1) | -0.042 (n=3) |
