# IRIS-SAT-NDWI — Sentinel-2 Peatland Moisture Index

Task 112 · raster / satellite pipeline · destination `iris_staging`

This pipeline computes the green/NIR **NDWI** from Sentinel-2 L2A imagery for peatland parcels in the
**Wurzacher Ried** (Bad Wurzach, Baden-Württemberg; nature reserve NSG 4.035). It masks no-data and
clouds per pixel, summarises valid pixels per parcel and scene, and writes the results to CSV/GeoJSON
files and to an isolated PostGIS `iris_staging` schema. Every number in the output can be traced back to
the exact Sentinel-2 files it came from.

- **Real imagery.** The example result in [`examples/`](examples/) comes from real Sentinel-2 data
  (2021-01 → 2026-10). Synthetic data is used **only** in tests and the offline demo, and is labelled
  as synthetic in every output (`manifest.json → synthetic_data`, the run summary, and the `iris_staging.s2_ndwi_run.synthetic_data` column).
- **Quality report.** See [`QUALITY_REPORT.md`](QUALITY_REPORT.md) for coverage, rejected data, edge cases and acceptance evidence.

## Workflow

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

Every stage records what it drops and why (scene ledger, pixel status counts, rejection reasons), so each
number on the dashboard can be traced back through the chain to the exact Sentinel-2 files. The evaluation
step checks the stored results against independent published NDWI; see [Evaluation](#evaluation).

---

## Quick start

### Prerequisites

- **[Git](https://git-scm.com/downloads)** to clone the repository (preinstalled on most macOS and Linux systems).
- **Docker route:** [Docker Desktop](https://www.docker.com/products/docker-desktop/), or Docker Engine with Compose v2.
  On macOS without Docker Desktop, [Colima](https://github.com/abiosoft/colima) works too (no admin rights needed);
  with [Homebrew](https://brew.sh): `brew install colima docker docker-compose docker-buildx`, then `colima start --vm-type vz --vz-rosetta`.
  The official `postgis/postgis` image is amd64-only, so Apple Silicon runs it emulated; Docker Desktop does this automatically, and Colima needs `--vz-rosetta`.
- **Local route:** [uv](https://docs.astral.sh/uv/getting-started/installation/): `curl -LsSf https://astral.sh/uv/install.sh | sh`
  on macOS/Linux, `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows
  (or `brew install uv`). uv installs Python 3.12 and the pinned dependencies by itself.
- Internet access for live runs (public Earth Search STAC/COGs, no account needed). Tests and the synthetic demo work offline.

**Nothing else needs installing.** There is no separate Python, GDAL or PostgreSQL to set up: uv provides Python, the
rasterio wheel bundles GDAL, and PostGIS runs in Docker (needed only for `--to-db`, the export and the two database tests).
The packages and their exact versions are listed in [`pyproject.toml`](pyproject.toml) and locked in [`uv.lock`](uv.lock):

| Group | Installed by | Packages |
|---|---|---|
| main | `uv sync` | rasterio (GDAL), numpy, shapely, pyproj, pystac-client, pydantic, pyyaml, SQLAlchemy, GeoAlchemy2, psycopg, Alembic |
| `dev` | `uv sync` (default group) | pytest |
| `ui` | `uv sync --group ui`, or automatically on the first `uv run --group ui …` | Streamlit, folium, streamlit-folium, plotly, pandas |
| evaluation extras | `uv run --with …` (temporary, not added to the project) | matplotlib, scipy |

Disk space: about 0.6 GB for the local environment (`.venv/`), or about 3 GB for the Docker images (pipeline image
2.3 GB, PostGIS 0.9 GB).

```bash
git clone https://github.com/SomayehAhmadpour/Peatland-Moisture-Index.git
cd Peatland-Moisture-Index
```

### Docker (recommended — no local Python needed)

```bash
cp .env.example .env                    # choose a local password; .env is git-ignored
docker compose build                    # build the image (again after every code change or git pull)
docker compose run --rm tests           # all tests, incl. PostGIS round trip
docker compose run --rm pipeline        # live run 2021-01 → 2026-10 → output/ + iris_staging (~3 min)
docker compose up ui                    # dashboard on http://localhost:8501 (Ctrl+C to stop)

# offline demo with synthetic scenes (no network):
docker compose run --rm pipeline run --config config/wurzacher_ried.yaml --source synthetic --to-db

# inspect the staging tables
docker compose exec staging-db psql -U iris_staging -d iris_staging_db

docker compose down                     # stop; add -v to also delete the staging database
```

The compose stack starts its **own** PostGIS container (`staging-db`, bound to `127.0.0.1:5434`).
It runs the Alembic migration, which creates only the `iris_staging` schema, and it never connects to `iris_core` or any
production system.

### Local (Python ≥ 3.12, uv)

```bash
uv sync                                                            # exact versions from uv.lock
uv run pytest                                                      # unit tests (offline, < 1 s)
uv run iris-ndwi parcels --config config/wurzacher_ried.yaml       # validate parcel geometries
uv run iris-ndwi run --config config/wurzacher_ried.yaml           # live run → output/<run_id>/ (~3 min)
uv run iris-ndwi run --config config/wurzacher_ried.yaml --start 2025-06-01 --end 2025-08-31   # ~30 s
uv run iris-ndwi run --config config/wurzacher_ried.yaml --source synthetic                    # offline
```

`--start`/`--end` override the configured period for one run. Add `--output-dir <dir>` to write somewhere
other than `output/`. For progress logging use `uv run iris-ndwi -v run …` (`-v` goes before `run`). Add `--to-db` to also upsert into `iris_staging`:
the connection string is read from `IRIS_STAGING_DATABASE_URL` (see `.env.example`), and nothing secret is stored in the repository.

**Export a finished run to your own PostGIS database.** Any run folder (yours or an example) can be upserted later,
into `iris_staging` or a schema of your choice:

```bash
export IRIS_STAGING_DATABASE_URL=postgresql://user:password@host:5432/database
uv run iris-ndwi export output/<run_id> --schema my_ndwi --create-tables
```

`--create-tables` creates the PostGIS extension, the schema and the four tables (identical to the Alembic
migration) if they are missing; without it the export stops when the schema does not exist. `iris_core`,
`public` and the system schemas are refused. The dashboard has the same export (see below).

**What a run writes.** Every run creates `output/<run_id>/`, named by UTC timestamp plus config hash:

| File | Content |
|---|---|
| `quality_summary.md` | Human-readable summary: scene ledger, acceptance per parcel, pixel statuses, summer comparison. **Start here** |
| `observations.csv` | One row per parcel × scene: pixel counts per status, NDWI statistics, accepted or rejected with reason |
| `scenes.csv` | Every STAC item found: used or rejected (with reason), baseline, offset, exact COG URLs |
| `parcels.geojson` | The parcels used, EPSG:25832 |
| `manifest.json` | Provenance: full config + SHA-256, collections, software versions, licences, counts |

### Dashboard

```bash
uv run --group ui streamlit run ui/app.py          # → http://localhost:8501, Ctrl+C to stop
```

This is a Streamlit page in the style of the IRIS dashboard. It lists every run under `output/` and
`examples/runs/`; viewing them needs no database or network. Pick a run, a parcel, a period, months
and a scene to see:
KPIs; a map of parcel medians for the chosen scene over satellite imagery (Esri World Imagery, or OpenStreetMap); the parcel's NDWI time series, with the p25–p75
range and rejected dates marked; a **rule-based interpretation** ([`interpret.py`](src/iris_ndwi/interpret.py):
fixed templates, deterministic, no LLM); a same-season comparison by year; and the scene quality ledger.
A badge flags synthetic data whenever it is shown.

**＋ New analysis** (top of the sidebar) runs the pipeline for any place and period without editing a config
file. It starts with nothing selected. Click a point on the map (satellite or OpenStreetMap layer; a new click
replaces the previous point) or type its latitude and longitude, set the box edge (20–2000 m) and any period
from 2017 on, then press **Run analysis**. The box gets every quality setting from
`config/wurzacher_ried.yaml`. The MGRS tile is picked automatically (the tile covering the box in the most
items), and the metric CRS is EPSG:25832 inside UTM zone 32, otherwise the point's own UTM zone (ETRS89 in
Europe, WGS 84 elsewhere), so a 100 m box stays 100 m. The run is written to `output/` like a CLI run and
opens in the dashboard. It needs network access to Earth Search: six months took about 1 minute in testing,
and several years can take 10 minutes or more. In code: [`adhoc.py`](src/iris_ndwi/adhoc.py).

Choose **Area (lon/lat range)** instead of *Point + box* to analyse a rectangle given by its west, east, south
and north edges, e.g. 131°29′–132°15′ E, 46°28′–46°59′ N. Coordinates can be typed as decimal degrees
(`131.4833`) or degrees and minutes (`131°29′ E`, `46 28 N`, seconds optional); west and south are negative or
marked W/S. Clicking the map moves the area and keeps its size; with no area yet, a click places one of about
0.05° × 0.03°. The whole area is one parcel, built in the
metric CRS of its centre with densified edges, because parallels are curved in UTM. Two limits apply:

- **At most 400 km².** Every scene is read as one 10 m window over the area. A 400 km² area peaked at about
  0.7 GB of memory with 8 scenes in parallel (`max_workers`), and one month took 25 s. The example above
  is about 3,400 km², so the form asks to split it.
- **One Sentinel-2 tile** (about 110 × 110 km). The time series stays on one pixel grid. An area that no tile
  covers completely stops with a message before any pixel is read.

The quality rules are unchanged: a scene counts only when at least 80 % of the area is clear. That gets rarer as
areas grow (in the 400 km² test, the best July scene was 78.7 % clear), so larger areas yield fewer
accepted observations.

**Manage this run** (sidebar, under the run list):
- **Re-run with new dates** repeats the selected run with the same parcels and settings, read from its
  `manifest.json`. Only the dates change, and the result is a new run. For a run made with
  **＋ New analysis**, the **Box edge** can be changed as well, around the same centre point. Runs are never
  edited in place, so every result stays traceable to its exact configuration.
- **Delete this run** removes the run's folder from `output/`, after a confirmation. Example runs in
  `examples/runs/` are part of the repository and can't be deleted from the dashboard.

**Export to PostGIS** (sidebar, under *Manage this run*) upserts the selected run into a PostGIS database:
enter its URL (`postgresql://user:password@host:5432/database`) and a schema (default `iris_staging`). The URL
is kept only in the browser session; leave it empty to use `IRIS_STAGING_DATABASE_URL`. Exporting the same run
again updates its rows. From the Docker dashboard, reach the bundled database as
`postgresql://<user>:<password>@staging-db:5432/<db>` and a database on your machine via `host.docker.internal`
instead of `localhost`.

### Changing the configuration

Everything that is a choice rather than a fact lives in [`config/wurzacher_ried.yaml`](config/wurzacher_ried.yaml).
It is validated by a typed model ([`config.py`](src/iris_ndwi/config.py)), so a typo or an out-of-range value stops the run with a clear message.
Copy the file to experiment (`cp config/wurzacher_ried.yaml config/my_test.yaml`, then `--config config/my_test.yaml`).

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
| `areas` | none | Longitude/latitude rectangles (`west`, `south`, `east`, `north`, WGS 84), as made by *Area (lon/lat range)* |
| `max_workers` | 8 | Parallel scene reads |

**Add a parcel:** append a box (or a feature to a GeoJSON file) with a new, unique `parcel_id`
(lower-case letters, digits and `-`), then check it with `uv run iris-ndwi parcels --config …`:

```yaml
  - parcel_id: wr-forest-100m
    name: Forest east of the bog (reference)
    kind: reference
    center_lat: 47.92
    center_lon: 9.90
    size_m: 100
    snap_m: 10
```

**Another study area:** for one box or one lon/lat area, use **＋ New analysis** in the dashboard. Otherwise set `study_area`,
the boxes, areas and/or `parcels_geojson`, and the `mgrs_tile` that contains them (or omit `mgrs_tile`). The tile code is in the STAC item properties (`grid:code`), or you can run once: every
item from another tile is listed in `scenes.csv` as `other_mgrs_tile`. Parcels must stay inside one tile
in UTM zone 32 for EPSG:25832 to remain the appropriate metric CRS.
`scripts/fetch_osm_parcels.py` shows how to turn OSM features into a parcel file.

After changing code, run `uv run pytest`. Tests that pin exact numbers document intended behaviour
and must be updated together with a deliberate change.

### Helper scripts

```bash
# NDWI time-series chart (standalone SVG, no plotting dependency)
uv run python scripts/plot_timeseries.py output/<run_id>/observations.csv output/ndwi.svg

# Validation: same acquisitions from both collections must give the same NDWI (~5–10 min)
uv run python scripts/cross_collection_check.py --start 2023-03-01 --end 2023-10-31

# Refresh the committed OSM parcel snapshot (Overpass API)
uv run python scripts/fetch_osm_parcels.py

# Evaluation against published NDWI (see Evaluation)
uv run --group ui --with matplotlib python scripts/jncc_check.py examples/runs/<run> --plot out.png
uv run --with scipy python scripts/la_salada_check.py
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `ERR_CONNECTION_REFUSED` on localhost:8501 | The dashboard isn't running. Start it and keep the terminal open |
| New-analysis map stays empty ("trouble loading the streamlit_folium component") | Two dashboards share port 8501, e.g. the Docker `ui` service and a local `streamlit run`; the page can then load from one and the map from the other. Stop one (`docker compose stop ui` or Ctrl+C) and reload. After pulling new code, restart the dashboard (and `docker compose build` for Docker) |
| `Port 8501 is already in use` | Another copy is running. Use it, or add `--server.port 8502` |
| `warning: VIRTUAL_ENV=… does not match the project environment` | Harmless (an active conda/venv); run `conda deactivate` / `deactivate` to silence it |
| A few `read_error` scenes in `scenes.csv` | Transient HTTP errors from the COG host; they are recorded and the run continues. Re-run to fill them |
| `schema iris_staging does not exist` with `--to-db` | Run the migration first: `docker compose run --rm migrate` (or `uv run alembic upgrade head` with `IRIS_STAGING_DATABASE_URL` set) |
| Export: `schema … does not exist` | Tick *Create PostGIS, the schema and the tables* (CLI: `--create-tables`), or run the migration |
| Export: `parcel … already exists … with a different geometry` | That `parcel_id` is stored with another outline, and older observations refer to it. Export into another schema. (New-analysis boxes from before this check all used the id `point-box`; their ids now include the location and size) |
| Export: `connection failed … Connection refused` from the Docker dashboard | `localhost` is the container itself; use `staging-db` or `host.docker.internal` |

---

## Data provenance

| Item | Value |
|---|---|
| Imagery | Copernicus Sentinel-2 MSI **Level-2A** (bottom-of-atmosphere reflectance, ESA Sen2Cor) |
| Access | Element84 **Earth Search v1** STAC API `https://earth-search.aws.element84.com/v1`. The pipeline reads public Cloud-Optimized GeoTIFFs over HTTPS with range requests and needs no credentials |
| Collections (priority order) | 1. `sentinel-2-c1-l2a`: ESA Collection-1 reprocessing (processing baseline ≥ 05.00, consistent radiometry). 2. `sentinel-2-l2a`: the legacy archive, used only to fill gaps (on Earth Search, C1 lacks Jan–Nov 2022 for this tile) |
| Tile / CRS | MGRS **32UNU**, native EPSG:32632 (UTM 32N / WGS 84) |
| Bands | B03 green and B08 NIR (10 m) for NDWI; B02 blue (10 m) for the fog/haze test; SCL scene classification (20 m) |
| Licence | Copernicus Sentinel data are free and open ([Sentinel data legal notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice)). Outputs contain *modified Copernicus Sentinel data [2021–2026]* |
| Parcels | OpenStreetMap: nature reserve `way/688159107` (NSG 4.035, WDPA 6979) and bog `relation/2958383` (`wetland=bog`), © OpenStreetMap contributors, **ODbL 1.0**. Snapshot committed in [`data/parcels/`](data/parcels/), regenerated with [`scripts/fetch_osm_parcels.py`](scripts/fetch_osm_parcels.py) |
| Stable identifiers | STAC `item_id` for scenes. `parcel_id` (e.g. `wr-nsg-4035`, with an OSM `source_ref`) for parcels. `observation_id = parcel_id:item_id:method_version` for results |

Each run writes a `manifest.json` containing the full configuration and its SHA-256 hash, the software
versions, the licences and the counts. `scenes.csv` lists **every** STAC item found, whether it was used
or rejected (with the reason), and the exact COG URLs that were read.

**Why not Google Earth Engine?** GEE needs a Google account or service-account credentials. That would
break "never include secrets" and stop reviewers running the stack without their own account. It also
runs the processing server-side, which makes per-pixel auditing harder. A source adapter for GEE could
be added behind the same `SceneSource` protocol ([`sources.py`](src/iris_ndwi/sources.py)).

## Study area and parcels

| `parcel_id` | Kind | Area | Purpose |
|---|---|---|---|
| `wr-nsg-4035` | reserve | 1 799 ha | Official nature-reserve boundary (OSM, ref NSG 4.035) |
| `wr-bog-osm` | bog | 1 166 ha | Mapped raised-bog extent; a multipolygon with 22 holes |
| `wr-core-100m` | bog-core | 1.00 ha = **100 px** | 100 m × 100 m on the open Haidgauer Hochmoor surface (47.926242 N, 9.877493 E) |
| `ref-field-100m` | reference | 1.00 ha = **100 px** | 100 m × 100 m on farmland west of the reserve (47.933030 N, 9.842128 E). Shows whether the bog signal differs from ordinary land |

The boxes are built in EPSG:25832 from their WGS 84 centre and snapped to the 10 m grid. A pixel counts
as part of a parcel when its centre falls inside the polygon. With snapping, each box therefore contains
**exactly 100 Sentinel-2 pixels**, which is tested. Both box locations were checked visually on the
true-colour image from 2025-08-13.

## Method

1. **Search** the STAC catalogue for every configured collection, using the bounding box of all
   parcels (WGS 84) and the configured date range.
2. **Select scenes.** Each rule is recorded in the scene ledger:
   - unparseable metadata → `metadata_error`
   - other MGRS tile → `other_mgrs_tile`
   - **duplicates**, meaning the same tile, platform and UTC day. These come from ESA reprocessing, from
     one pass split over two datastrips, or from the same acquisition existing in both collections. The
     pipeline keeps the item with the largest AOI coverage, then the higher collection priority, then
     the highest baseline, then the lowest tile nodata %, then the latest generation time
     → `superseded_by:<item_id>`
   - item-level cloud cover > 80 % → `scene_cloud_cover_above_max`. This is only a pre-filter to save
     downloads; whether a parcel is accepted is decided per pixel.
3. **Read** green, NIR and SCL in one window covering all parcels, in the scene's own grid (EPSG:32632).
   **Reflectances are never resampled.** The window is aligned to the shared 20 m grid, so each SCL
   pixel maps onto exactly 2 × 2 pixels at 10 m.
4. **Radiometric harmonisation.** `reflectance = DN × scale + offset`, using the `raster:bands` metadata of each item.
   Since processing baseline 04.00 (2022-01-25), L2A data carry a +1000 DN offset (−0.1 in reflectance).
   In Earth Search's *legacy* collection, items flagged `earthsearch:boa_offset_applied=true` already have the offset removed
   from their DN, yet their metadata still advertise −0.1. For those items the pipeline applies no offset.
   This was verified on the same acquisition in both forms, 2021-05-31: 03.00 DN 474 vs 05.00 DN 483.
5. **Scene guard.** If more than 50 % of clear pixels have DN below the advertised offset, the offset
   would otherwise be applied twice, so the scene is rejected as `radiometric_offset_inconsistent`. This
   catches legacy items whose flag is wrong. For example, 2022-09-13 had 94 % of clear pixels below the line,
   whereas a dark low-sun winter scene shows about 25 %.
6. **Classify pixels.** The first failing rule wins, and its status code is kept:
   `NODATA` → `SCL_MASKED` (SCL class not in {4 vegetation, 5 not-vegetated, 6 water}) →
   `BRIGHT_SURFACE` (blue reflectance > 0.12) → `NON_POSITIVE_REFLECTANCE` (green or NIR ≤ 0) →
   `ZERO_DENOMINATOR` (green + NIR = 0) → `OUT_OF_RANGE` (|NDWI| > 1).
   The blue test exists because Sen2Cor's SCL sometimes labels valley fog, haze or thin snow as land.
   For example, on 2021-11-12 fog covering the bog was classed SCL 5 and produced an NDWI of −0.12.
   On clear dates the bog and the reference field stay at ≤ 0.07 in B02, while the fog, haze and
   snow cases measured 0.14–0.74. The item's `cloud` probability layer reported 0 % for these cases, so it could not be used instead.
7. **NDWI (McFeeters 1996)** = (B03 − B08) / (B03 + B08). A zero denominator gives NaN, never ±inf, and is counted.
8. **Summarise per parcel and scene.** The pipeline reports pixel counts per status, valid fraction, mean, median, std,
   p25/p75, min/max, and water fraction (share of valid pixels with NDWI > 0). An observation is **accepted** when
   valid fraction ≥ 0.8 (inclusive); otherwise it is rejected as `no_valid_pixels`,
   `valid_fraction_below_min` or `parcel_has_no_pixels`. Statistics of rejected observations are kept for auditing.

All thresholds are set in [`config/wurzacher_ried.yaml`](config/wurzacher_ried.yaml) and validated by a
typed Pydantic model ([`config.py`](src/iris_ndwi/config.py)). Runs are deterministic: the same config and
the same STAC items give byte-identical observations, which is tested.

## Schema mapping (`iris_staging`)

Created by [`alembic/versions/0001_create_iris_staging_ndwi.py`](alembic/versions/0001_create_iris_staging_ndwi.py).
The Alembic version table lives in `iris_staging` as well. Before writing, the writer
([`staging.py`](src/iris_ndwi/staging.py)) checks that every target table is in `iris_staging`. All writes are
idempotent upserts in a single transaction.

The export (`iris-ndwi export`, dashboard *Export to PostGIS*) writes the same four tables into one chosen
schema: the table definitions are renamed with SQLAlchemy's `schema_translate_map`, so nothing can land
outside it, and `iris_core`, `public` and `pg_*` are refused. It reads the run folder (`manifest.json`, the two
CSVs, `parcels.geojson`), so runs can be exported long after they finished. Geometries are stored in EPSG:25832;
a New-analysis run in another UTM zone is reprojected (its `area_m2` stays as measured in its own zone). An
existing `parcel_id` with a different outline (more than 0.5 m Hausdorff distance) stops the export rather than
silently moving the parcel under older observations.

| Table | Key | Content |
|---|---|---|
| `s2_ndwi_run` | `run_id` | timestamp + config hash, source, `synthetic_data`, method version, full manifest (JSONB) |
| `s2_ndwi_parcel` | `parcel_id` | name, kind, source, `source_ref`, licence, area, `MULTIPOLYGON` **EPSG:25832** (CHECK `ST_IsValid`, GiST index) |
| `s2_scene` | `item_id` | collection, acquisition time, platform, tile, baseline, cloud %, offset flag + applied offset, `selected`, `rejection_reason`, COG URLs |
| `s2_ndwi_observation` | `observation_id` (unique `parcel_id, item_id, method_version`) | pixel counts per status (incl. `n_bright_surface`), valid fraction, NDWI statistics, water fraction, `status`, `rejection_reason`. CHECK constraints: counts add up, NDWI in [−1, 1] |

File outputs per run, under `output/<run_id>/`: `observations.csv`, `scenes.csv`, `parcels.geojson` (EPSG:25832),
`manifest.json` and `quality_summary.md`. Validation SQL is in [`examples/validation_queries.sql`](examples/validation_queries.sql).

## Tests

`uv run pytest` runs 131 offline tests in under a second. `docker compose run --rm tests` runs all 133, adding two PostGIS
round trips: the `iris_staging` writer (SRID 25832, geometry validity, idempotent upserts; it deletes its own rows) and
the export into a fresh schema (tables created on request only, idempotent, refuses to move an existing parcel; the
schema is dropped afterwards).
Synthetic scene ids carry a `SYNTHETIC_` prefix, so test data can never overwrite real rows. The tests cover:

- **NDWI:** known values; zero denominator (NaN, no warning); negative reflectance; out-of-range values;
  nodata priority; every SCL class; fog labelled as land (blue test, threshold boundary, rule order).
- **Temporal comparability:** the same surface before and after baseline 04.00 gives the same NDWI, and
  skipping the offset demonstrably biases NDWI.
- **Scene selection, using recorded real STAC items** ([`tests/fixtures/`](tests/fixtures/)):
  reprocessed duplicates 1 ms apart; a split-datastrip pair; collection priority; the legacy offset flag;
  missing assets or metadata.
- **Geometry:** a 100 m box gives exactly 100 pixels; self-intersections are repaired; empty, line or
  zero-area geometries are rejected; OSM multipolygon holes are preserved; duplicate IDs are rejected.
- **End to end (synthetic scenes):** snow, fog missed by SCL, half cloud, tile-edge nodata, read error,
  wrong tile, cloud pre-filter, double offset, an empty period, and identical results on repeated runs.
- **New analysis and re-runs:** metric CRS per UTM zone, automatic tile choice from real STAC items, box
  size, a complete point run written to disk, and re-runs with new dates or a resized box. Areas: coordinates
  in decimal and degree-minute notation (and invalid ones), area size against the geodesic area, the
  400 km² limit, a complete area run and its re-run, and an area outside every tile.
- **PostGIS export:** `iris_core`, `public` and invalid schema names are refused; run-folder rows get the
  right types (empty CSV cells become NULL); a box in another UTM zone is reprojected to EPSG:25832.

## Evaluation

To test the work beyond the unit tests, we compared the pipeline's NDWI with independent, published
values at the same places and dates. All three references use the same index as this pipeline,
McFeeters' green/NIR NDWI = (B03 − B08) / (B03 + B08) from Sentinel-2.

| Reference | Type | Place and dates | Result |
|---|---|---|---|
| [JNCC Sentinel-2 ARD NDWI v2](https://catalogue.ceda.ac.uk/uuid/9ae429e3026f4903a338f6baa0a11035) | national NDWI product, time series | Thorne Moors (peat bog) and Hornsea Mere (lake), England, 2019–2023 | **Peat bog: mean difference +0.011, RMSE 0.040, all 131 dates within ±0.10.** Lake: same course over time (r = 0.72), but +0.24 higher |
| Santecchia et al. (2023) | peer-reviewed paper, one date | La Salada lake, Argentina, 12 April 2019 | Shoreline pixels negative over the same range (land side −0.71 to 0.00 vs paper −0.64 to −0.01); open water +0.93 to +0.99 |
| Sahour et al. (2022) | peer-reviewed paper, time-series figure | Prairie pothole, North Dakota, 2017–2018 | Same wet and dry phases (peak +0.81 in Nov 2017, dry from mid-2018); our series is sparser |

### 1. Time series against the JNCC NDWI product (England)

The Joint Nature Conservation Committee (JNCC) publishes NDWI for every Sentinel-2 acquisition over England and
Scotland since 2015 (10 m, EPSG:27700, Open Government Licence). It is computed from the Defra/JNCC surface
reflectance product, so it shares the satellite and the formula with this pipeline but not the atmospheric
correction (Sen2Cor here) or the processing code.

**Method.** A 100 m box was run through the pipeline for 2019-01-01 to 2023-12-31 at two sites: Thorne Moors, a
lowland raised bog under restoration (53.632 N, 0.905 W), and Hornsea Mere, a shallow lake (53.904 N, 0.190 W).
For every date the pipeline accepted, [`scripts/jncc_check.py`](scripts/jncc_check.py) reads the JNCC file of the
same day and takes the median of its pixels inside the same box. JNCC v2 files have no cloud mask, so only dates
that passed our SCL mask are compared.

![Time series of this pipeline and JNCC NDWI at Thorne Moors and Hornsea Mere](examples/validation/jncc_comparison.png)

| Site | Dates compared | Mean difference (ours − JNCC) | MAE | RMSE | Pearson r | Within ±0.05 | Within ±0.10 |
|---|---|---|---|---|---|---|---|
| Thorne Moors (peat bog) | 131 of 134 | **+0.011** | 0.034 | **0.040** | 0.81 | 76 % | **100 %** |
| Hornsea Mere (lake) | 138 of 139 | +0.239 | 0.247 | 0.307 | 0.72 | 5 % | 17 % |

**Peat bog.** Both series lie between −0.5 and −0.8 and follow the same seasonal course date by date, with
almost no offset. For the surface this project is built for, the pipeline agrees with an independent product to
within the uncertainty of L2A reflectance (see [Assumptions and limitations](#assumptions-and-limitations)).

**Lake.** The two series rise and fall together, but ours is about 0.2 higher on almost every date. The offset is
the same in every year and season, and in both the legacy and the Collection-1 data, so it does not come from
individual scenes. The likely cause is atmospheric correction over dark water: water reflects almost no NIR, so
the NDWI denominator is small and a NIR difference of 0.01 between the two corrections moves NDWI by 0.1–0.3. This
explanation has not yet been checked against JNCC's reflectance bands.

The same run in the dashboard (open **Run → Thorne Moors (JNCC check)**):

![Dashboard with the Thorne Moors run: satellite map with the 100 m box and the NDWI time series 2019–2023](examples/validation/dashboard_thorne_moors.png)

### 2. One date against Santecchia et al. (2023), La Salada lake (Argentina)

The paper compares water indices on La Salada lake (39°28′ S, 62°42′ W) with a drone survey, using the same
Sentinel-2B L2A acquisition of 12 April 2019 (tile 20HNB). It reports NDWI only for the pixels on the lake
shoreline: "McFeeters values are all negative, between −0.64 and −0.01". [`scripts/la_salada_check.py`](scripts/la_salada_check.py)
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

### 3. Prairie pothole from Sahour et al. (2022), North Dakota (USA)

Figure 4 of the paper shows NDWI time series of two potholes but gives no coordinates. The points were located
by matching the paper's Figure 4A against Sentinel-2 imagery of the study area (tile 13UFQ, near Bowbells): the
match scored 0.69, against 0.32 for the next-best location. A 20 m box was placed on the pond at Point 1
(48.76884 N, 102.94572 W) for 2017–2018 ([example run](examples/runs/20261028T222139Z-8d7e9f63)).

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

### What the evaluation shows

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

### Reproduce

```bash
# JNCC comparison: CSV per site, summary, and the chart above (~5 min, reads JNCC files over HTTP)
uv run --group ui --with matplotlib python scripts/jncc_check.py \
    examples/runs/20261029T085700Z-21efb7e2 examples/runs/20261029T085700Z-f03b45a9 \
    --plot examples/validation/jncc_comparison.png

# La Salada shoreline and open-water statistics (~1 min)
uv run --with scipy python scripts/la_salada_check.py
```

Results are in [`examples/validation/`](examples/validation/): `jncc_thorne_moors.csv` and `jncc_hornsea_mere.csv`
(one row per date, both values and the JNCC file used), `jncc_summary.csv` and `la_salada_shoreline.csv`.

### References

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

## Assumptions and limitations

- **What NDWI measures here.** Green/NIR NDWI was designed to detect **open water**. Over peat vegetation it
  responds mainly to surface wetness and canopy state, and it is **not a calibrated soil-moisture
  measurement**. For vegetation water content, NDMI (B08/B11) is usually more sensitive; it would be a
  small addition but was not requested. The water threshold NDWI > 0 is McFeeters' generic value and has
  not been calibrated for this bog.
- **Uncertainty.** L2A surface-reflectance accuracy is roughly ±0.01–0.02 in reflectance units, which
  translates to a few hundredths in NDWI for this surface. A 100-pixel box is sensitive to individual
  mixed or misclassified pixels, so the p25/p75 spread is reported alongside the median.
- **Masks.** SCL comes from Sen2Cor and can miss thin cloud or shadow edges, or mislabel snow. It is
  20 m, so each SCL pixel masks four 10 m pixels. No buffer is added around clouds. The blue-brightness
  test (0.12) is a simple empirical threshold derived from this site. It can miss thin, dark haze, and
  it would wrongly mask genuinely bright bare surfaces, which don't occur in these parcels.
- **Comparing over time.** Compare like with like: the same season (see the summer table in the report)
  and accepted observations only. Sun elevation varies from about 18° (Dec) to 65° (Jun) and is recorded per scene.
- **Collection gap.** Most of 2022 comes from the legacy collection and has different file lineage. The
  cross-collection checks in [`examples/`](examples/) quantify how well the two agree.
- **Parcel boundaries.** The OSM features are community-mapped, not the official LUBW cadastre. About 1 % of
  the OSM bog polygon (12 ha) lies outside the NSG boundary.
- **Dark open water.** With the −0.1 BOA offset, NIR over clear, dark water often reaches zero or below, so such
  pixels are counted as invalid and lake scenes can be rejected as `radiometric_offset_inconsistent`. Absolute NDWI
  over water also depends on the atmospheric correction (about +0.2 against JNCC's product). Peat vegetation is not
  affected. See [Evaluation](#evaluation).
- **Single tile.** Only tile 32UNU is used. Parcels crossing a tile edge would get `nodata` pixels; they are
  not mosaicked across tiles.

## Repository layout

```
src/iris_ndwi/   config · parcels · scenes · sources (STAC/COG) · ndwi · zonal · pipeline · outputs · staging · report · interpret · synthetic · adhoc · cli
ui/              Streamlit dashboard (optional `ui` dependency group)
alembic/         iris_staging migration
config/          example configuration (no secrets)
data/parcels/    committed OSM parcel snapshot (ODbL)
scripts/         fetch_osm_parcels.py · cross_collection_check.py · plot_timeseries.py · jncc_check.py · la_salada_check.py
tests/           pytest suite + recorded real STAC fixtures
examples/        real 2021–2026 run, evaluation runs, cross-collection checks, validation SQL
examples/validation/  evaluation against published NDWI: tables, comparison chart, dashboard screenshot
```
