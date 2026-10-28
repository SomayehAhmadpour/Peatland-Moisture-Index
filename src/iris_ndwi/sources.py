"""Scene sources: live STAC + Cloud-Optimized GeoTIFF reads, behind one protocol."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import rasterio
from affine import Affine
from rasterio.windows import Window
from shapely.geometry import Polygon, mapping

from iris_ndwi.scenes import BandAsset, Scene

# GDAL settings for anonymous, range-request reads of public COGs.
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}


class SourceError(RuntimeError):
    """A scene could not be read from the source."""


@dataclass
class SceneWindow:
    """Blue, green, NIR and SCL on one 10 m grid in the scene CRS."""

    blue_dn: np.ndarray
    green_dn: np.ndarray
    nir_dn: np.ndarray
    scl: np.ndarray
    transform: Affine
    crs: str


class SceneSource(Protocol):
    name: str

    def search(self, aoi_wgs84: Polygon, start: str, end: str) -> list[dict]:
        """Return STAC item dicts intersecting aoi_wgs84 between start and end (ISO dates)."""

    def read(self, scene: Scene, bounds: tuple[float, float, float, float]) -> SceneWindow:
        """Read the scene for bounds given in the scene CRS."""


def aligned_windows(origin: Affine, bounds: tuple[float, float, float, float]) -> tuple[Window, Window, Affine]:
    """10 m and 20 m windows covering bounds, aligned on the shared 20 m grid.

    Expanding outward to 20 m multiples means every SCL pixel maps onto
    exactly 2x2 10 m pixels, so SCL is upsampled by pure repetition.
    """
    if origin.a != 10 or origin.e != -10:
        raise SourceError(f"expected a 10 m north-up grid, got {origin}")
    min_x, min_y, max_x, max_y = bounds
    col0 = math.floor((min_x - origin.c) / 20)
    row0 = math.floor((origin.f - max_y) / 20)
    col1 = math.ceil((max_x - origin.c) / 20)
    row1 = math.ceil((origin.f - min_y) / 20)
    window_20 = Window(col0, row0, col1 - col0, row1 - row0)
    window_10 = Window(col0 * 2, row0 * 2, (col1 - col0) * 2, (row1 - row0) * 2)
    transform = Affine(10, 0, origin.c + col0 * 20, 0, -10, origin.f - row0 * 20)
    return window_10, window_20, transform


def _read(band: BandAsset, window: Window) -> tuple[np.ndarray, Affine]:
    with rasterio.open(band.href) as dataset:
        data = dataset.read(1, window=window, boundless=True, fill_value=band.nodata)
        return data, dataset.transform


class StacSource:
    name = "stac"

    def __init__(self, url: str, collections: list[str]):
        self.url = url
        self.collections = collections

    def search(self, aoi_wgs84: Polygon, start: str, end: str) -> list[dict]:
        from pystac_client import Client

        client = Client.open(self.url)
        items = []
        for collection in self.collections:  # one search each, so every item keeps its collection
            search = client.search(
                collections=[collection],
                intersects=mapping(aoi_wgs84),
                datetime=f"{start}T00:00:00Z/{end}T23:59:59Z",
                limit=100,  # page size; larger pages make Earth Search return HTTP 500
            )
            items += search.items_as_dicts()
        return sorted(items, key=lambda item: (item["collection"], item["id"]))

    def read(self, scene: Scene, bounds: tuple[float, float, float, float]) -> SceneWindow:
        try:
            with rasterio.Env(**GDAL_ENV):
                with rasterio.open(scene.green.href) as dataset:
                    origin = dataset.transform
                window_10, window_20, transform = aligned_windows(origin, bounds)
                blue, blue_origin = _read(scene.blue, window_10)
                green, _ = _read(scene.green, window_10)
                nir, nir_origin = _read(scene.nir, window_10)
                scl, scl_origin = _read(scene.scl, window_20)
        except rasterio.errors.RasterioIOError as exc:
            raise SourceError(f"{scene.item_id}: {exc}") from exc
        if any((o.c, o.f) != (origin.c, origin.f) for o in (blue_origin, nir_origin, scl_origin)):
            raise SourceError(f"{scene.item_id}: band grids are not aligned")
        return SceneWindow(
            blue_dn=blue,
            green_dn=green,
            nir_dn=nir,
            scl=scl.repeat(2, axis=0).repeat(2, axis=1),
            transform=transform,
            crs=scene.crs,
        )
