"""IRIS NDWI dashboard — view of a pipeline run, plus ad-hoc runs for any point.

    uv run --group ui streamlit run ui/app.py

Reads a run folder written by `iris-ndwi run` (observations.csv, scenes.csv,
parcels.geojson, manifest.json); viewing needs no database or network access.
"New analysis" runs the same pipeline live for a box around a clicked point
and writes a new run folder to output/ (network access to Earth Search needed).
"""

from __future__ import annotations

import json
import math
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pydantic import ValidationError
from shapely.geometry import mapping, shape
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from streamlit_folium import st_folium

from iris_ndwi.adhoc import (
    MAX_AREA_KM2, area_config, is_single_box, metric_crs, parse_coordinate, point_config, rerun_config, run_and_write,
)  # fmt: skip
from iris_ndwi.config import load_config
from iris_ndwi.interpret import Obs, interpret
from iris_ndwi.parcels import GeometryError, load_parcels, lonlat_rectangle, reproject, square_box
from iris_ndwi.staging import DATABASE_URL_ENV, STAGING_SCHEMA, StagingGuardError, engine_for, export_run_dir

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
RUN_ROOTS = [OUTPUT_DIR, ROOT / "examples" / "runs"]
BASE_CONFIG = ROOT / "config" / "wurzacher_ried.yaml"  # quality settings reused by new analyses
DEFAULT_POINT = (47.926242, 9.877493)  # Haidgauer Hochmoor open bog
AREA_EXAMPLE = {"area_west": "9°51′ E", "area_east": "9°55′ E", "area_south": "47°54′ N", "area_north": "47°57′ N"}  # Wurzacher Ried
CLICK_AREA = (0.05, 0.03)  # lon, lat extent of an area placed by a click (about 4 km × 3 km in Germany)
ESRI_IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
AREA_MODE = "Area (lon/lat range)"
S2_FIRST_DATE = date(2017, 1, 1)  # Sentinel-2 L2A archive is sparse before this

PRIMARY, TEXT_MUTED, BORDER = "#2563EB", "#64748B", "#E5E7EB"
PARCEL_COLOURS = {"bog-core": "#2563EB", "bog": "#16A34A", "reserve": "#94A3B8", "reference": "#DC2626", "point": "#7C3AED", "area": "#7C3AED"}
NDWI_RANGE = (-0.80, -0.45)  # colour scale for the map; values outside are clipped

