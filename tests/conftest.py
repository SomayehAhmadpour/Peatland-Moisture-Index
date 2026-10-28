from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from iris_ndwi.config import PipelineConfig, load_config
from iris_ndwi.scenes import BandAsset

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

OFFSET_BAND = BandAsset(href="x", scale=0.0001, offset=-0.1, nodata=0)  # baseline >= 04.00
NO_OFFSET_BAND = BandAsset(href="x", scale=0.0001, offset=0.0, nodata=0)  # baseline < 04.00


@pytest.fixture
def config() -> PipelineConfig:
    cfg = load_config(ROOT / "config" / "wurzacher_ried.yaml")
    return cfg.model_copy(update={"parcels_geojson": ROOT / cfg.parcels_geojson, "max_workers": 2})


@pytest.fixture
def boxes_only_config(config: PipelineConfig) -> PipelineConfig:
    return config.model_copy(update={"parcels_geojson": None, "start_date": date(2021, 1, 1)})


@pytest.fixture
def real_items() -> list[dict]:
    """Legacy `sentinel-2-l2a` items (mixed baselines 03.00 - 05.13)."""
    return json.loads((FIXTURES / "earth_search_items_32UNU.json").read_text())["features"]


@pytest.fixture
def c1_items() -> list[dict]:
    """Collection-1 `sentinel-2-c1-l2a` items, incl. a split-datastrip pair (2023-06-25)."""
    return json.loads((FIXTURES / "earth_search_c1_items_32UNU.json").read_text())["features"]
