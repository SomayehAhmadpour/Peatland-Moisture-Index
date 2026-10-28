from datetime import date

import pytest
from pydantic import ValidationError

from iris_ndwi.config import PipelineConfig

BASE = {
    "study_area": "t",
    "mgrs_tile": "32UNU",
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "boxes": [{"parcel_id": "b-1", "name": "b", "kind": "k", "center_lat": 47.9, "center_lon": 9.9, "size_m": 100}],
}


def test_example_config_loads(config):
    assert config.target_crs == "EPSG:25832"
    assert config.start_date < date(2022, 1, 25) < config.end_date  # spans baseline 04.00


@pytest.mark.parametrize(
    "override, message",
    [
        ({"end_date": "2023-01-01"}, "end_date is before start_date"),
        ({"valid_scl_classes": [0, 4]}, "no data"),
        ({"valid_scl_classes": [12]}, "unknown SCL"),
        ({"min_valid_fraction": 0}, "greater than 0"),
        ({"min_valid_fraction": 1.5}, "less than or equal to 1"),
        ({"mgrs_tile": "UNU32"}, "pattern"),
        ({"boxes": []}, "parcels_geojson and/or boxes"),
    ],
)
def test_invalid_configs_rejected(override, message):
    with pytest.raises(ValidationError, match=message):
        PipelineConfig.model_validate(BASE | override)
