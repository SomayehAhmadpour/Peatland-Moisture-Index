"""Green/NIR NDWI (McFeeters 1996) with an auditable per-pixel status.

    NDWI = (green - nir) / (green + nir)   with green = B03, nir = B08

Reflectance is derived from the stored digital numbers using the scale and
offset published per asset in STAC (raster:bands). This harmonises products
before and after Sentinel-2 processing baseline 04.00 (25 Jan 2022), which
introduced a -1000 DN (-0.1 reflectance) BOA offset.
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np

from iris_ndwi.scenes import BandAsset

METHOD_VERSION = "ndwi-mcfeeters-b03b08-v1"


class PixelStatus(IntEnum):
    """Why a pixel is (in)valid.

    Rules are applied in the order NODATA, SCL_MASKED, BRIGHT_SURFACE,
    NON_POSITIVE_REFLECTANCE, ZERO_DENOMINATOR, OUT_OF_RANGE; the first
    failing rule wins.
    """

    VALID = 0
    NODATA = 1  # nodata DN in blue, green, nir or SCL
    SCL_MASKED = 2  # SCL class not in the configured valid set (cloud, shadow, snow, ...)
    NON_POSITIVE_REFLECTANCE = 3  # green or nir reflectance <= 0 after offset
    ZERO_DENOMINATOR = 4  # green + nir == 0
    OUT_OF_RANGE = 5  # |NDWI| > 1 (only reachable with negative reflectances)
    BRIGHT_SURFACE = 6  # blue reflectance above max: fog, haze or snow that SCL missed


def to_reflectance(dn: np.ndarray, band: BandAsset) -> np.ndarray:
    """DN -> surface reflectance (float64); nodata becomes NaN."""
    reflectance = dn.astype(np.float64) * band.scale + band.offset
    reflectance[dn == band.nodata] = np.nan
    return reflectance


def ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Plain NDWI; a zero denominator yields NaN, never inf or a warning."""
    green = np.asarray(green, dtype=np.float64)
    nir = np.asarray(nir, dtype=np.float64)
    denominator = green + nir
    out = np.full(np.broadcast(green, nir).shape, np.nan)
    np.divide(green - nir, denominator, out=out, where=denominator != 0)
    return out


def below_offset_fraction(
    green_dn: np.ndarray,
    nir_dn: np.ndarray,
    scl: np.ndarray,
    green_band: BandAsset,
    nir_band: BandAsset,
    valid_scl_classes: list[int],
) -> float | None:
    """Share of clear pixels whose DN lies below the advertised BOA offset.

    If metadata claims an offset of -0.1 the stored DN of real surfaces start
    at ~1000; a scene where most clear pixels are below that was already
    harmonised by the provider and applying the offset again would push
    reflectance negative. Returns None when no offset is claimed or no clear
    pixel exists.
    """
    if green_band.offset >= 0 and nir_band.offset >= 0:
        return None
    clear = np.isin(scl, valid_scl_classes) & (green_dn != green_band.nodata) & (nir_dn != nir_band.nodata)
    if not clear.any():
        return None
    below = (green_dn < -green_band.offset / green_band.scale) | (nir_dn < -nir_band.offset / nir_band.scale)
    return float(np.count_nonzero(below & clear) / np.count_nonzero(clear))


def classify_pixels(
    blue_dn: np.ndarray,
    green_dn: np.ndarray,
    nir_dn: np.ndarray,
    scl: np.ndarray,
    blue_band: BandAsset,
    green_band: BandAsset,
    nir_band: BandAsset,
    scl_nodata: int,
    valid_scl_classes: list[int],
    require_positive_reflectance: bool = True,
    max_blue_reflectance: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (ndwi, status). ndwi is NaN wherever status != VALID.

    max_blue_reflectance catches bright fog/haze/snow that Sen2Cor labels as
    land (SCL 4/5); None disables the test.
    """
    if not (blue_dn.shape == green_dn.shape == nir_dn.shape == scl.shape):
        raise ValueError(f"band shapes differ: {blue_dn.shape}, {green_dn.shape}, {nir_dn.shape}, {scl.shape}")

    blue = to_reflectance(blue_dn, blue_band)
    green = to_reflectance(green_dn, green_band)
    nir = to_reflectance(nir_dn, nir_band)
    values = ndwi(green, nir)

    status = np.full(green_dn.shape, PixelStatus.VALID, dtype=np.uint8)

    def mark(condition: np.ndarray, reason: PixelStatus) -> None:
        status[(status == PixelStatus.VALID) & condition] = reason

    mark(np.isnan(blue) | np.isnan(green) | np.isnan(nir) | (scl == scl_nodata), PixelStatus.NODATA)
    mark(~np.isin(scl, valid_scl_classes), PixelStatus.SCL_MASKED)
    if max_blue_reflectance is not None:
        mark(blue > max_blue_reflectance, PixelStatus.BRIGHT_SURFACE)
    if require_positive_reflectance:
        mark((green <= 0) | (nir <= 0), PixelStatus.NON_POSITIVE_REFLECTANCE)
    mark((green + nir) == 0, PixelStatus.ZERO_DENOMINATOR)
    mark(np.abs(values) > 1, PixelStatus.OUT_OF_RANGE)

    values[status != PixelStatus.VALID] = np.nan
    return values, status
