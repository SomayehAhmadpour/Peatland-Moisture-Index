"""Deterministic synthetic scenes for tests and offline demos.

Nothing here is real imagery. Items mimic the Earth Search v1 STAC layout and
the 32UNU tile grid so the full pipeline (selection, reading, masking,
summaries) runs without network access. Each scenario exercises one edge case.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np
from affine import Affine
from shapely.geometry import Polygon

from iris_ndwi.scenes import Scene
from iris_ndwi.sources import SceneWindow, SourceError, aligned_windows

TILE = "32UNU"
TILE_ORIGIN = Affine(10, 0, 499980, 0, -10, 5400000)
FAIL_HREF = "synthetic://read-error"
# Published item ids carry this prefix so synthetic rows can never collide with
# (and upsert over) real Sentinel-2 item ids in iris_staging.
ID_PREFIX = "SYNTHETIC_"


@dataclass(frozen=True)
class Scenario:
    item_id: str
    datetime: str
    baseline: str
    sequence: int = 0
    cloud_pct: float = 5.0
    tile: str = TILE
    scl_fill: int = 4  # SCL class for the whole window ...
    cloud_rows: float = 0.0  # ... except this top fraction of rows, set to SCL 9
    nodata_cols: float = 0.0  # left fraction of columns set to nodata (tile edge)
    dark_water: bool = False  # DN below the BOA offset -> non-positive reflectance
    dn_without_offset: bool = False  # metadata claims -0.1 but DN were already harmonised
    fog: bool = False  # bright fog labelled SCL 5 (not vegetated), as seen on 2021-11-12
    read_error: bool = False
    note: str = field(default="", compare=False)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("S2B_32UNU_20210710_0_L2A", "2021-07-10T10:27:00Z", "03.01", note="pre-04.00, no BOA offset"),
    Scenario("S2A_32UNU_20230712_0_L2A", "2023-07-12T10:27:00Z", "05.09", sequence=0, note="superseded duplicate"),
    Scenario("S2A_32UNU_20230712_1_L2A", "2023-07-12T10:27:00Z", "05.09", sequence=1, note="post-04.00, -0.1 offset"),
    Scenario("S2B_32UNU_20240115_0_L2A", "2024-01-15T10:27:00Z", "05.10", scl_fill=11, note="snow everywhere"),
    Scenario("S2A_32UNU_20240503_0_L2A", "2024-05-03T10:27:00Z", "05.10", dark_water=True, note="DN below offset"),
    Scenario("S2B_32UNU_20240618_0_L2A", "2024-06-18T10:27:00Z", "05.10", cloud_rows=0.5, cloud_pct=40, note="half clouded"),
    Scenario("S2A_32UNU_20240801_0_L2A", "2024-08-01T10:27:00Z", "05.10", cloud_pct=95, note="pre-filter reject"),
    Scenario("S2A_32TNT_20240801_0_L2A", "2024-08-01T10:27:00Z", "05.10", tile="32TNT", note="other tile"),
    Scenario("S2B_32UNU_20240905_0_L2A", "2024-09-05T10:27:00Z", "05.10", nodata_cols=0.6, note="tile edge nodata"),
    Scenario("S2A_32UNU_20241010_0_L2A", "2024-10-10T10:27:00Z", "05.11", read_error=True, note="unreadable asset"),
    Scenario("S2B_32UNU_20250604_0_L2A", "2025-06-04T10:27:00Z", "05.11", dn_without_offset=True, note="offset applied twice"),
    Scenario("S2A_32UNU_20251112_0_L2A", "2025-11-12T10:27:00Z", "05.11", fog=True, scl_fill=5, note="fog missed by SCL"),
)


def _offset(baseline: str) -> float:
    return -0.1 if tuple(int(p) for p in baseline.split(".")) >= (4, 0) else 0.0


def scenario_item(s: Scenario) -> dict:
    band_meta = {"nodata": 0, "data_type": "uint16", "scale": 0.0001}
    if _offset(s.baseline):
        band_meta["offset"] = _offset(s.baseline)
    href = FAIL_HREF if s.read_error else f"synthetic://{s.item_id}"
    zone, band, square = s.tile[:2], s.tile[2], s.tile[3:]
    return {
        "type": "Feature",
        "id": ID_PREFIX + s.item_id,
        "collection": "sentinel-2-c1-l2a",
        "properties": {
            "datetime": s.datetime,
            "platform": "sentinel-2a" if s.item_id.startswith("S2A") else "sentinel-2b",
            "eo:cloud_cover": s.cloud_pct,
            "proj:epsg": 32632,
            "grid:code": f"MGRS-{s.tile}",
            "mgrs:utm_zone": int(zone),
            "mgrs:latitude_band": band,
            "mgrs:grid_square": square,
            "s2:processing_baseline": s.baseline,
            "s2:generation_time": f"{s.datetime[:10]}T{12 + s.sequence:02d}:00:00Z",
            "view:sun_elevation": 55.0,
            "synthetic:note": s.note,
        },
        "assets": {
            "blue": {"href": f"{href}/B02.tif", "raster:bands": [band_meta]},
            "green": {"href": f"{href}/B03.tif", "raster:bands": [band_meta]},
            "nir": {"href": f"{href}/B08.tif", "raster:bands": [band_meta]},
            "scl": {"href": f"{href}/SCL.tif", "raster:bands": [{"nodata": 0, "data_type": "uint8"}]},
        },
    }


def physical_reflectance(transform: Affine, shape: tuple[int, int], seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Smooth, deterministic green/NIR reflectance fields with small noise."""
    rows, cols = np.indices(shape)
    x = transform.c + (cols + 0.5) * transform.a
    y = transform.f + (rows + 0.5) * transform.e
    rng = np.random.default_rng(seed)
    green = 0.06 + 0.02 * np.sin(x / 700.0) + rng.normal(0, 0.002, shape)
    nir = 0.28 + 0.06 * np.cos(y / 900.0) + rng.normal(0, 0.004, shape)
    return green, nir


