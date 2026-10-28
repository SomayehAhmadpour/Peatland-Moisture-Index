"""Fetch the Wurzacher Ried reference geometries from OpenStreetMap.

Writes data/parcels/wurzacher_ried_osm.geojson (EPSG:4326). The output is
committed to the repository so pipeline runs never depend on Overpass.

Usage:
    python scripts/fetch_osm_parcels.py               # query Overpass
    python scripts/fetch_osm_parcels.py --from-file overpass.json
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import LineString, MultiPolygon, Polygon, mapping
from shapely.ops import linemerge, polygonize, unary_union

OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter"

# Stable OSM identifiers for the two features we use.
NSG_WAY_ID = 688159107  # leisure=nature_reserve, ref:DE-BW=4.035, WDPA 6979
BOG_RELATION_ID = 2958383  # natural=wetland, wetland=bog

QUERY = f"""[out:json][timeout:90];
(relation({BOG_RELATION_ID});way({NSG_WAY_ID}););
out geom;"""

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "parcels" / "wurzacher_ried_osm.geojson"


def fetch() -> dict:
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    request = urllib.request.Request(
        OVERPASS_URL, data=data, headers={"User-Agent": "iris-ndwi/0.1"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def _ring_lines(members: list[dict], role: str) -> list[LineString]:
    return [
        LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
        for m in members
        if m["type"] == "way" and m["role"] == role and len(m.get("geometry", [])) >= 2
    ]


def relation_to_polygon(element: dict) -> MultiPolygon:
    """Assemble an OSM multipolygon relation (outer minus inner rings)."""
    outers = list(polygonize(linemerge(_ring_lines(element["members"], "outer"))))
    inners = list(polygonize(linemerge(_ring_lines(element["members"], "inner"))))
    shape = unary_union(outers).difference(unary_union(inners))
    return shape if isinstance(shape, MultiPolygon) else MultiPolygon([shape])


def way_to_polygon(element: dict) -> MultiPolygon:
    return MultiPolygon([Polygon([(p["lon"], p["lat"]) for p in element["geometry"]])])


def build_features(payload: dict) -> list[dict]:
    features = []
    for element in payload["elements"]:
        tags = element.get("tags", {})
        if element["type"] == "way" and element["id"] == NSG_WAY_ID:
            geometry = way_to_polygon(element)
            properties = {
                "parcel_id": "wr-nsg-4035",
                "name": "NSG Wurzacher Ried (4.035)",
                "kind": "reserve",
            }
        elif element["type"] == "relation" and element["id"] == BOG_RELATION_ID:
            geometry = relation_to_polygon(element)
            properties = {
                "parcel_id": "wr-bog-osm",
                "name": "Wurzacher Ried bog (OSM wetland=bog)",
                "kind": "bog",
            }
        else:
            continue
        properties |= {
            "source": "OpenStreetMap",
            "source_ref": f"osm:{element['type']}/{element['id']}",
            "licence": "ODbL 1.0 (c) OpenStreetMap contributors",
            "osm_tags": tags,
        }
        features.append({"type": "Feature", "properties": properties, "geometry": mapping(geometry)})
    return sorted(features, key=lambda f: f["properties"]["parcel_id"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", type=Path, help="use a saved Overpass JSON response")
    args = parser.parse_args()

    payload = json.loads(args.from_file.read_text()) if args.from_file else fetch()
    features = build_features(payload)
    if len(features) != 2:
        raise SystemExit(f"expected 2 features, got {len(features)}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    collection = {
        "type": "FeatureCollection",
        "name": "wurzacher_ried_osm",
        "retrieved_at": payload.get("osm3s", {}).get("timestamp_osm_base")
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": features,
    }
    OUTPUT.write_text(json.dumps(collection, indent=1) + "\n")
    print(f"wrote {len(features)} features to {OUTPUT}")


if __name__ == "__main__":
    main()
