"""Per-parcel summaries of a classified NDWI window."""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
from affine import Affine
from rasterio.errors import NotGeoreferencedWarning
from rasterio.features import geometry_mask
from shapely.geometry.base import BaseGeometry

from iris_ndwi.ndwi import PixelStatus

# rasterio 1.4 warns from its internal in-memory rasterize dataset even though
# the explicit transform is applied (pixel counts are verified in tests).
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning, module=r"rasterio\.features")


@dataclass(frozen=True)
class ParcelObservation:
    observation_id: str
    parcel_id: str
    item_id: str
    acquired_at: datetime
    method_version: str
    n_pixels: int
    n_nodata: int
    n_scl_masked: int
    n_bright_surface: int
    n_non_positive_reflectance: int
    n_zero_denominator: int
    n_out_of_range: int
    n_valid: int
    valid_fraction: float
    ndwi_mean: float | None
    ndwi_median: float | None
    ndwi_std: float | None
    ndwi_p25: float | None
    ndwi_p75: float | None
    ndwi_min: float | None
    ndwi_max: float | None
    water_fraction: float | None
    status: str  # accepted | rejected
    rejection_reason: str | None

    def as_row(self) -> dict:
        return asdict(self)


def parcel_pixel_mask(geometry: BaseGeometry, shape: tuple[int, int], transform: Affine) -> np.ndarray:
    """True for pixels whose centre lies inside the geometry (all_touched=False).

    Centre-in-polygon keeps partial edge pixels out, so a grid-aligned
    100 m x 100 m box yields exactly 100 Sentinel-2 10 m pixels.
    """
    return geometry_mask([geometry], out_shape=shape, transform=transform, invert=True, all_touched=False)


def _round(value: float | np.floating | None) -> float | None:
    return None if value is None else round(float(value), 6)


def summarize(
    *,
    parcel_id: str,
    item_id: str,
    acquired_at: datetime,
    method_version: str,
    inside: np.ndarray,
    values: np.ndarray,
    status: np.ndarray,
    min_valid_fraction: float,
    water_threshold: float,
) -> ParcelObservation:
    parcel_status = status[inside]
    n_pixels = int(parcel_status.size)
    counts = {reason: int(np.count_nonzero(parcel_status == reason)) for reason in PixelStatus}
    valid = values[inside][parcel_status == PixelStatus.VALID]
    n_valid = counts[PixelStatus.VALID]
    valid_fraction = n_valid / n_pixels if n_pixels else 0.0

    if n_pixels == 0:
        reason = "parcel_has_no_pixels"
    elif n_valid == 0:
        reason = "no_valid_pixels"
    elif valid_fraction < min_valid_fraction:
        reason = "valid_fraction_below_min"
    else:
        reason = None

    stats: dict[str, float | None] = dict.fromkeys(
        ["ndwi_mean", "ndwi_median", "ndwi_std", "ndwi_p25", "ndwi_p75", "ndwi_min", "ndwi_max", "water_fraction"]
    )
    if n_valid:
        p25, median, p75 = np.percentile(valid, [25, 50, 75])
        stats |= {
            "ndwi_mean": valid.mean(),
            "ndwi_median": median,
            "ndwi_std": valid.std(),
            "ndwi_p25": p25,
            "ndwi_p75": p75,
            "ndwi_min": valid.min(),
            "ndwi_max": valid.max(),
            "water_fraction": np.count_nonzero(valid > water_threshold) / n_valid,
        }

    return ParcelObservation(
        observation_id=f"{parcel_id}:{item_id}:{method_version}",
        parcel_id=parcel_id,
        item_id=item_id,
        acquired_at=acquired_at,
        method_version=method_version,
        n_pixels=n_pixels,
        n_nodata=counts[PixelStatus.NODATA],
        n_scl_masked=counts[PixelStatus.SCL_MASKED],
        n_bright_surface=counts[PixelStatus.BRIGHT_SURFACE],
        n_non_positive_reflectance=counts[PixelStatus.NON_POSITIVE_REFLECTANCE],
        n_zero_denominator=counts[PixelStatus.ZERO_DENOMINATOR],
        n_out_of_range=counts[PixelStatus.OUT_OF_RANGE],
        n_valid=n_valid,
        valid_fraction=round(valid_fraction, 6),
        **{key: _round(value) for key, value in stats.items()},
        status="rejected" if reason else "accepted",
        rejection_reason=reason,
    )