class SyntheticSource:
    name = "synthetic"

    def __init__(self, scenarios: tuple[Scenario, ...] = SCENARIOS):
        self.scenarios = {s.item_id: s for s in scenarios}

    def search(self, aoi_wgs84: Polygon, start: str, end: str) -> list[dict]:
        return sorted(
            (scenario_item(s) for s in self.scenarios.values() if start <= s.datetime[:10] <= end),
            key=lambda item: item["id"],
        )

    def read(self, scene: Scene, bounds: tuple[float, float, float, float]) -> SceneWindow:
        s = self.scenarios[scene.item_id.removeprefix(ID_PREFIX)]
        if s.read_error:
            raise SourceError(f"{scene.item_id}: simulated HTTP 503 from asset host")
        _, window_20, transform = aligned_windows(TILE_ORIGIN, bounds)
        shape = (int(window_20.height) * 2, int(window_20.width) * 2)

        # The physical surface does not depend on the processing baseline, so the
        # same date seed yields identical NDWI before and after the offset change.
        green, nir = physical_reflectance(transform, shape, zlib.crc32(s.datetime[5:10].encode()))
        blue = green * 0.6
        if s.fog:
            blue[:], green[:], nir[:] = 0.22, 0.22, 0.26
        dark_rows = max(2, shape[0] // 50)  # ~2 % of the window, below the scene guard
        if s.dark_water:
            green[:dark_rows] = 0.02
            nir[:dark_rows] = 0.01

        def to_dn(reflectance: np.ndarray, band) -> np.ndarray:
            offset = 0.0 if s.dn_without_offset else band.offset
            dn = np.round((reflectance - offset) / band.scale)
            return np.clip(dn, 1, 65535).astype(np.uint16)

        blue_dn = to_dn(blue, scene.blue)
        green_dn = to_dn(green, scene.green)
        nir_dn = to_dn(nir, scene.nir)
        if s.dark_water:
            # Real L2A water/shadow pixels can sit below the +1000 DN offset.
            green_dn[:dark_rows] = 900
            nir_dn[:dark_rows] = 950

        scl = np.full(shape, s.scl_fill, dtype=np.uint8)
        scl[: int(round(shape[0] * s.cloud_rows / 2)) * 2] = 9
        nodata_cols = int(round(shape[1] * s.nodata_cols / 2)) * 2
        if nodata_cols:
            blue_dn[:, :nodata_cols] = 0
            green_dn[:, :nodata_cols] = 0
            nir_dn[:, :nodata_cols] = 0
            scl[:, :nodata_cols] = 0
        return SceneWindow(
            blue_dn=blue_dn, green_dn=green_dn, nir_dn=nir_dn, scl=scl, transform=transform, crs=scene.crs
        )
