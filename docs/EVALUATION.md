# Evaluation against published NDWI

To test the work beyond the unit tests, the pipeline's NDWI was compared with independent, published values at
the same places and dates. All three references use the same index as this pipeline, McFeeters' green/NIR
NDWI = (B03 − B08) / (B03 + B08) from Sentinel-2. A summary is in the [README](../README.md#evaluation).

| Reference | Type | Place and dates | Result |
|---|---|---|---|
| [JNCC Sentinel-2 ARD NDWI v2](https://catalogue.ceda.ac.uk/uuid/9ae429e3026f4903a338f6baa0a11035) | national NDWI product, time series | Thorne Moors (peat bog) and Hornsea Mere (lake), England, 2019–2023 | **Peat bog: mean difference +0.011, RMSE 0.040, all 131 dates within ±0.10.** Lake: same course over time (r = 0.72), but +0.24 higher |
| Santecchia et al. (2023) | peer-reviewed paper, one date | La Salada lake, Argentina, 12 April 2019 | Shoreline pixels negative over the same range (land side −0.71 to 0.00 vs paper −0.64 to −0.01); open water +0.93 to +0.99 |
| Sahour et al. (2022) | peer-reviewed paper, time-series figure | Prairie pothole, North Dakota, 2017–2018 | Same wet and dry phases (peak +0.81 in Nov 2017, dry from mid-2018); our series is sparser |

## 1. Time series against the JNCC NDWI product (England)

The Joint Nature Conservation Committee (JNCC) publishes NDWI for every Sentinel-2 acquisition over England and
Scotland since 2015 (10 m, EPSG:27700, Open Government Licence). It is computed from the Defra/JNCC surface
reflectance product, so it shares the satellite and the formula with this pipeline but not the atmospheric
correction (Sen2Cor here) or the processing code.

**Method.** A 100 m box was run through the pipeline for 2019-01-01 to 2023-12-31 at two sites: Thorne Moors, a
lowland raised bog under restoration (53.632 N, 0.905 W), and Hornsea Mere, a shallow lake (53.904 N, 0.190 W).
For every date the pipeline accepted, [`scripts/jncc_check.py`](../scripts/jncc_check.py) reads the JNCC file of the
same day and takes the median of its pixels inside the same box. JNCC v2 files have no cloud mask, so only dates
that passed our SCL mask are compared.

![Time series of this pipeline and JNCC NDWI at Thorne Moors and Hornsea Mere](../examples/validation/jncc_comparison.png)

| Site | Dates compared | Mean difference (ours − JNCC) | MAE | RMSE | Pearson r | Within ±0.05 | Within ±0.10 |
|---|---|---|---|---|---|---|---|
| Thorne Moors (peat bog) | 131 of 134 | **+0.011** | 0.034 | **0.040** | 0.81 | 76 % | **100 %** |
| Hornsea Mere (lake) | 138 of 139 | +0.239 | 0.247 | 0.307 | 0.72 | 5 % | 17 % |

**Peat bog.** Both series lie between −0.5 and −0.8 and follow the same seasonal course date by date, with
almost no offset. For the surface this project is built for, the pipeline agrees with an independent product to
within the uncertainty of L2A reflectance.

**Lake.** The two series rise and fall together, but ours is about 0.2 higher on almost every date. The offset is
the same in every year and season, and in both the legacy and the Collection-1 data, so it does not come from
individual scenes. The likely cause is atmospheric correction over dark water: water reflects almost no NIR, so
the NDWI denominator is small and a NIR difference of 0.01 between the two corrections moves NDWI by 0.1–0.3. This
explanation has not yet been checked against JNCC's reflectance bands.

The same run in the dashboard (open **Run → Thorne Moors (JNCC check)**):

![Dashboard with the Thorne Moors run: satellite map with the 100 m box and the NDWI time series 2019–2023](../examples/validation/dashboard_thorne_moors.png)

## 2. One date against Santecchia et al. (2023), La Salada lake (Argentina)

The paper compares water indices on La Salada lake (39°28′ S, 62°42′ W) with a drone survey, using the same
Sentinel-2B L2A acquisition of 12 April 2019 (tile 20HNB). It reports NDWI only for the pixels on the lake
shoreline: "McFeeters values are all negative, between −0.64 and −0.01". [`scripts/la_salada_check.py`](../scripts/la_salada_check.py)
reads the same acquisition, traces the lake from NIR and summarises NDWI around its whole shoreline:

| Pixels (original 2019 processing, as in the paper) | Paper | This check: min to max | Median | Negative |
|---|---|---|---|---|
| Shoreline, both sides | −0.64 to −0.01, all negative | −0.71 to +0.62 | −0.16 | 76 % |
| Shoreline, land side | | **−0.71 to 0.00** | −0.29 | **99.9 %** |
| Shoreline, water side | | −0.63 to +0.62 | −0.01 | 52 % |
| Open water (≥ 30 m from shore) | not reported | 5th–95th percentile +0.72 to +0.99 | **+0.99** | 0 % |

The land side of the shoreline matches the paper. On the water side about half of our values are positive. The
paper's shoreline comes from a drone survey of part of the lake, and it uses the two pixels the surveyed line
passes between, while this check uses the first ring of pixels around the whole lake. A pipeline run with a 100 m
box at the lake centre (39.46248 S, 62.70053 W) accepted the date with a median of **+0.93**.

## 3. Prairie pothole from Sahour et al. (2022), North Dakota (USA)

Figure 4 of the paper shows NDWI time series of two potholes but gives no coordinates. The points were located
by matching the paper's Figure 4A against Sentinel-2 imagery of the study area (tile 13UFQ, near Bowbells): the
match scored 0.69, against 0.32 for the next-best location. A 20 m box was placed on the pond at Point 1
(48.76884 N, 102.94572 W) for 2017–2018 ([example run](../examples/runs/20261028T222139Z-8d7e9f63)).

| Date | 2017-08-20 | 2017-09-29 | 2017-10-04 | 2017-11-28 | 2018-05-02 | 2018-06-06 | 2018-06-11 | 2018-08-25 | 2018-09-29 | 2018-10-19 |
|---|---|---|---|---|---|---|---|---|---|---|
| NDWI | +0.18 | −0.15 | −0.61 | **+0.81** | +0.38 | +0.06 | −0.04 | −0.05 | −0.40 | −0.43 |

The paper's curve (read from the figure) peaks near +0.9 in 2017 and +0.85 in winter 2017/18 and drops to
about −0.3 in mid-2018. Our series shows the same wet and dry phases, but only 10 of 62 dates were accepted, so it
cannot resolve the paper's peaks. Two causes: snow and cloud, and the Sen2Cor scene classification, which labels
small ponds on many clear summer days as "dark area" (SCL 2) or "unclassified" (SCL 7), classes this pipeline
masks. The paper used Google Earth Engine and probably single pixels, and Earth Search holds no L2A data for this
area before 2017, so its 2016 values cannot be compared. Point 2 sits on a pond of about 0.03 ha, smaller than a
20 m box can resolve, and was not used.

## What the evaluation shows

- **Peat surfaces agree with an independent product** (mean difference +0.011, RMSE 0.040 over five years). This
  supports using the pipeline for the bog parcels it was built for.
- **Open water behaves as expected** (strongly positive NDWI, negative mixed shoreline pixels), but absolute
  values over water depend on the atmospheric correction and differ by about 0.2 from JNCC's.
- **Weakness found: very dark water.** With the −0.1 BOA offset of processing baseline ≥ 04.00, NIR over clear,
  dark water often falls to zero or below. The pipeline then counts the pixels as invalid (`non_positive_reflectance`)
  or rejects the whole scene (`radiometric_offset_inconsistent`; 30 scenes at Hornsea Mere). At La Salada only
  9 % of possible 100 m boxes in open water would pass the 80 % rule. This does not affect vegetated peat, where NIR
  is far above zero, but it limits the pipeline on open water.
- **Small ponds** are often masked as SCL "dark area" or "unclassified", which leaves gaps in their time series.

## Reproduce

```bash
# JNCC comparison: CSV per site, summary, and the chart above (~5 min, reads JNCC files over HTTP)
uv run --group ui --with matplotlib python scripts/jncc_check.py \
    examples/runs/20261029T085700Z-21efb7e2 examples/runs/20261029T085700Z-f03b45a9 \
    --plot examples/validation/jncc_comparison.png

# La Salada shoreline and open-water statistics (~1 min)
uv run --with scipy python scripts/la_salada_check.py
```

Results are in [`examples/validation/`](../examples/validation/): `jncc_thorne_moors.csv` and `jncc_hornsea_mere.csv`
(one row per date, both values and the JNCC file used), `jncc_summary.csv` and `la_salada_shoreline.csv`.

## References

- Joint Nature Conservation Committee (2025). *JNCC Sentinel-2 indices Analysis Ready Data (ARD) Normalised
  Difference Water Index (NDWI) v2.* NERC EDS Centre for Environmental Data Analysis.
  [catalogue.ceda.ac.uk/uuid/9ae429e3026f4903a338f6baa0a11035](https://catalogue.ceda.ac.uk/uuid/9ae429e3026f4903a338f6baa0a11035).
  Contains public sector information licensed under the Open Government Licence v3.0.
- Santecchia, G. S., Revollo Sarmiento, G. N., Genchi, S. A., Vitale, A. J., Delrieux, C. A. (2023). Assessment of
  Landsat-8 and Sentinel-2 Water Indices: A Case Study in the Southwest of the Buenos Aires Province (Argentina).
  *Journal of Imaging* 9(9), 186. [doi:10.3390/jimaging9090186](https://doi.org/10.3390/jimaging9090186)
- Sahour, H., Kemink, K. M., O'Connell, J. (2022). Integrating SAR and Optical Remote Sensing for
  Conservation-Targeted Wetlands Mapping. *Remote Sensing* 14(1), 159. [doi:10.3390/rs14010159](https://doi.org/10.3390/rs14010159)
- McFeeters, S. K. (1996). The use of the Normalized Difference Water Index (NDWI) in the delineation of open water
  features. *International Journal of Remote Sensing* 17(7), 1425–1432.
