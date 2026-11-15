"""Base parcel layer from the county's ArcGIS Open Data Parcels service.

Ownership here lags months behind, so it is used only for what changes slowly:
map location, land use, class, certified value, building size, street list.
"""
from __future__ import annotations

import time

import requests

URL = "https://gis.cuyahogacounty.gov/server/rest/services/Open_Data_Parcels/MapServer/1/query"
FIELDS = ["parcelpin", "par_addr", "par_street", "par_suffix", "par_unit", "par_city", "par_zip", "property_class",
          "tax_luc_description", "gross_certified_total", "total_res_liv_area", "total_acreage", "deeded_owner",
          "res_bldg_count", "com_living_units"]


def _get(params, retries=4):
    err = None
    for i in range(retries):
        try:
            r = requests.post(URL, data={**params, "f": "json"}, timeout=120,
                              headers={"User-Agent": "Deedwatch/1.0"})
            j = r.json()
            if "error" in j:
                raise RuntimeError(j["error"])
            return j
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(5 * (i + 1))
    raise err


def centroid(geom):
    if not geom:
        return None
    if "x" in geom:
        return round(geom["y"], 6), round(geom["x"], 6)
    xs, ys = [], []
    for ring in geom.get("rings", []):
        for x, y in ring:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return round((min(ys) + max(ys)) / 2, 6), round((min(xs) + max(xs)) / 2, 6)


def fetch_city(city: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        j = _get({"where": f"par_city = '{city}'", "outFields": ",".join(FIELDS), "returnGeometry": "true",
                  "outSR": 4326, "geometryPrecision": 6, "maxAllowableOffset": 0.00005,
                  "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": 2000})
        feats = j.get("features", [])
        for f in feats:
            a = f["attributes"]
            c = centroid(f.get("geometry"))
            num = (a.get("par_addr") or "").strip()
            street = " ".join(x for x in [(a.get("par_street") or "").strip(), (a.get("par_suffix") or "").strip()] if x)
            unit = (a.get("par_unit") or "").strip()
            rows.append({
                "pin": (a.get("parcelpin") or "").strip(),
                "a": " ".join(x for x in [num, street] if x) + (f" #{unit}" if unit else ""),
                "st": (a.get("par_street") or "").strip(),
                "c": city,
                "cls": (a.get("property_class") or "").strip()[:1],
                "luc": (a.get("tax_luc_description") or "").strip(),
                "v": a.get("gross_certified_total") or 0,
                "sf": a.get("total_res_liv_area") or 0,
                "ac": a.get("total_acreage") or 0,
                "u": a.get("com_living_units") or 0,
                "o0": (a.get("deeded_owner") or "").strip(),
                "lat": c[0] if c else None,
                "lng": c[1] if c else None,
            })
        if not (j.get("exceededTransferLimit") or len(feats) == 2000) or not feats:
            break
        offset += len(feats)
    return [r for r in rows if r["pin"]]
