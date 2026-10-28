"""IRIS NDWI dashboard — read-only view of a pipeline run.

    uv run --group ui streamlit run ui/app.py

Reads a run folder written by `iris-ndwi run` (observations.csv, scenes.csv,
parcels.geojson, manifest.json); no database or network access is needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from shapely.geometry import mapping, shape

from iris_ndwi.interpret import Obs, interpret
from iris_ndwi.parcels import reproject

ROOT = Path(__file__).resolve().parent.parent
RUN_ROOTS = [ROOT / "output", ROOT / "examples" / "runs"]

PRIMARY, TEXT_MUTED, BORDER = "#2563EB", "#64748B", "#E5E7EB"
PARCEL_COLOURS = {"bog-core": "#2563EB", "bog": "#16A34A", "reserve": "#94A3B8", "reference": "#DC2626"}
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
      [data-testid="stSidebar"] span[data-baseweb="tag"] {{ background-color: {PRIMARY} !important; }}
      div[data-testid="stMetricValue"] > div {{ font-size: 1.55rem; }}
      div[data-testid="stMetric"] {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 10px 14px; }}
      div[data-testid="stMetricLabel"] p {{ font-size: 12px; color: {TEXT_MUTED}; }}
      .block-container {{ padding-top: 1.6rem; }}
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


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

runs = find_runs()
if not runs:
    st.error("No pipeline run found. Run `iris-ndwi run --config config/wurzacher_ried.yaml` first.")
    st.stop()

with st.sidebar:
    st.markdown("### 🛰️ IRIS · Peatland NDWI")
    run_dir = st.selectbox("Run", runs, format_func=lambda p: f"{p.parent.name}/{p.name}")
    data = load_run(str(run_dir))
    obs_all, scenes, manifest, parcels = data["obs"], data["scenes"], data["manifest"], data["parcels"]
    parcel_by_id = {p["parcel_id"]: p for p in parcels}

    parcel_ids = sorted(parcel_by_id, key=lambda i: ["bog-core", "bog", "reserve", "reference"].index(parcel_by_id[i]["kind"]) if parcel_by_id[i]["kind"] in PARCEL_COLOURS else 9)
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
    features = []
    for p in sorted(parcels, key=lambda p: -p["area_m2"]):  # big polygons first, boxes on top
        row = scene_obs.loc[p["parcel_id"]] if p["parcel_id"] in scene_obs.index else None
        value = row["ndwi_median"] if row is not None and row["status"] == "accepted" else None
        status = "no observation" if row is None else row["status"] if row["status"] == "accepted" else f"rejected: {row['rejection_reason']}"
        features.append({
            "type": "Feature",
            "geometry": mapping(p["geometry"]),
            "properties": {
                "name": p["name"], "status": status,
                "ndwi": "–" if value is None or pd.isna(value) else f"{value:.3f}",
                "valid": "–" if row is None else f"{row['valid_fraction']:.0%}",
                "fill": ndwi_colour(value),
                "line": [37, 99, 235] if p["parcel_id"] == parcel_id else [30, 41, 59],
                # metres; pydeck reads string kwargs as JS expressions, so no "pixels" units here
                "width": 8 if p["parcel_id"] == parcel_id else (5 if p["area_m2"] < 1e5 else 12),
            },
        })  # fmt: skip
    centre = parcel_by_id[parcel_id]["geometry"].centroid
    deck = pdk.Deck(
        layers=[
            pdk.Layer(
                "GeoJsonLayer",
                {"type": "FeatureCollection", "features": features},
                pickable=True, stroked=True, filled=True,
                get_fill_color="properties.fill", get_line_color="properties.line",
                get_line_width="properties.width", line_width_min_pixels=1,
            )
        ],
        initial_view_state=pdk.ViewState(latitude=centre.y, longitude=centre.x, zoom=12.6),
        map_style="light",
        tooltip={"html": "<b>{name}</b><br/>NDWI median: {ndwi}<br/>valid pixels: {valid}<br/>{status}"},
    )  # fmt: skip
    st.pydeck_chart(deck, height=470)
    st.caption(
        f"Parcel medians for scene **{labels.get(item_id, '–')}** · colour: brown = drier "
        f"(NDWI ≤ {NDWI_RANGE[0]}) → blue = wetter (≥ {NDWI_RANGE[1]}), grey = rejected · "
        "100 m boxes are small: zoom in. Basemap © CARTO, © OpenStreetMap contributors."
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