st.set_page_config(page_title="IRIS · Peatland NDWI", page_icon="🛰️", layout="wide")
st.markdown(
    f"""
    <style>
      [data-testid="stSidebar"] {{ background-color: #0B0F17; }}
      [data-testid="stSidebar"] * {{ color: #E2E8F0; }}
      [data-testid="stSidebar"] label {{ font-size: 11px !important; color: #94A3B8 !important; }}
      [data-testid="stSidebar"] div[data-baseweb="select"] > div {{ background-color: #111827 !important;
          border-color: rgba(255,255,255,0.12) !important; }}
      [data-testid="stSidebar"] div[data-baseweb="select"] * {{ color: #F8FAFC !important; }}
      [data-testid="stSidebar"] button * {{ color: #000000 !important; }}
      [data-testid="stSidebar"] :is([data-testid="stDateInput"], [data-testid="stNumberInput"], [data-testid="stTextInput"]) input {{
          color: #000000 !important; -webkit-text-fill-color: #000000; }}
      [data-testid="stSidebar"] span[data-baseweb="tag"] {{ background-color: {PRIMARY} !important; }}
      div[data-testid="stMetricValue"] > div {{ font-size: 1.55rem; }}
      div[data-testid="stMetric"] {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 10px 14px; }}
      div[data-testid="stMetricLabel"] p {{ font-size: 12px; color: {TEXT_MUTED}; }}
      .block-container {{ padding-top: 1.6rem; }}
      [data-testid="stAppDeployButton"], [data-testid="stMainMenu"] {{ display: none !important; }}
      .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def find_runs() -> list[Path]:
    runs = [p for root in RUN_ROOTS if root.exists() for p in root.iterdir() if (p / "manifest.json").exists()]
    return sorted(runs, key=lambda p: p.name, reverse=True)


@st.cache_data(show_spinner=False)
def run_label(run_dir: str) -> str:
    run = Path(run_dir)
    area = json.loads((run / "manifest.json").read_text())["config"]["study_area"]
    example = " (example)" if run.parent.name == "examples" or run.parent.parent.name == "examples" else ""
    return f"{run.name[:8]}-{run.name[9:13]} · {area}{example}"


@st.cache_data(show_spinner=False)
def load_run(run_dir: str) -> dict:
    run = Path(run_dir)
    obs = pd.read_csv(run / "observations.csv")
    scenes = pd.read_csv(run / "scenes.csv", dtype={"processing_baseline": str})  # keep "05.13", not 5.13
    # timestamps mix second and microsecond precision, so parse as ISO 8601 explicitly
    for frame in (obs, scenes):
        frame["acquired_at"] = pd.to_datetime(frame["acquired_at"], utc=True, format="ISO8601")
    scenes["selected"] = scenes["selected"].astype(str).eq("True")
    manifest = json.loads((run / "manifest.json").read_text())
    collection = json.loads((run / "parcels.geojson").read_text())
    crs = "EPSG:" + collection["crs"]["properties"]["name"].rsplit(":", 1)[-1]
    parcels = []
    for feature in collection["features"]:
        geometry = reproject(shape(feature["geometry"]), crs, "EPSG:4326")
        parcels.append({**feature["properties"], "geometry": geometry})
    return {"obs": obs, "scenes": scenes, "manifest": manifest, "parcels": parcels}


def to_obs(frame: pd.DataFrame) -> list[Obs]:
    def value(v):
        return None if pd.isna(v) else float(v)

    return [
        Obs(
            acquired_at=row.acquired_at.to_pydatetime(),
            status=row.status,
            ndwi_median=value(row.ndwi_median),
            ndwi_p25=value(row.ndwi_p25),
            ndwi_p75=value(row.ndwi_p75),
            valid_fraction=float(row.valid_fraction),
            rejection_reason=None if pd.isna(row.rejection_reason) else row.rejection_reason,
        )
        for row in frame.itertuples()
    ]


def ndwi_colour(value: float | None) -> list[int]:
    """Brown (drier) -> blue (wetter); grey when there is no accepted value."""
    if value is None or pd.isna(value):
        return [160, 160, 160, 90]
    low, high = NDWI_RANGE
    t = min(max((value - low) / (high - low), 0.0), 1.0)
    dry, wet = (166, 97, 26), (37, 99, 235)
    return [round(d + (w - d) * t) for d, w in zip(dry, wet)] + [170]


def base_map() -> folium.Map:
    """Satellite and street basemaps only; the view and the shapes go to st_folium separately.

    st_folium rebuilds the whole map whenever this script changes, so nothing that moves
    is part of it. Only Leaflet is loaded: folium also asks for jQuery, Bootstrap and icon
    fonts, which these maps do not use, and the map stays empty if one of them fails to load.
    """
    fmap = folium.Map(location=DEFAULT_POINT, zoom_start=15, tiles=None, control_scale=True)
    fmap.default_js = [js for js in fmap.default_js if js[0] == "leaflet"]
    fmap.default_css = [css for css in fmap.default_css if css[0] == "leaflet_css"]
    folium.TileLayer("OpenStreetMap", name="Map", show=False).add_to(fmap)
    folium.TileLayer(ESRI_IMAGERY, attr="Esri World Imagery", name="Satellite").add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap


def fit_zoom(bounds: tuple[float, float, float, float], max_zoom: int) -> int:
    """Largest Leaflet zoom at which lon/lat bounds fit a map of about 560 × 440 px."""
    min_x, min_y, max_x, max_y = bounds
    stretch = 1 / math.cos(math.radians((min_y + max_y) / 2))  # Web Mercator
    px_per_degree = min(560 / max(max_x - min_x, 1e-9), 440 / max((max_y - min_y) * stretch, 1e-9))
    return max(2, min(max_zoom, math.floor(math.log2(px_per_degree * 360 / 256))))


# ---------------------------------------------------------------------------
# New analysis: any point, any dates
# ---------------------------------------------------------------------------


def new_analysis_page() -> None:
    state = st.session_state
    lat, lon = state.get("point") or (None, None)  # None until the map is clicked or a value is typed
    busy = state.get("running", False)  # set by the Run button; cleared when the run ends

    st.markdown("## New analysis")
    st.caption(
        "Place a square box around a point, or enter an area as a longitude/latitude range; choose any period, "
        "and run the same pipeline (same masks and quality rules as the tested runs) on live Sentinel-2 L2A data "
        "from Earth Search."
    )
    # The area texts are kept outside the inputs, which Streamlit forgets while point mode is shown.
    # A map click moves the area; it is applied here, before the inputs are drawn.
    pending = state.pop("pending_area", {})
    area_text = state.area_text = {**dict.fromkeys(AREA_EXAMPLE, ""), **state.get("area_text", {}), **pending}
    if pending or not state.get("area_inputs_shown"):  # restore after point mode, or move after a click
        for key, value in area_text.items():
            state[key] = value
    map_col, form_col = st.columns([7, 5], gap="medium")

    with form_col:
        if message := state.pop("run_message", None):
            (st.error if message[0] == "error" else st.warning)(message[1])
        area_mode = st.radio("Study area", ["Point + box", AREA_MODE], horizontal=True, key="study_mode",
                             disabled=busy) == AREA_MODE  # fmt: skip
        state.area_inputs_shown = area_mode
        name = st.text_input("Name", value="My area" if area_mode else "My box", max_chars=60)
        problem = hint = None
        west = None
        if area_mode:
            c1, c2 = st.columns(2)
            texts = {key: (c1 if i % 2 == 0 else c2).text_input(label, key=key, placeholder=AREA_EXAMPLE[key])
                     for i, (key, label) in enumerate([
                         ("area_west", "West (longitude)"), ("area_east", "East (longitude)"),
                         ("area_south", "South (latitude)"), ("area_north", "North (latitude)"),
                     ])}  # fmt: skip
            state.area_text = texts
            if not any(text.strip() for text in texts.values()):
                hint = "Click the map to place an area, or enter its four edges."
            else:
                try:
                    west, east = (parse_coordinate(texts[k], "lon") for k in ("area_west", "area_east"))
                    south, north = (parse_coordinate(texts[k], "lat") for k in ("area_south", "area_north"))
                    lat, lon = (south + north) / 2, (west + east) / 2
                except ValueError as exc:
                    problem, west = str(exc), None
            st.caption("Decimal degrees (131.4833) or degrees and minutes (131°29′ E, 46 28 N); minus or W/S for west/south.")
        else:
            size_m = st.number_input("Box edge (m)", min_value=20, max_value=2000, value=100, step=10,
                                     help="Snapped to the 10 m Sentinel-2 pixel grid")  # fmt: skip
            c1, c2 = st.columns(2)
            lat = c1.number_input("Latitude", min_value=-56.0, max_value=84.0, value=lat, format="%.6f",
                                  placeholder="e.g. 47.926242")  # fmt: skip
            lon = c2.number_input("Longitude", min_value=-180.0, max_value=180.0, value=lon, format="%.6f",
                                  placeholder="west is negative")  # fmt: skip
            state.point = (lat, lon)
            if lat is None or lon is None:
                hint = "Click the map to place the box, or enter a latitude and longitude."
        today = datetime.now(timezone.utc).date()
        period = st.date_input(
            "Period", value=(today - timedelta(days=180), today),
            min_value=S2_FIRST_DATE, max_value=today, format="YYYY-MM-DD",
        )  # fmt: skip

        config = None
        if problem is None and not (isinstance(period, tuple) and len(period) == 2):
            problem = "Choose a start and an end date."
        if problem is None and hint is None:
            label = name.strip() or ("My area" if area_mode else "My box")
            try:  # no network: the configuration and the parcel are only checked here
                if area_mode:
                    config = area_config(load_config(BASE_CONFIG), west, south, east, north, *period, label)
                else:
                    config = point_config(load_config(BASE_CONFIG), lat, lon, size_m, *period, label)
            except ValidationError as exc:
                problem = exc.errors()[0]["msg"].removeprefix("Value error, ")
            except (GeometryError, ValueError) as exc:
                problem = str(exc)
        if problem:
            st.error(problem)
        elif hint:
            st.info(hint)
        elif area_mode:
            km2 = load_parcels(config)[0].area_m2 / 1e6
            st.caption(
                f"Area {km2:,.1f} km² (limit {MAX_AREA_KM2} km²) · metric CRS {config.target_crs}. A scene counts only "
                "when at least 80 % of the area is clear, which gets rarer the larger the area. A few months take "
                "about a minute per 100 km²."
            )
        else:
            st.caption(
                f"Metric CRS: {metric_crs(lat, lon)}. Every acquisition in the period (one every 2–5 days) is read "
                "from the cloud, so a few months take about a minute and several years can take 10 minutes or more."
            )
        st.button("Run analysis", type="primary", use_container_width=True, disabled=busy or config is None,
                  on_click=lambda: state.update(running=True))  # fmt: skip
        if busy:  # grey the form out and put a loading spinner over it
            st.markdown(
                """<div class="run-overlay"></div><style>
                div[data-testid="stColumn"]:has(.run-overlay) { position: relative; align-self: flex-start; }
                div[data-testid="stColumn"]:has(.run-overlay)::after {
                    content: "Running analysis …"; position: absolute; inset: -8px; z-index: 1000;
                    background: rgba(100, 116, 139, 0.75); border-radius: 12px;
                    display: flex; align-items: center; justify-content: center;
                    padding-top: 110px; color: #0F172A; font-weight: 600; cursor: wait; }
                div[data-testid="stColumn"]:has(.run-overlay)::before {
                    content: ""; position: absolute; left: 50%; top: calc(50% - 28px); z-index: 1001;
                    width: 56px; height: 56px; margin: -28px 0 0 -28px; border-radius: 50%;
                    border: 6px solid rgba(255, 255, 255, 0.7); border-top-color: #0F172A;
                    animation: run-spin 0.9s linear infinite; }
                @keyframes run-spin { to { transform: rotate(360deg); } }
                </style>""",
                unsafe_allow_html=True,
            )

    with map_col:
        box = None
        try:
            if area_mode and west is not None:
                box = lonlat_rectangle(west, south, east, north, "EPSG:4326")
            elif not area_mode and lat is not None and lon is not None:
                box = reproject(square_box(lat, lon, size_m, 10, metric_crs(lat, lon)), metric_crs(lat, lon), "EPSG:4326")
        except Exception:  # noqa: BLE001 - preview only
            pass
        selection = folium.FeatureGroup(name="Selection")
        if box is not None:
            state.map_centre = (lat, lon)
            folium.GeoJson(mapping(box), style_function=lambda _: {"color": "#FFFFFF", "fillColor": "#FFFFFF", "weight": 2, "fillOpacity": 0.15}).add_to(selection)
            if not area_mode:
                # A CSS dot: no marker image to download, and a CircleMarker breaks st_folium's layer updates
                dot = '<div style="width:10px;height:10px;border-radius:50%;background:#2563EB;border:2px solid #FFFFFF"></div>'
                folium.Marker([lat, lon], icon=folium.DivIcon(html=dot, icon_size=(14, 14), icon_anchor=(7, 7)),
                              tooltip=f"{lat:.6f}, {lon:.6f}").add_to(selection)  # fmt: skip
        clicked = st_folium(
            base_map(), key="picker", height=480, use_container_width=True, returned_objects=["last_clicked"],
            center=state.get("map_centre", DEFAULT_POINT), zoom=None if box is None else fit_zoom(box.bounds, 16),
            feature_group_to_add=selection if box is not None else None,
        )  # fmt: skip
        click = (clicked or {}).get("last_clicked")
        if click and click != state.get("last_click"):
            state.last_click = click
            if not area_mode:
                state.point = (round(click["lat"], 6), round(click["lng"], 6))
            else:  # same size and a new centre, or a new area of the default size
                half_x, half_y = ((east - west) / 2, (north - south) / 2) if west is not None else (CLICK_AREA[0] / 2, CLICK_AREA[1] / 2)
                state.pending_area = {
                    "area_west": f"{click['lng'] - half_x:.5f}", "area_east": f"{click['lng'] + half_x:.5f}",
                    "area_south": f"{click['lat'] - half_y:.5f}", "area_north": f"{click['lat'] + half_y:.5f}",
                }  # fmt: skip
            st.rerun()
        st.caption(
            f"Click to place the {'area' if area_mode else 'box'}; a new click replaces it. Switch to the street map at the top right. "
            "If the map stays empty, check that only one dashboard runs on port 8501 (e.g. `docker compose stop ui` "
            "when using the local one) and reload. Latitude and longitude can always be typed in."
        )

    if not busy:
        return
    # A message is kept in session state because the rerun that re-enables the form clears the page.
    state.running = False
    if config is None:  # the form shows why
        st.rerun()
    try:
        result, run_dir = run_and_write(config, OUTPUT_DIR)
    except (GeometryError, ValueError, OSError, RuntimeError) as exc:
        state.run_message = ("error", f"Run failed: {exc}")
        st.rerun()
    if not result.observations:
        state.run_message = ("warning", f"Run `{result.run_id}` finished, but no scene could be used "
                             f"({result.n_items_found} items found). Try a longer period or another place.")  # fmt: skip
        st.rerun()
    state.run_dir = run_dir  # open the new run in the results view
    state.new_analysis = False
    st.rerun()


# ---------------------------------------------------------------------------
# Manage a run: re-run with new dates, delete
# ---------------------------------------------------------------------------


def is_own_run(run_dir: Path) -> bool:
    """Runs in output/ can be deleted; the examples belong to the repository."""
    return run_dir.resolve().parent == OUTPUT_DIR.resolve() and (run_dir / "manifest.json").is_file()


def delete_run(run_dir: Path) -> None:  # button callback: runs before the script, so it may reset widgets
    state = st.session_state
    state.pop("confirm_delete", None)
    if not is_own_run(run_dir):
        state.manage_message = ("error", "Only runs in output/ can be deleted.")
        return
    shutil.rmtree(run_dir)
    state.pop("run_dir", None)  # fall back to the newest remaining run
    state.manage_message = ("success", f"Deleted run {run_dir.name}.")


def manage_run(run_dir: Path, manifest: dict) -> None:
    state = st.session_state
    request = state.get("rerun_request")
    cfg = manifest["config"]
    with st.expander("Manage this run", expanded=bool(request) or "confirm_delete" in state):
        if message := state.pop("manage_message", None):
            getattr(st, message[0])(message[1])

        today = datetime.now(timezone.utc).date()
        period_key = f"rerun_period_{run_dir.name}"
        st.date_input(
            "New period", value=(date.fromisoformat(cfg["start_date"]), min(date.fromisoformat(cfg["end_date"]), today)),
            min_value=S2_FIRST_DATE, max_value=today, format="YYYY-MM-DD", key=period_key,
        )  # fmt: skip
        size_key = f"rerun_size_{run_dir.name}"
        resizable = is_single_box(cfg)
        if resizable:
            st.number_input("Box edge (m)", min_value=20, max_value=2000, step=10, key=size_key,
                            value=int(cfg["boxes"][0]["size_m"]), help="Same centre point; snapped to the 10 m grid")  # fmt: skip
        synthetic = manifest["synthetic_data"]
        st.button(
            "Re-run with these settings" if resizable else "Re-run with new dates",
            use_container_width=True, disabled=bool(request) or synthetic,
            on_click=lambda: state.update(rerun_request=(run_dir, state[period_key], state.get(size_key) if resizable else None)),
            help="Same parcels and settings as this run, except what you changed above. The result is a new run.",
        )  # fmt: skip
        st.caption("Synthetic test runs can't be re-run here." if synthetic else
                   "Re-runs read every scene again: a few months take about a minute, several years 10 minutes or more.")  # fmt: skip

        if not is_own_run(run_dir):
            st.caption("Example runs belong to the repository and can't be deleted here.")
        elif state.get("confirm_delete") == str(run_dir):
            st.warning(f"Delete **{run_label(str(run_dir))}**? Its folder in output/ is removed; this can't be undone.")
            yes, no = st.columns(2)
            yes.button("Delete", type="primary", use_container_width=True, on_click=delete_run, args=(run_dir,))
            no.button("Cancel", use_container_width=True, on_click=lambda: state.pop("confirm_delete", None))
        else:
            st.button("Delete this run", use_container_width=True, disabled=bool(request),
                      on_click=lambda: state.update(confirm_delete=str(run_dir)))  # fmt: skip

    if not request:
        return
    # The run happens after the buttons are drawn, so they show as disabled while it is busy.
    del state["rerun_request"]
    old_dir, period, size_m = request
    if not isinstance(period, tuple) or len(period) != 2:
        state.manage_message = ("error", "Choose a start and an end date.")
        st.rerun()
    start, end = period
    try:
        config = rerun_config(load_run(str(old_dir))["manifest"]["config"], start, end, ROOT, size_m)
        with st.spinner(f"Re-running {start} → {end} …"):
            result, new_dir = run_and_write(config, OUTPUT_DIR)
    except (GeometryError, ValueError, OSError, RuntimeError) as exc:
        state.manage_message = ("error", f"Re-run failed: {exc}")
        st.rerun()
    if not result.observations:
        state.manage_message = ("warning", f"Re-run `{result.run_id}` finished, but no scene could be used.")
    state.pending_run_dir = new_dir  # selected on the next pass, before the Run selectbox is drawn
    st.rerun()


def export_panel(run_dir: Path) -> None:
    """Upsert the selected run into a PostGIS database; the URL is only kept in this browser session."""
    state = st.session_state
    with st.expander("Export to PostGIS", expanded="export_message" in state):
        if message := state.pop("export_message", None):
            getattr(st, message[0])(message[1])
        with st.form("export_form", border=False):
            url = st.text_input(
                "Database URL", type="password", placeholder="postgresql://user:password@host:5432/database",
                help=f"Leave empty to use ${DATABASE_URL_ENV}. Not saved to disk.",
            )  # fmt: skip
            schema = st.text_input("Schema", value=STAGING_SCHEMA, help="iris_core and public are refused")
            create = st.checkbox("Create PostGIS, the schema and the tables if missing", value=True)
            submitted = st.form_submit_button("Export this run", use_container_width=True)
        st.caption("Upserts run, parcels (EPSG:25832), scenes and observations; exporting again updates the same rows.")
    if not submitted:
        return
    url = url.strip() or os.environ.get(DATABASE_URL_ENV, "")
    if not url:
        state.export_message = ("error", f"Enter a database URL (or set {DATABASE_URL_ENV}).")
        st.rerun()
    try:
        engine = engine_for(url, schema.strip())
        with st.spinner("Exporting …"):
            counts = export_run_dir(run_dir, engine, create_tables=create)
    except ArgumentError:  # its message would repeat the URL, password included
        state.export_message = ("error", "That is not a database URL, e.g. postgresql://user:password@host:5432/database")
    except (StagingGuardError, SQLAlchemyError, ValueError) as exc:
        detail = getattr(exc, "orig", None) or exc
        state.export_message = ("error", f"Export failed: {str(detail).strip().splitlines()[0]}")
    else:
        target = f"{engine.url.host or 'localhost'}/{engine.url.database} · {schema.strip()}"
        state.export_message = ("success", f"Exported to {target}: {counts['parcels']} parcels, {counts['scenes']} scenes, "
                                           f"{counts['observations']} observations.")  # fmt: skip
    st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

if "new_analysis" not in st.session_state:
    st.session_state.new_analysis = False
runs = find_runs()

with st.sidebar:
    st.markdown("### 🛰️ IRIS · Peatland NDWI")
    if st.session_state.new_analysis:
        if runs and st.button("← Back to results", use_container_width=True):
            st.session_state.new_analysis = False
            st.rerun()
    elif st.button("＋ New analysis", use_container_width=True, help="Any point on the map, any dates"):
        st.session_state.new_analysis = True
        # Start with nothing selected; the map opens where the last selection was.
        st.session_state.point = None
        st.session_state.area_text = {}
        st.rerun()

if st.session_state.new_analysis or not runs:
    new_analysis_page()
    st.stop()
st.session_state.area_inputs_shown = False  # the area inputs are forgotten while the results are shown

with st.sidebar:
    if "pending_run_dir" in st.session_state:
        st.session_state.run_dir = st.session_state.pop("pending_run_dir")
    if st.session_state.get("run_dir") not in runs:
        st.session_state.run_dir = runs[0]
    run_dir = st.selectbox("Run", runs, key="run_dir", format_func=lambda p: run_label(str(p)))
    data = load_run(str(run_dir))
    obs_all, scenes, manifest, parcels = data["obs"], data["scenes"], data["manifest"], data["parcels"]
    manage_run(run_dir, manifest)
    export_panel(run_dir)
    if obs_all.empty:
        st.warning("This run has no observations.")
        st.stop()
    parcel_by_id = {p["parcel_id"]: p for p in parcels}

    parcel_ids = sorted(parcel_by_id, key=lambda i: list(PARCEL_COLOURS).index(parcel_by_id[i]["kind"]) if parcel_by_id[i]["kind"] in PARCEL_COLOURS else 9)
    parcel_id = st.selectbox("Parcel", parcel_ids, format_func=lambda i: parcel_by_id[i]["name"])

    first, last = obs_all["acquired_at"].min().date(), obs_all["acquired_at"].max().date()
    date_from, date_to = st.slider("Period", min_value=first, max_value=last, value=(first, last), format="YYYY-MM")
    months = st.multiselect(
        "Months", list(range(1, 13)), default=list(range(1, 13)),
        format_func=lambda m: datetime(2000, m, 1).strftime("%b"),
    )  # fmt: skip
    show_rejected = st.toggle("Show rejected observations", value=True)

    in_period = obs_all[
        (obs_all["acquired_at"].dt.date >= date_from)
        & (obs_all["acquired_at"].dt.date <= date_to)
        & (obs_all["acquired_at"].dt.month.isin(months))
    ]
    parcel_obs = in_period[in_period["parcel_id"] == parcel_id].sort_values("acquired_at")

    scene_options = parcel_obs.sort_values("acquired_at", ascending=False)
    accepted_scenes = scene_options[scene_options["status"] == "accepted"]
    default_item = accepted_scenes["item_id"].iloc[0] if not accepted_scenes.empty else None
    labels = {
        r.item_id: f"{r.acquired_at:%Y-%m-%d} · {'✔' if r.status == 'accepted' else '✖'} {r.item_id}"
        for r in scene_options.itertuples()
    }
    item_ids = list(labels)
    item_id = st.selectbox(
        "Scene (map)", item_ids, index=item_ids.index(default_item) if default_item in item_ids else 0,
        format_func=labels.get,
    ) if item_ids else None  # fmt: skip

    st.caption(
        f"Source: {manifest['source']} · {', '.join(manifest['collections'])}  \n"
        f"Method: {manifest['method_version']}  \nConfig SHA-256: {manifest['config_sha256'][:12]}…"
    )


# ---------------------------------------------------------------------------
# Header + KPIs
# ---------------------------------------------------------------------------

cfg = manifest["config"]
st.markdown(f"## Sentinel-2 Peatland Moisture (NDWI) — {cfg['study_area']}")
badge = (
    '<span class="badge" style="background:#FEE2E2;color:#B91C1C">SYNTHETIC DATA — not real imagery</span>'
    if manifest["synthetic_data"]
    else '<span class="badge" style="background:#DCFCE7;color:#166534">Real Copernicus Sentinel-2 L2A</span>'
)
st.markdown(
    f"{badge} &nbsp; <span style='color:{TEXT_MUTED};font-size:13px'>tile {cfg['mgrs_tile']} · "
    f"{cfg['target_crs']} · green/NIR NDWI (B03, B08) · valid fraction ≥ {cfg['min_valid_fraction']}</span>",
    unsafe_allow_html=True,
)

scenes_period = scenes[
    (scenes["acquired_at"].dt.date >= date_from) & (scenes["acquired_at"].dt.date <= date_to)
    & (scenes["acquired_at"].dt.month.isin(months))
]  # fmt: skip
accepted = parcel_obs[parcel_obs["status"] == "accepted"]
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Scenes used / found", f"{int(scenes_period['selected'].sum())} / {len(scenes_period)}",
          help="Items found in STAC vs. scenes processed after de-duplication, cloud pre-filter and checks")
k2.metric("Accepted observations", f"{len(accepted)} / {len(parcel_obs)}")
if not accepted.empty:
    latest = accepted.iloc[-1]
    k3.metric("Latest accepted", f"{latest.acquired_at:%d %b %Y}")
    delta = latest.ndwi_median - accepted.iloc[-2].ndwi_median if len(accepted) > 1 else None
    k4.metric("Latest median NDWI", f"{latest.ndwi_median:.3f}", None if delta is None else f"{delta:+.3f} vs previous")
    k5.metric("Median valid pixels", f"{accepted['valid_fraction'].median():.0%}")
else:
    k3.metric("Latest accepted", "–")
    k4.metric("Latest median NDWI", "–")
    k5.metric("Median valid pixels", "–")


# ---------------------------------------------------------------------------
# Map + parcel panel
# ---------------------------------------------------------------------------

map_col, panel_col = st.columns([7, 5], gap="medium")

with map_col:
    scene_obs = in_period[in_period["item_id"] == item_id].set_index("parcel_id") if item_id else pd.DataFrame()
    shapes = folium.FeatureGroup(name="Parcels")
    for p in sorted(parcels, key=lambda p: -p["area_m2"]):  # big polygons first, boxes on top
        row = scene_obs.loc[p["parcel_id"]] if p["parcel_id"] in scene_obs.index else None
        value = row["ndwi_median"] if row is not None and row["status"] == "accepted" else None
        status = "no observation" if row is None else row["status"] if row["status"] == "accepted" else f"rejected: {row['rejection_reason']}"
        red, green, blue, alpha = ndwi_colour(value)
        style = {
            "fillColor": f"#{red:02x}{green:02x}{blue:02x}", "fillOpacity": alpha / 255 * 0.8,
            "color": "#FACC15" if p["parcel_id"] == parcel_id else "#FFFFFF",
            "weight": 3 if p["parcel_id"] == parcel_id else 1.5,
        }  # fmt: skip
        folium.GeoJson(
            {"type": "Feature", "geometry": mapping(p["geometry"]), "properties": {
                "name": p["name"], "status": status,
                "ndwi": "–" if value is None or pd.isna(value) else f"{value:.3f}",
                "valid": "–" if row is None else f"{row['valid_fraction']:.0%}",
            }},
            style_function=lambda _, style=style: style,
            tooltip=folium.GeoJsonTooltip(["name", "ndwi", "valid", "status"], aliases=["", "NDWI median", "valid pixels", ""]),
        ).add_to(shapes)  # fmt: skip
    geometry = parcel_by_id[parcel_id]["geometry"]
    st_folium(
        base_map(), key="results_map", height=470, use_container_width=True, returned_objects=[],
        center=(geometry.centroid.y, geometry.centroid.x), zoom=fit_zoom(geometry.bounds, 16), feature_group_to_add=shapes,
    )  # fmt: skip
    st.caption(
        f"Parcel medians for scene **{labels.get(item_id, '–')}** · colour: brown = drier "
        f"(NDWI ≤ {NDWI_RANGE[0]}) → blue = wetter (≥ {NDWI_RANGE[1]}), grey = rejected · "
        "selected parcel outlined in yellow · switch between satellite and street map at the top right."
    )

with panel_col:
    parcel = parcel_by_id[parcel_id]
    st.markdown(f"**{parcel['name']}**")
    st.caption(
        f"`{parcel_id}` · {parcel['area_m2'] / 1e4:,.2f} ha · source {parcel['source_ref']} · "
        f"geometry {'repaired' if parcel['geometry_repaired'] else 'valid'}"
    )

    fig = go.Figure()
    if not accepted.empty:
        fig.add_trace(go.Scatter(
            x=accepted["acquired_at"], y=accepted["ndwi_median"], mode="markers+lines", name="accepted (median)",
            line={"color": PARCEL_COLOURS.get(parcel["kind"], PRIMARY), "width": 1},
            marker={"size": 6},
            error_y={"type": "data", "symmetric": False, "thickness": 1, "width": 0,
                     "array": accepted["ndwi_p75"] - accepted["ndwi_median"],
                     "arrayminus": accepted["ndwi_median"] - accepted["ndwi_p25"]},
            customdata=accepted[["item_id", "valid_fraction"]],
            hovertemplate="%{x|%Y-%m-%d}<br>NDWI %{y:.3f}<br>valid %{customdata[1]:.0%}<br>%{customdata[0]}<extra></extra>",
        ))  # fmt: skip
    rejected = parcel_obs[parcel_obs["status"] == "rejected"]
    if show_rejected and not rejected.empty:
        floor = (accepted["ndwi_median"].min() - 0.05) if not accepted.empty else -0.9
        fig.add_trace(go.Scatter(
            x=rejected["acquired_at"], y=[floor] * len(rejected), mode="markers", name="rejected",
            marker={"symbol": "x-thin", "size": 7, "line": {"width": 1, "color": "#94A3B8"}},
            customdata=rejected[["rejection_reason", "valid_fraction"]],
            hovertemplate="%{x|%Y-%m-%d}<br>%{customdata[0]}<br>valid %{customdata[1]:.0%}<extra></extra>",
        ))  # fmt: skip
    fig.update_layout(
        height=300, margin={"l": 10, "r": 10, "t": 10, "b": 10}, yaxis_title="NDWI (B03/B08)",
        legend={"orientation": "h", "y": -0.2}, plot_bgcolor="white",
    )  # fmt: skip
    fig.update_yaxes(gridcolor=BORDER)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Interpretation** <span style='font-size:11px;color:#64748B'>(rule-based, deterministic)</span>", unsafe_allow_html=True)
    for note in interpret(to_obs(parcel_obs)):
        (st.warning if note.level == "warning" else st.info)(note.text)


# ---------------------------------------------------------------------------
# Comparison + quality tables
# ---------------------------------------------------------------------------

st.markdown("### Same-season comparison")
summer = in_period[(in_period["status"] == "accepted")]
if not summer.empty:
    table = (
        summer.assign(year=summer["acquired_at"].dt.year)
        .groupby(["parcel_id", "year"])["ndwi_median"]
        .agg(["median", "count"])
        .reset_index()
    )
    table["cell"] = table.apply(lambda r: f"{r['median']:.3f} (n={int(r['count'])})", axis=1)
    st.dataframe(table.pivot(index="parcel_id", columns="year", values="cell"), use_container_width=True)
    st.caption("Median of accepted parcel medians per year for the selected months. Pick the same months (e.g. Jun–Aug) to compare years.")

st.markdown("### Scene quality ledger")
ledger = scenes_period.copy()
ledger["status"] = ledger["selected"].map({True: "✔ processed", False: "✖ rejected"})
parcel_status = parcel_obs.set_index("item_id")[["status", "valid_fraction", "ndwi_median", "rejection_reason"]]
parcel_status.columns = [f"{parcel_id}: {c}" for c in parcel_status.columns]
ledger = ledger.join(parcel_status, on="item_id")
columns = ["acquired_at", "item_id", "collection", "processing_baseline", "scene_cloud_pct", "boa_offset",
           "status", "rejection_reason", *parcel_status.columns]  # fmt: skip
shown = ledger.sort_values("acquired_at", ascending=False)[columns]
# rejected items carry no metadata: show blanks instead of "None"; text columns keep Arrow happy
text_columns = [c for c in columns if c != "acquired_at"]
shown[text_columns] = shown[text_columns].map(lambda v: "" if pd.isna(v) else f"{v:g}" if isinstance(v, float) else str(v))
st.dataframe(
    shown,
    use_container_width=True, hide_index=True, height=320,
    column_config={"acquired_at": st.column_config.DatetimeColumn("acquired (UTC)", format="YYYY-MM-DD HH:mm")},
)  # fmt: skip

d1, d2, _ = st.columns([1, 1, 3])
d1.download_button("observations.csv", (Path(run_dir) / "observations.csv").read_bytes(), "observations.csv", "text/csv")
d2.download_button("scenes.csv", (Path(run_dir) / "scenes.csv").read_bytes(), "scenes.csv", "text/csv")
st.caption(
    f"Run `{manifest['run_id']}` · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
    "Contains modified Copernicus Sentinel data · parcels © OpenStreetMap contributors (ODbL)."
)
