"""Base parcel layer from the county Fiscal Office's Parcel Fabric Taxparcels service.

Used for what changes slowly: map location, land use, class, certified value,
building size, mailing address and the street list for the nightly MyPlace sweep.
"""
from __future__ import annotations

import time

import requests

URL = "https://gis.cuyahogacounty.gov/server/rest/services/CCFO/Parcel_Fabric_Taxparcels/FeatureServer/0/query"
FIELDS = ["parcelpin", "parcel_addr", "parcel_predir", "parcel_street", "parcel_suffix", "parcel_unit", "parcel_city",
          "property_class", "tax_luc_description", "gross_certified_total", "total_res_liv_area", "parcel_acreage",
          "parcel_owner", "res_bldg_count", "com_living_units", "mail_name", "mail_addr_street", "mail_unit_no",
          "mail_city", "mail_state", "mail_zip"]


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


def _s(a, k):
    return " ".join(str(a.get(k) or "").split())


def fetch_city(city: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        j = _get({"where": f"parcel_city = '{city}'", "outFields": ",".join(FIELDS), "returnGeometry": "true",
                  "outSR": 4326, "geometryPrecision": 6, "maxAllowableOffset": 0.00005,
                  "orderByFields": "ObjectID", "resultOffset": offset, "resultRecordCount": 2000})
        feats = j.get("features", [])
        for f in feats:
            a = f["attributes"]
            c = centroid(f.get("geometry"))
            street = " ".join(x for x in [_s(a, "parcel_predir"), _s(a, "parcel_street"), _s(a, "parcel_suffix")] if x)
            unit = _s(a, "parcel_unit")
            rows.append({
                "pin": _s(a, "parcelpin"),
                "a": " ".join(x for x in [_s(a, "parcel_addr"), street] if x) + (f" #{unit}" if unit else ""),
                "st": _s(a, "parcel_street"),
                "c": city,
                "cls": _s(a, "property_class")[:1],
                "luc": _s(a, "tax_luc_description"),
                "v": a.get("gross_certified_total") or 0,
                "sf": a.get("total_res_liv_area") or 0,
                "ac": a.get("parcel_acreage") or 0,
                "u": a.get("com_living_units") or 0,
                "o0": _s(a, "parcel_owner"),
                "mn": _s(a, "mail_name"),
                "ma": " ".join(x for x in [_s(a, "mail_addr_street"), _s(a, "mail_unit_no")] if x),
                "mc": " ".join(x for x in [_s(a, "mail_city") + ",", _s(a, "mail_state"), _s(a, "mail_zip")[:5]]
                               if x.strip(",")).strip(" ,"),
                "lat": c[0] if c else None,
                "lng": c[1] if c else None,
            })
        if not (j.get("exceededTransferLimit") or len(feats) == 2000) or not feats:
            break
        offset += len(feats)
    return [r for r in rows if r["pin"]]
