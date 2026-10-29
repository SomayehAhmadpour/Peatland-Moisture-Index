# IRIS-SAT-NDWI — Sentinel-2 moisture monitoring for peatlands

Compute and monitor the green/NIR **NDWI** from Sentinel-2 L2A imagery for **any point, area or parcel**, from a
web dashboard or the command line. Clouds, fog, snow and no-data are masked per pixel, valid pixels are summarised
per parcel and date, and every number can be traced back to the exact Sentinel-2 files it came from.

![Dashboard: the Wurzacher Ried raised bog on satellite imagery, KPIs and the NDWI time series 2021–2026](docs/images/dashboard.png)

- **Dashboard:** browse runs on a satellite map with KPIs, time series and a plain-language interpretation, or start a
  **new analysis** anywhere: click a point or enter a longitude/latitude range, pick the dates, press *Run*.
- **Pipeline:** finds scenes through a public STAC API (no account needed), reads only the pixels it needs, and
  writes CSV/GeoJSON with full provenance, optionally to PostGIS.
- **Checked:** 138 automated tests (run on every push by [GitHub Actions](.github/workflows/tests.yml)), a [quality report](QUALITY_REPORT.md), and an [evaluation](#evaluation) against
  published NDWI (a peat bog agrees within RMSE 0.04 over five years).
- **Worked example:** the **Wurzacher Ried** raised bog in Baden-Württemberg (nature reserve NSG 4.035), real data
  2021–2026, in [`examples/`](examples/). Synthetic data is used only in tests and the offline demo, and is
  labelled as synthetic everywhere.

Built for IRIS Task 112 (raster/satellite pipeline, destination `iris_staging`). The core deliverable is the
pipeline, its `iris_staging` schema, the tests and the [quality report](QUALITY_REPORT.md); the dashboard and the
ad-hoc analysis are extras on top of the same pipeline.

> **Key findings from the real data**
>
> 1. **A metadata error in the public archive.** In Earth Search's legacy collection, items flagged
>    `earthsearch:boa_offset_applied=true` still advertise a −0.1 offset that was already removed from the pixel
>    values. Trusting the metadata made the bog's reflectance negative (1 of 56 summer observations accepted).
>    Proof: the same acquisition has green DN 474 (baseline 03.00) and 483 (05.00), so no +1000 offset is present.
>    The pipeline honours the flag, and a [scene guard](#method) catches items where the flag itself is wrong.
> 2. **Fog that the scene classification calls land.** On 2021-11-12, valley fog was labelled "not vegetated" and
>    gave the bog −0.12 instead of about −0.65. A blue-reflectance test (> 0.12) now masks it; afterwards no
>    accepted observation exceeds −0.45.
> 3. **Two archives, one answer.** The same acquisitions read from both collections give identical parcel medians
>    (31/31 in 2023, 28/28 in 2021), and the example run reproduces exactly on another platform and GDAL version.
> 4. **Independent check.** Against the JNCC NDWI product, a peat bog in England agrees within RMSE 0.040 over
>    five years ([evaluation](#evaluation)). Over open water the pipeline is weaker (about +0.24, see limitations).
> 5. **Auditable to the pixel.** Every excluded pixel has a status, every dropped scene a reason, every result the
>    exact files it was read from, and results computed with other thresholds never overwrite earlier ones.

**Deviation from the brief:** the brief asks for Python 3.11+; this project needs **3.12**: rasterio 1.4.3 has no Linux ARM
wheel (needed for Docker on Apple Silicon), and 1.5.1, which has one, requires Python ≥ 3.12. uv and the Docker image
install 3.12 automatically.

**Contents:** [Quick start](#quick-start) · [Using the dashboard](#using-the-dashboard) ·
[What can I change, and where?](#what-can-i-change-and-where) · [Command line](#command-line) ·
[How it works](#how-it-works) · [Data provenance](#data-provenance) · [Method](#method) ·
[Database](#database-schema-iris_staging) · [Tests](#tests) · [Evaluation](#evaluation) ·
[Assumptions and limitations](#assumptions-and-limitations) · [Troubleshooting](#troubleshooting) ·
[Repository layout](#repository-layout)

---

## Quick start

**1. Install the tools (once).** You need [Git](https://git-scm.com/downloads) and **one** of:

| | Docker (recommended for reviewers) | uv (fastest for development) |
|---|---|---|
| Install | [Docker Desktop](https://www.docker.com/products/docker-desktop/), or on macOS [Colima](https://github.com/abiosoft/colima) with [Homebrew](https://brew.sh): `brew install colima docker docker-compose docker-buildx` | macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` (or `brew install uv`); Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Includes | Python, all packages and a PostGIS database | Python 3.12 and all packages (installed by uv itself) |
| Disk space | about 3 GB (images) | about 0.6 GB (`.venv/`) |

Nothing else needs installing: no separate Python, GDAL or PostgreSQL (the rasterio wheel bundles GDAL). Live runs
need internet access to the public Earth Search catalogue; tests and the synthetic demo work offline.

**2. Get the code.**

```bash
git clone https://github.com/SomayehAhmadpour/Peatland-Moisture-Index.git
cd Peatland-Moisture-Index
```

**3a. Run with Docker**

```bash
colima start --vm-type vz --vz-rosetta   # macOS with Colima only: start the Docker engine
cp .env.example .env                     # once: choose a local database password (.env is git-ignored)
docker compose build                     # again after every code change or git pull
docker compose run --rm tests            # optional: all 138 tests → "138 passed"
docker compose up -d ui                  # dashboard → http://localhost:8501
```

**3b. …or run with uv**

```bash
uv sync                                       # exact versions from uv.lock
uv run pytest                                 # optional: 136 offline tests in < 2 s
uv run --group ui streamlit run ui/app.py     # dashboard → http://localhost:8501 (Ctrl+C to stop)
```

**4. Open http://localhost:8501**, pick a run in the sidebar, or click **＋ New analysis**. Stop the Docker dashboard
with `docker compose stop ui`. Run only one dashboard at a time (see [Troubleshooting](#troubleshooting)).

<details>
<summary>Installed packages (all pinned in <code>pyproject.toml</code> and <code>uv.lock</code>)</summary>

| Group | Installed by | Packages |
|---|---|---|
| main | `uv sync` | rasterio (GDAL), numpy, shapely, pyproj, pystac-client, pydantic, pyyaml, SQLAlchemy, GeoAlchemy2, psycopg, Alembic |
| `dev` | `uv sync` (default group) | pytest |
| `ui` | `uv sync --group ui`, or automatically on the first `uv run --group ui …` | Streamlit, folium, streamlit-folium, plotly, pandas |
| evaluation extras | `uv run --with …` (temporary, not added to the project) | matplotlib, scipy |

On Apple Silicon the official `postgis/postgis` image runs emulated: Docker Desktop does this automatically, Colima
needs `--vz-rosetta`.
</details>

## Using the dashboard

The dashboard lists every run in `output/` (your runs) and `examples/runs/` (the bundled examples). Viewing runs
needs no database or network.

- **Look at a run.** Choose the **Run**, a **Parcel**, the **Period** and **Months** to show, and the **Scene** drawn on
  the map. You get KPIs, the parcel on a satellite map (switch to a street map at the top right), the NDWI time
  series with the p25–p75 range and rejected dates, a **rule-based interpretation** (fixed templates, no AI), a
  same-season comparison by year, and the scene ledger. A red badge marks synthetic data.
- **＋ New analysis** runs the pipeline for a new place and period:

  ![New analysis: click the map to place a box, choose the box size and period](docs/images/new_analysis.png)

  1. Choose **Point + box** or **Area (lon/lat range)**.
  2. *Point:* click the map or type latitude and longitude (west and south are negative), and set the **Box edge**
     (20–2000 m). *Area:* type west, east, south and north, as decimal degrees (`131.4833`) or degrees and minutes
     (`131°29′ E`), or click the map to place an area.
  3. Choose the **Period** (any dates from 2017 on) and a **Name**, then press **Run analysis**.

  The run opens when it is done (a few months take about a minute; several years can take 10 minutes or more). The
  Sentinel-2 tile and the metric coordinate system are chosen automatically. Areas are limited to **400 km²** and
  to **one tile** (about 110 × 110 km), and a date counts only when ≥ 80 % of the area is clear, which gets rarer as
  areas grow.
- **Manage this run → Re-run with these settings** repeats a run with new dates (and, for New-analysis boxes, a new
  box size). The result is a new run; runs are never edited in place. **Delete this run** removes one of your runs
  from `output/`.
- **Export to PostGIS** sends the selected run to a database of your choice (see [Database](#database-schema-iris_staging)).

## What can I change, and where?

| Setting | In the dashboard | In the config file ([`config/wurzacher_ried.yaml`](config/wurzacher_ried.yaml)) / command line |
|---|---|---|
| Place: a box around a point | **New analysis** → Point + box (map click or lat/lon; box 20–2000 m) | `boxes` |
| Place: a longitude/latitude rectangle | **New analysis** → Area (lon/lat range) | `areas` |
| Place: polygons (e.g. an OSM reserve) | – (shown, not drawn) | `parcels_geojson` |
| Period | **New analysis** → Period, or **Manage this run → Re-run** | `start_date`, `end_date`; CLI `--start`, `--end` |
| Name | **New analysis** → Name | `study_area` |
| Box size of an existing run | **Manage this run → Re-run** (New-analysis box runs) | `size_m` |
| Sentinel-2 tile, metric CRS | chosen automatically | `mgrs_tile`, `target_crs` |
| Quality thresholds (cloud pre-filter, 80 % valid, SCL classes, fog test, offset guard, water threshold) | not editable: **New analysis** reads them from the config file at every run; **Re-run** keeps the run's own settings | see the table below |
| Data source, parallel reads | – | `stac_url`, `collections`, `max_workers` |
| What the charts show (parcel, period, months, scene, rejected dates) | sidebar filters (view only, no new data) | – |
| Database target | **Export to PostGIS** form | `IRIS_STAGING_DATABASE_URL`; CLI `export --schema` |

So to change a threshold for dashboard analyses, edit the config file and start a new analysis. With uv the change
applies at once; with Docker the file is part of the image, so run `docker compose build` and restart the dashboard
(`docker compose up -d ui`).

<details>
<summary>All config keys</summary>

The file is validated by a typed model ([`config.py`](src/iris_ndwi/config.py)), so a typo or an out-of-range value
stops the run with a clear message. Copy it to experiment: `cp config/wurzacher_ried.yaml config/my_test.yaml`,
then `--config config/my_test.yaml`.

| Key | Default | Meaning |
|---|---|---|
| `start_date`, `end_date` | 2021-01-01, 2026-10-26 | Acquisition period (UTC) |
| `collections` | `[sentinel-2-c1-l2a, sentinel-2-l2a]` | STAC collections in priority order |
| `mgrs_tile` | `32UNU` | Sentinel-2 tile; items from other tiles are rejected. Omit it to use the tile covering the parcels in the most items |
| `max_scene_cloud_pct` | 80 | Item-level cloud pre-filter |
| `min_valid_fraction` | 0.8 | A parcel observation is accepted when ≥ this share of its pixels is valid |
| `valid_scl_classes` | `[4, 5, 6]` | SCL classes counted as clear (vegetation, not-vegetated, water) |
| `max_blue_reflectance` | 0.12 | Fog/haze/snow test on B02; `null` disables it |
| `require_positive_reflectance` | true | Mask pixels with green or NIR ≤ 0 |
| `max_below_offset_fraction` | 0.5 | Scene guard against a double-applied BOA offset |
| `water_threshold` | 0.0 | NDWI above this counts towards `water_fraction` |
| `parcels_geojson` | OSM snapshot | GeoJSON (EPSG:4326) with `parcel_id`, `name`, `kind`, `source`, `source_ref`, `licence` properties; omit to use boxes only |
| `boxes` | core + reference | Square parcels from a WGS 84 centre, `size_m` and `snap_m` (10 = Sentinel-2 grid) |
| `areas` | none | Longitude/latitude rectangles (`west`, `south`, `east`, `north`, WGS 84) |
| `max_workers` | 8 | Parallel scene reads |

**Add a parcel:** append a box (or a feature to a GeoJSON file) with a new, unique `parcel_id` (lower-case letters,
digits and `-`), then check it with `uv run iris-ndwi parcels --config …`:

```yaml
  - parcel_id: wr-forest-100m
    name: Forest east of the bog (reference)
    kind: reference
    center_lat: 47.92
    center_lon: 9.90
    size_m: 100
    snap_m: 10
```

**Another study area with several parcels:** set `study_area`, the boxes, areas and/or `parcels_geojson`, and the
`mgrs_tile` that contains them (or omit `mgrs_tile`). Items from other tiles are listed in `scenes.csv` as
`other_mgrs_tile`. [`scripts/fetch_osm_parcels.py`](scripts/fetch_osm_parcels.py) shows how to turn OSM features into
a parcel file.
</details>

## Command line

The command `iris-ndwi` runs the same pipeline as the dashboard. Prefix it with `uv run`, or with
`docker compose run --rm pipeline` for Docker (`--to-db` then writes to the bundled database).

```bash
uv run iris-ndwi run --config config/wurzacher_ried.yaml --start 2024-06-01 --end 2025-08-31   # your period
uv run iris-ndwi run --config config/wurzacher_ried.yaml                     # the configured period (~3 min)
uv run iris-ndwi run --config config/wurzacher_ried.yaml --source synthetic  # offline demo
uv run iris-ndwi parcels --config config/wurzacher_ried.yaml                 # check parcel geometries only

docker compose run --rm pipeline run --config config/wurzacher_ried.yaml --start 2024-06-01 --end 2025-08-31 --to-db
```

Options: `-v` before `run` shows progress; `--output-dir <dir>` writes elsewhere; `--to-db` also upserts into
`iris_staging`, with the connection read from `IRIS_STAGING_DATABASE_URL` (see [`.env.example`](.env.example)).
The run ends with a line like `run 20261029T105855Z-c26028a8: 67 scenes … -> output/20261029T105855Z-c26028a8`;
the run ID is the UTC start time plus a fingerprint of the configuration.

**What a run writes** to `output/<run_id>/`:

| File | Content |
|---|---|
| `quality_summary.md` | Human-readable summary: scene ledger, acceptance per parcel, latest value against the same month in other years (monitoring), pixel statuses, summer comparison. **Start here** |
| `observations.csv` | One row per parcel × scene: pixel counts per status, NDWI statistics, accepted or rejected with reason |
| `scenes.csv` | Every STAC item found: used or rejected (with reason), baseline, offset, exact COG URLs of all four bands read |
| `parcels.geojson` | The parcels used, EPSG:25832 |
| `manifest.json` | Provenance: full config + SHA-256, collections, software versions, licences, counts |

**Export a finished run to your own PostGIS database** (into `iris_staging` or any other schema):

```bash
export IRIS_STAGING_DATABASE_URL=postgresql://user:password@host:5432/database
uv run iris-ndwi export output/<run_id> --schema my_ndwi --create-tables
```

**Docker extras:** `docker compose exec staging-db psql -U iris_staging -d iris_staging_db` opens the bundled
database; `docker compose down` stops everything (add `-v` to delete the database). The compose stack starts its
**own** PostGIS container (`staging-db`, bound to `127.0.0.1:5434`), runs the Alembic migration, which creates only
the `iris_staging` schema, and never connects to `iris_core` or any production system.

<details>
<summary>Helper scripts</summary>

```bash
# NDWI time-series chart (standalone SVG, no plotting dependency)
uv run python scripts/plot_timeseries.py output/<run_id>/observations.csv output/ndwi.svg

# Validation: same acquisitions from both collections must give the same NDWI (~5–10 min)
uv run python scripts/cross_collection_check.py --start 2023-03-01 --end 2023-10-31

# Refresh the committed OSM parcel snapshot (Overpass API)
uv run python scripts/fetch_osm_parcels.py

# Evaluation against published NDWI (see docs/EVALUATION.md)
uv run --group ui --with matplotlib python scripts/jncc_check.py examples/runs/<run> --plot out.png
uv run --with scipy python scripts/la_salada_check.py
```
</details>

---

## How it works

```mermaid
%%{init: {"fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif"}, "flowchart": {"wrappingWidth": 340}}}%%
flowchart TD
    S2[("<b>Sentinel-2 L2A</b><br/>Earth Search STAC, Cloud-Optimized GeoTIFFs")]
    FUTSRC[("<b>Future sources</b><br/>Google Earth Engine, Landsat 8/9<br/>Copernicus Data Space, Sentinel-1 SAR<br/><i>via the SceneSource adapter</i>")]
    CFG[/"<b>Config + parcels</b><br/>config/wurzacher_ried.yaml, data/parcels/"/]
    FUTSHP[/"<b>Future work: shapefile upload</b><br/>select the area from a .shp file"/]
    REF[("<b>Published NDWI</b><br/>JNCC Sentinel-2 NDWI product<br/>Santecchia et al. 2023, Sahour et al. 2022")]

    ACQ["<b>1. Acquisition</b><br/>search both collections, drop other tiles<br/>de-duplicate acquisitions, cloud pre-filter<br/><i>sources.py, scenes.py</i>"]
    PRO["<b>2. Processing</b><br/>read B02, B03, B08, SCL windows<br/>BOA-offset harmonisation + guard<br/>SCL + fog masks → NDWI per pixel<br/><i>sources.py, ndwi.py</i>"]
    ANA["<b>3. Analytics</b><br/>pixels per parcel, status counts<br/>median, p25–p75, valid fraction<br/>accept / reject per parcel × scene<br/><i>parcels.py, zonal.py, pipeline.py</i>"]
    STO["<b>4. Storage</b><br/>CSV, GeoJSON (EPSG:25832), manifest<br/>PostGIS iris_staging (upserts)<br/>export to your own PostGIS<br/><i>outputs.py, staging.py, alembic/</i>"]
    SRV["<b>5. Results & queries</b><br/>quality summary, validation SQL<br/>rule-based interpretation<br/><i>report.py, validation_queries.sql, interpret.py</i>"]
    UI["<b>6. Streamlit dashboard</b><br/>KPIs, satellite map, time series, scene ledger<br/>new analysis for any point or area<br/><i>ui/app.py</i>"]
    EVA["<b>7. Evaluation</b><br/>same places and dates as the references<br/>agreement: bias, RMSE, correlation<br/><i>scripts/jncc_check.py, la_salada_check.py</i>"]
    FUTAI["<b>Future work: AI assistant</b><br/>chatbox for interpretation"]

    S2 --> ACQ --> PRO --> ANA --> STO --> SRV --> UI
    STO --> EVA
    REF --> EVA
    CFG -.-> ACQ
    CFG -.-> ANA
    FUTSHP -.->|planned| CFG
    FUTSRC -.->|planned| ACQ
    SRV -.->|planned| FUTAI

    classDef source fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e
    classDef stage fill:#f8fafc,stroke:#334155,color:#0f172a
    classDef ui fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef future fill:#fafafa,stroke:#a1a1aa,stroke-dasharray:5 4,color:#71717a
    classDef eval fill:#fef3c7,stroke:#b45309,color:#78350f
    class S2,CFG,REF source
    class ACQ,PRO,ANA,STO,SRV stage
    class EVA eval
    class UI ui
    class FUTSRC,FUTSHP,FUTAI future
```

Every stage records what it drops and why (scene ledger, pixel status counts, rejection reasons), so each number on
the dashboard can be traced back through the chain to the exact Sentinel-2 files. The evaluation step checks the
stored results against independent published NDWI.

## Data provenance

| Item | Value |
|---|---|
| Imagery | Copernicus Sentinel-2 MSI **Level-2A** (bottom-of-atmosphere reflectance, ESA Sen2Cor) |
| Access | Element84 **Earth Search v1** STAC API `https://earth-search.aws.element84.com/v1`. The pipeline reads public Cloud-Optimized GeoTIFFs over HTTPS with range requests and needs no credentials |
| Collections (priority order) | 1. `sentinel-2-c1-l2a`: ESA Collection-1 reprocessing (processing baseline ≥ 05.00, consistent radiometry). 2. `sentinel-2-l2a`: the legacy archive, used only to fill gaps (on Earth Search, C1 lacks Jan–Nov 2022 for tile 32UNU) |
| Bands and resolution | B03 green and B08 NIR (10 m) for NDWI; B02 blue (10 m) for the fog/haze test; SCL scene classification (20 m) |
| Licence | Copernicus Sentinel data are free and open ([Sentinel data legal notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice)). Outputs contain *modified Copernicus Sentinel data* |
| Parcels (example) | OpenStreetMap: nature reserve `way/688159107` (NSG 4.035, WDPA 6979) and bog `relation/2958383` (`wetland=bog`), © OpenStreetMap contributors, **ODbL 1.0**. Snapshot in [`data/parcels/`](data/parcels/) |
| Stable identifiers | STAC `item_id` for scenes. `parcel_id` (e.g. `wr-nsg-4035`, with an OSM `source_ref`) for parcels. `observation_id = parcel_id:item_id:method_version:<thresholds digest>` for results (the digest covers every threshold that changes a value or its acceptance, so re-running with other thresholds adds rows instead of overwriting) |

Each run's `manifest.json` holds the full configuration and its SHA-256 hash, the thresholds digest, the software
versions, the licences and the counts; `scenes.csv` lists **every** STAC item found, with its metadata, the reason
it was dropped, and the exact COG URLs of all four bands read (B02, B03, B08, SCL).

**Why not Google Earth Engine?** GEE needs a Google account or service-account credentials. That would break "never
include secrets" and stop reviewers running the stack without their own account. It also processes server-side,
which makes per-pixel auditing harder. A GEE adapter can be added behind the same `SceneSource` protocol
([`sources.py`](src/iris_ndwi/sources.py)).

### The worked example: Wurzacher Ried

Tile **32UNU** (native EPSG:32632), 2021-01-01 → 2026-10-26, four parcels
([`config/wurzacher_ried.yaml`](config/wurzacher_ried.yaml)):

| `parcel_id` | Kind | Area | Purpose |
|---|---|---|---|
| `wr-nsg-4035` | reserve | 1 799 ha | Official nature-reserve boundary (OSM, ref NSG 4.035) |
| `wr-bog-osm` | bog | 1 166 ha | Mapped raised-bog extent; a multipolygon with 22 holes |
| `wr-core-100m` | bog-core | 1.00 ha = **100 px** | 100 m × 100 m on the open Haidgauer Hochmoor surface (47.926242 N, 9.877493 E) |
| `ref-field-100m` | reference | 1.00 ha = **100 px** | 100 m × 100 m on farmland west of the reserve (47.933030 N, 9.842128 E). Shows whether the bog signal differs from ordinary land |

Boxes are built in EPSG:25832 from their WGS 84 centre and snapped to the 10 m grid. A pixel belongs to a parcel when
its centre falls inside the polygon, so each box contains **exactly 100 Sentinel-2 pixels** (tested). Results:
972 STAC items, 263 scenes processed, 514 accepted parcel observations; the open bog core has a higher median NDWI
(−0.63) than the farmland reference (−0.71). Over vegetation this difference reflects vegetation type and crop
stage as much as wetness, so the field is a contrast, not a moisture baseline: each parcel is best compared with
its own values in the same month (see the monitoring table in `quality_summary.md`). Details in
[`QUALITY_REPORT.md`](QUALITY_REPORT.md).

## Method

1. **Search** the STAC catalogue for every configured collection, using the bounding box of all parcels (WGS 84) and
   the date range.
2. **Select scenes.** Each rule is recorded in the scene ledger: unparseable metadata → `metadata_error`; other MGRS
   tile → `other_mgrs_tile`; **duplicates** (same tile, platform and UTC day, from ESA reprocessing, split datastrips
   or both collections) → keep the item with the largest AOI coverage, then collection priority, baseline, lowest tile
   nodata % and latest generation time, the rest `superseded_by:<item_id>`; item cloud cover > 80 % →
   `scene_cloud_cover_above_max` (a pre-filter only; acceptance is decided per pixel).
3. **Read** blue, green, NIR and SCL in one window covering all parcels, in the scene's own grid. **Reflectances are
   never resampled.** The window is aligned to the shared 20 m grid, so each SCL pixel maps onto exactly 2 × 2 pixels at 10 m.
4. **Radiometric harmonisation.** `reflectance = DN × scale + offset`, from each item's `raster:bands` metadata. Since
   processing baseline 04.00 (2022-01-25), L2A data carry a +1000 DN offset (−0.1 in reflectance). In Earth Search's
   *legacy* collection, items flagged `earthsearch:boa_offset_applied=true` already have it removed from their DN yet
   still advertise −0.1, so no offset is applied to them (verified on 2021-05-31: 03.00 DN 474 vs 05.00 DN 483).
5. **Scene guard.** If more than 50 % of clear pixels are below the advertised offset, the offset would be applied
   twice, so the scene is rejected as `radiometric_offset_inconsistent` (e.g. 2022-09-13: 94 %; a dark winter scene: about 25 %).
6. **Classify pixels** (the first failing rule wins and its status is kept): `NODATA` → `SCL_MASKED` (class not in
   4 vegetation, 5 not-vegetated, 6 water) → `BRIGHT_SURFACE` (blue > 0.12: fog, haze or snow that SCL labels as land,
   e.g. 2021-11-12, when fog gave the bog −0.12 instead of about −0.65) → `NON_POSITIVE_REFLECTANCE` (green or NIR ≤ 0)
   → `ZERO_DENOMINATOR` → `OUT_OF_RANGE` (|NDWI| > 1).
7. **NDWI (McFeeters 1996)** = (B03 − B08) / (B03 + B08). A zero denominator gives NaN, never ±inf, and is counted.
8. **Summarise per parcel and scene:** pixel counts per status, valid fraction, mean, median, std, p25/p75, min/max,
   water fraction (share of valid pixels with NDWI > 0). **Accepted** when valid fraction ≥ 0.8 (inclusive); otherwise
   rejected as `no_valid_pixels`, `valid_fraction_below_min` or `parcel_has_no_pixels`, with statistics kept for auditing.

Runs are deterministic: the same config and the same STAC items give byte-identical observations (tested).

## Database schema (`iris_staging`)

Created by the Alembic migrations in [`alembic/versions/`](alembic/versions/) (`0001` creates the tables, `0002`
moves scene decisions into their own table and adds the thresholds digest and `blue_href`, keeping existing rows);
the Alembic version table lives in `iris_staging` as well. The writer ([`staging.py`](src/iris_ndwi/staging.py))
checks that every target table is in `iris_staging`, and all writes are idempotent upserts in one transaction.

| Table | Key | Content |
|---|---|---|
| `s2_ndwi_run` | `run_id` | timestamp + config hash, source, `synthetic_data`, method version, full manifest (JSONB) |
| `s2_ndwi_parcel` | `parcel_id` | name, kind, source, `source_ref`, licence, area, `MULTIPOLYGON` **EPSG:25832** (CHECK `ST_IsValid`, GiST index) |
| `s2_scene` | `item_id` | item metadata only: collection, acquisition time, platform, tile, baseline, cloud %, offset flag + applied offset, sun elevation, COG URLs of B02, B03, B08 and SCL. A NULL never overwrites a known value |
| `s2_ndwi_run_scene` | `run_id, item_id` | what **each run** decided about each item: `selected`, `rejection_reason`. Kept per run, because selection depends on the run's collections and pre-filter |
| `s2_ndwi_observation` | `observation_id` (unique `parcel_id, item_id, method_version, method_params_sha256`) | pixel counts per status, valid fraction, NDWI statistics, water fraction, `status`, `rejection_reason`, `run_id` of the last run that wrote it. CHECKs: counts add up, NDWI in [−1, 1] |

<details>
<summary>Schema mapping: where every column comes from</summary>

| Column(s) | Source |
|---|---|
| `s2_scene.item_id`, `collection` | STAC item `id`, `collection` |
| `acquired_at` | `properties.datetime` (UTC) |
| `platform`, `mgrs_tile` | `properties.platform`; `properties.grid:code` (`MGRS-32UNU` → `32UNU`) |
| `processing_baseline`, `generation_time` | `properties.s2:processing_baseline`, `properties.s2:generation_time` |
| `scene_cloud_pct`, `sun_elevation` | `properties.eo:cloud_cover`, `properties.view:sun_elevation` |
| `boa_offset_applied_flag` | `properties.earthsearch:boa_offset_applied` (legacy collection only) |
| `reflectance_scale`, `boa_offset` | `assets.green.raster:bands[0].scale` / `.offset`; the offset is set to 0 when the flag above is true |
| `blue_href`, `green_href`, `nir_href`, `scl_href` | `assets.blue/green/nir/scl.href` (B02, B03, B08, SCL Cloud-Optimized GeoTIFFs) |
| `s2_ndwi_run_scene.selected`, `rejection_reason` | scene selection and scene guard ([Method](#method) steps 2 and 5) |
| `s2_ndwi_parcel.*` | config `boxes`/`areas`, or the GeoJSON properties `parcel_id`, `name`, `kind`, `source`, `source_ref`, `licence`; geometry validated and reprojected to EPSG:25832 |
| `s2_ndwi_observation.n_*` | pixels inside the parcel per status ([Method](#method) step 6) |
| `valid_fraction`, `ndwi_*`, `water_fraction`, `status` | [Method](#method) steps 7–8, from the valid pixels only |
| `method_version`, `method_params_sha256` | `ndwi-mcfeeters-b03b08-v1`; SHA-256 of `target_crs`, `valid_scl_classes`, `max_blue_reflectance`, `require_positive_reflectance`, `max_below_offset_fraction`, `min_valid_fraction`, `water_threshold` |
| `s2_ndwi_run.*` | the run's `manifest.json` (config, its SHA-256, software versions, licences, counts) |

</details>

**Export** (`iris-ndwi export`, dashboard *Export to PostGIS*) writes the same five tables into one chosen schema of
any database: the tables are redirected with SQLAlchemy's `schema_translate_map`, `iris_core`, `public` and `pg_*` are
refused, and *create tables* adds the PostGIS extension, schema and tables if missing. Geometries are reprojected to
EPSG:25832, and an existing `parcel_id` with a different outline (> 0.5 m Hausdorff distance) stops the export instead
of silently moving it. From the Docker dashboard, the bundled database is `postgresql://<user>:<password>@staging-db:5432/<db>`,
and a database on your machine is reached via `host.docker.internal` instead of `localhost`.

Validation SQL: [`examples/validation_queries.sql`](examples/validation_queries.sql).

## Tests

`uv run pytest` runs 136 offline tests in under two seconds; `docker compose run --rm tests` runs all 138, adding two
PostGIS round trips (the `iris_staging` writer, and the export into a fresh schema that is dropped afterwards).
[GitHub Actions](.github/workflows/tests.yml) runs all 138 against a PostGIS service on every push.
Synthetic scene ids start with `SYNTHETIC_`, so test data can never overwrite real rows.

- **NDWI:** known values; zero denominator (NaN, no warning); negative reflectance; out-of-range values; nodata
  priority; every SCL class; fog labelled as land (blue test, threshold boundary, rule order).
- **Temporal comparability:** the same surface before and after baseline 04.00 gives the same NDWI.
- **Scene selection with recorded real STAC items** ([`tests/fixtures/`](tests/fixtures/)): reprocessed duplicates,
  a split-datastrip pair, collection priority, the legacy offset flag, missing assets or metadata.
- **Geometry:** a 100 m box gives exactly 100 pixels; repairs; empty, line or zero-area geometries rejected; holes kept.
- **End to end (synthetic scenes):** snow, fog, half cloud, tile-edge nodata, read error, wrong tile, cloud pre-filter,
  double offset, an unexpected error in one scene (rejected as `processing_error`, the run continues), an empty
  period, identical repeated runs; other thresholds give new observation ids, another period the same ones; the
  monitoring table.
- **New analysis and re-runs:** CRS per UTM zone, automatic tile choice, point and area runs, coordinate parsing, the
  400 km² limit, an area outside every tile.
- **PostGIS:** refused schemas, CSV typing, reprojection to EPSG:25832; a second run with other thresholds adds
  rows and keeps its own scene decisions; run folders written before the thresholds digest get the same keys.

## Evaluation

To test the work beyond the unit tests, the pipeline's NDWI was compared with independent published values at the same
places and dates — full details in [`docs/EVALUATION.md`](docs/EVALUATION.md).

| Reference | Place and dates | Result |
|---|---|---|
| [JNCC Sentinel-2 NDWI product](https://catalogue.ceda.ac.uk/uuid/9ae429e3026f4903a338f6baa0a11035) (time series) | Thorne Moors peat bog and Hornsea Mere lake, England, 2019–2023 | **Peat bog: mean difference +0.011, RMSE 0.040, all 131 dates within ±0.10.** Lake: same course (r = 0.72), but +0.24 higher |
| Santecchia et al. (2023), *J. Imaging* 9, 186 | La Salada lake, Argentina, 12 April 2019 | Shoreline pixels negative over the same range as the paper; open water +0.93 to +0.99 |
| Sahour et al. (2022), *Remote Sensing* 14, 159 | Prairie pothole, North Dakota, 2017–2018 | Same wet and dry phases (peak +0.81); fewer accepted dates |

![This pipeline vs JNCC NDWI: time series and scatter for a peat bog and a lake](examples/validation/jncc_comparison.png)

## Assumptions and limitations

- **What NDWI measures.** Green/NIR NDWI was designed to detect **open water**. Over peat vegetation it responds mainly
  to surface wetness and canopy state, and it is **not a calibrated soil-moisture measurement**. For vegetation water
  content, NDMI (B08/B11) is usually more sensitive. The water threshold NDWI > 0 is McFeeters' generic value.
- **Uncertainty.** L2A reflectance accuracy is roughly ±0.01–0.02, a few hundredths in NDWI for this surface. A
  100-pixel box is sensitive to individual mixed or misclassified pixels, so the p25/p75 spread is reported too.
- **Masks.** SCL (Sen2Cor, 20 m) can miss thin cloud or shadow edges, or mislabel snow; no buffer is added around
  clouds or shadows (a dilation of the mask is the obvious next step). The blue test (0.12) and the scene guard
  (0.5) were derived from the example site and can need tuning elsewhere; the blue test can miss thin, dark haze.
- **Comparing parcels and years.** NDWI differences between land covers (bog vs field) mix wetness with vegetation
  type and crop stage, so compare a parcel with itself: the same month in other years (monitoring table) or the same
  season. Accepted observations only; sun elevation varies from about
  18° (December) to 65° (June) in the example and is recorded per scene.
- **Collection gap.** Most of 2022 in the example comes from the legacy collection; the cross-collection checks in
  [`examples/`](examples/) show identical medians where both exist.
- **Parcel boundaries.** The OSM features are community-mapped, not the official LUBW cadastre.
- **Dark open water.** With the −0.1 offset, NIR over clear, dark water often reaches zero or below, so such pixels are
  invalid and lake scenes can be rejected. The scene guard was designed on land: over a mostly-water area it
  mistakes dark water for a double offset (30 scenes at Hornsea Mere). NDWI over water also depends on the
  atmospheric correction (see Evaluation). Peat vegetation is not affected.
- **One tile per run.** Parcels crossing a tile edge get `nodata` pixels; tiles are not mosaicked. Dashboard areas are
  limited to 400 km².

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser: *Connection error* / `ERR_CONNECTION_REFUSED` on localhost:8501 | No dashboard is running. Start it (`docker compose up -d ui`, or the uv command) and reload the page |
| The page or the map stays empty | Two dashboards share port 8501 (e.g. Docker and a local `streamlit run`). Stop one (`docker compose stop ui`, or Ctrl+C) and reload |
| Docker shows old code or old settings | `docker compose build`, then restart the service |
| `Cannot connect to the Docker daemon` | Start the engine: `colima start --vm-type vz --vz-rosetta` (or open Docker Desktop) |
| `Port 8501 is already in use` | Another copy is running. Use it, or add `--server.port 8502` |
| `warning: VIRTUAL_ENV=… does not match the project environment` | Harmless (an active conda/venv); `conda deactivate` / `deactivate` silences it |
| A few `read_error` scenes in `scenes.csv` | Transient HTTP errors from the COG host; recorded, and the run continues. Re-run to fill them |
| `column … method_params_sha256 does not exist` or `relation … s2_ndwi_run_scene does not exist` | The database predates migration 0002: `docker compose run --rm migrate` (existing rows are kept) |
| `schema iris_staging does not exist` with `--to-db` | `docker compose run --rm migrate` (or `uv run alembic upgrade head` with `IRIS_STAGING_DATABASE_URL` set) |
| Export: `schema … does not exist` | Tick *Create PostGIS, the schema and the tables* (CLI: `--create-tables`), or run the migration |
| Export: `parcel … already exists … with a different geometry` | That `parcel_id` is stored with another outline; export into another schema |
| Export: `connection failed … Connection refused` from the Docker dashboard | `localhost` is the container itself; use `staging-db` or `host.docker.internal` |

## Repository layout

```
README.md, QUALITY_REPORT.md   documentation and quality/error report
src/iris_ndwi/   config · parcels · scenes · sources (STAC/COG) · ndwi · zonal · pipeline · outputs · staging · report · interpret · synthetic · adhoc · cli
ui/              Streamlit dashboard (optional `ui` dependency group)
config/          example configuration (no secrets)
data/parcels/    committed OSM parcel snapshot (ODbL)
alembic/         iris_staging migrations (0001 tables, 0002 per-run scene decisions + thresholds key)
.github/         GitHub Actions: all tests against PostGIS on every push
scripts/         plot_timeseries · cross_collection_check · fetch_osm_parcels · jncc_check · la_salada_check
tests/           pytest suite + recorded real STAC fixtures
examples/        example and evaluation runs, cross-collection checks, validation SQL, evaluation results
docs/            EVALUATION.md, README screenshots
Dockerfile, docker-compose.yml, pyproject.toml, uv.lock, .env.example
```
