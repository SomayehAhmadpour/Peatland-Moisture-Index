"""Typed pipeline configuration, loaded from YAML."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Sentinel-2 L2A Scene Classification Layer classes.
# https://sentiwiki.copernicus.eu/web/s2-processing (Level-2A algorithm, SCL)
SCL_CLASSES = {
    0: "no_data",
    1: "saturated_or_defective",
    2: "topographic_casted_shadows",
    3: "cloud_shadows",
    4: "vegetation",
    5: "not_vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud_medium_probability",
    9: "cloud_high_probability",
    10: "thin_cirrus",
    11: "snow_or_ice",
}


class BoxParcel(BaseModel):
    """A square parcel defined by its centre point and edge length in metres."""

    parcel_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    kind: str
    center_lat: float = Field(ge=-90, le=90)
    center_lon: float = Field(ge=-180, le=180)
    size_m: float = Field(gt=0, le=10_000)
    snap_m: float | None = Field(default=10, gt=0)


class AreaParcel(BaseModel):
    """A longitude/latitude rectangle (WGS84), e.g. 131°29′–132°15′ E, 46°28′–46°59′ N."""

    parcel_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    kind: str
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _ordered(self) -> AreaParcel:
        if self.east <= self.west:
            raise ValueError("east must be greater than west (areas across the 180° meridian are not supported)")
        if self.north <= self.south:
            raise ValueError("north must be greater than south")
        return self


class PipelineConfig(BaseModel):
    study_area: str
    stac_url: str = "https://earth-search.aws.element84.com/v1"
    # Priority order: the first collection wins when an acquisition exists in several.
    collections: list[str] = Field(default=["sentinel-2-c1-l2a", "sentinel-2-l2a"], min_length=1)
    # None = use the tile that covers the parcels most often (see scenes.dominant_tile)
    mgrs_tile: str | None = Field(default=None, pattern=r"^\d{2}[A-Z]{3}$")
    start_date: date
    end_date: date
    target_crs: str = "EPSG:25832"

    # Scene pre-filter on the item-level cloud estimate. This only avoids
    # downloading hopeless scenes; per-parcel acceptance uses the SCL mask.
    max_scene_cloud_pct: float = Field(default=80, ge=0, le=100)
    min_valid_fraction: float = Field(default=0.8, gt=0, le=1)
    valid_scl_classes: list[int] = [4, 5, 6]
    require_positive_reflectance: bool = True
    water_threshold: float = Field(default=0.0, ge=-1, le=1)
    # Pixels brighter than this in blue (B02) are fog/haze/snow missed by SCL;
    # None disables the test.
    max_blue_reflectance: float | None = Field(default=0.12, gt=0, le=1)
    # Scene guard: reject when more than this share of clear pixels has DN
    # below the advertised BOA offset (see ndwi.below_offset_fraction).
    max_below_offset_fraction: float = Field(default=0.5, ge=0, le=1)

    parcels_geojson: Path | None = None
    boxes: list[BoxParcel] = []
    areas: list[AreaParcel] = []
    output_dir: Path = Path("output")
    max_workers: int = Field(default=4, ge=1, le=32)

    @field_validator("valid_scl_classes")
    @classmethod
    def _known_scl_classes(cls, value: list[int]) -> list[int]:
        unknown = sorted(set(value) - set(SCL_CLASSES))
        if unknown:
            raise ValueError(f"unknown SCL classes: {unknown}")
        if 0 in value:
            raise ValueError("SCL class 0 (no data) can never be valid")
        return sorted(set(value))

    @model_validator(mode="after")
    def _check_ranges(self) -> PipelineConfig:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if not self.parcels_geojson and not self.boxes and not self.areas:
            raise ValueError("configure parcels_geojson, boxes and/or areas")
        return self


def load_config(path: Path) -> PipelineConfig:
    with open(path) as handle:
        return PipelineConfig.model_validate(yaml.safe_load(handle))
