"""Turn the stored data into the static JSON the website reads."""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from . import store

SUFFIX = re.compile(r"\b(L ?L ?C|INC|INCORPORATED|CO|CORP|CORPORATION|COMPANY|LTD|LIMITED|L ?P|L ?L ?P|PLLC|TR|TRS|TRUST|"
                    r"TRUSTEE|TRUSTEES|THE|ET AL|ETAL|ET UX|SUCCESSOR|AS)\b")
PUBLIC = re.compile(r"\b(CITY OF|CITY|VILLAGE OF|COUNTY|STATE OF OHIO|BOARD OF EDUCATION|SCHOOL DIST|LAND REUTILIZATION|"
                    r"LAND BANK|HOUSING AUTHORITY|METROPOLITAN|PORT AUTHORITY|UNITED STATES|MUNICIPAL|PARK DIST|"
                    r"REGIONAL TRANSIT|DEPT OF|DEPARTMENT OF|LIBRARY|BOARD OF|COMMISSIONERS|SEWER DIST)\b")


def owner_key(s: str) -> str:
    s = store.norm_owner(s)
    s = SUFFIX.sub(" ", s)
    return " ".join(s.split())


def is_market(t: dict) -> bool:
    deed = (t.get("t") or "").upper()
    return (t.get("p") or 0) >= 10000 and t.get("n", 1) == 1 and "QUIT" not in deed and "SHERIFF" not in deed


def _days_ago(n: int) -> str:
    return (datetime.now(store.TZ) - timedelta(days=n)).strftime("%Y-%m-%d")


def build(out: str):
    root = store.ROOT
    out = os.path.join(root, out) if not os.path.isabs(out) else out
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(root, "site"), out)
    # cache-bust the app files so browsers pick up new versions right away
    import hashlib
    idx_path = os.path.join(out, "index.html")
    with open(idx_path) as f:
        html = f.read()
    for asset in ("app.js", "styles.css"):
        with open(os.path.join(out, asset), "rb") as f:
            v = hashlib.sha1(f.read()).hexdigest()[:10]
        html = html.replace(f'"{asset}"', f'"{asset}?v={v}"')
    with open(idx_path, "w") as f:
        f.write(html)
    data_dir = os.path.join(out, "data")
    os.makedirs(os.path.join(data_dir, "h"), exist_ok=True)

    parcels = store.load_parcels()
    hist = store.load_all_history()
    events = store.load_events()
    state = store.load_state()
    d400, d30 = _days_ago(400), _days_ago(30)

    # ---- recent transfers (map, this week, prices) ----
    recent = []
    for pin, trs in hist.items():
        p = parcels.get(pin)
        if not p:
            continue
        for t in trs:
            if not t.get("d") or t["d"] < d400:
                continue
            recent.append({
                "pin": pin, "d": t["d"], "p": t.get("p") or 0, "t": t.get("t", ""), "n": t.get("n", 1),
                "buyer": "; ".join(t.get("ge") or []), "seller": "; ".join(t.get("gr") or []),
                "a": p.get("a", ""), "c": p.get("c", ""), "cls": p.get("cls", ""), "luc": p.get("luc", ""),
                "sf": p.get("sf") or 0, "v": p.get("v") or 0, "lat": p.get("lat"), "lng": p.get("lng"),
                "m": 1 if is_market(t) else 0,
            })
    recent.sort(key=lambda r: (r["d"], r["pin"]), reverse=True)

    # ---- changes log (last 30 days): recorded transfers + owner changes not yet matched to a transfer ----
    changes = [{"kind": "sale" if r["m"] else "transfer", "d": r["d"], "pin": r["pin"], "a": r["a"], "c": r["c"],
                "p": r["p"], "t": r["t"], "to": r["buyer"], "from": r["seller"], "lat": r["lat"], "lng": r["lng"]}
               for r in recent if r["d"] >= d30]
    have = {(c["pin"]) for c in changes}
    for e in events:
        if e["seen"] >= d30 and e["pin"] not in have:
            p = parcels.get(e["pin"], {})
            changes.append({"kind": "owner", "d": e["seen"], "pin": e["pin"], "a": p.get("a", ""), "c": p.get("c", ""),
                            "to": e["to"], "from": e["from"], "lat": p.get("lat"), "lng": p.get("lng")})
    changes.sort(key=lambda c: (c["d"], c["pin"]), reverse=True)

    # ---- monthly price stats (market sales, deduped, by city and all) ----
    prices = defaultdict(lambda: defaultdict(list))
    seen_deals = set()
    for r in recent:
        if not r["m"]:
            continue
        key = (r["d"], r["p"], r["buyer"])
        if key in seen_deals:
            continue
        seen_deals.add(key)
        month = r["d"][:7]
        for area in (r["c"], "ALL"):
            prices[area]["res" if r["cls"] == "R" else "other"].append((month, r["p"], r["pin"], r["d"]))

    def summarize(rows):
        by_m = defaultdict(list)
        for m, pr, pin, d in rows:
            by_m[m].append((pr, pin, d))
        months = []
        for m in sorted(by_m):
            v = sorted(x[0] for x in by_m[m])
            hi = max(by_m[m]); lo = min(by_m[m])
            months.append({"m": m, "n": len(v), "med": statistics.median(v), "hi": hi[0], "hiPin": hi[1],
                           "lo": lo[0], "loPin": lo[1]})
        return months

    price_out = {area: {k: summarize(v) for k, v in kinds.items()} for area, kinds in prices.items()}

    # ---- top owners (by name and by tax-bill mailing address) ----
    def addr_key(p):
        a = store.norm_owner(p.get("ma", ""))
        a = re.sub(r"\b(SUITE|STE|UNIT|APT|FL|FLOOR|RM|ROOM)\b", " ", a)
        a = re.sub(r"\bCTR\b", "CENTER", a)
        c = store.norm_owner(p.get("mc", ""))
        return " ".join((a + " " + c).split()) if a else ""

    def top(counter, k=1):
        return [x for x, _ in sorted(counter.items(), key=lambda kv: -kv[1])[:k]]

    def new_group():
        return {"names": defaultdict(int), "n": 0, "v": 0, "pins": [], "mail": defaultdict(int),
                "mailto": defaultdict(int), "addrkeys": defaultdict(int)}

    owners_out, mail_out = {}, {}
    areas = sorted({p.get("c", "") for p in parcels.values()} | {"ALL"})
    by_area = {a: [p for p in parcels.values() if a == "ALL" or p.get("c") == a] for a in areas}
    for area, plist in by_area.items():
        by_name, by_addr = defaultdict(new_group), defaultdict(new_group)
        for p in plist:
            name = p.get("o") or p.get("o0") or ""
            if not name:
                continue
            ak = addr_key(p)
            label_addr = ", ".join(x for x in [p.get("ma", ""), p.get("mc", "")] if x)
            for key, groups in ((owner_key(name) or name, by_name), (ak, by_addr)):
                if not key:
                    continue
                g = groups[key]
                g["names"][name] += 1
                g["n"] += 1
                g["v"] += p.get("v") or 0
                if len(g["pins"]) < 400:
                    g["pins"].append(p["pin"])
                if label_addr:
                    g["mail"][label_addr] += 1
                mn = p.get("mn", "")
                if mn and store.norm_owner(mn) != store.norm_owner(name):
                    g["mailto"][mn] += 1
                if ak:
                    g["addrkeys"][ak] += 1
        rows = []
        for key, g in sorted(by_name.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["v"])):
            if g["n"] < 2 or len(rows) >= 60:
                break
            label = top(g["names"])[0]
            related = defaultdict(int)
            for ak in g["addrkeys"]:
                for nm, cnt in by_addr[ak]["names"].items():
                    if (owner_key(nm) or nm) != key:
                        related[nm] += cnt
            rows.append({"name": label, "n": g["n"], "v": round(g["v"]), "variants": len(g["names"]),
                         "pub": 1 if PUBLIC.search(store.norm_owner(label)) else 0, "pins": g["pins"],
                         "mail": top(g["mail"], 2), "mailto": top(g["mailto"], 3),
                         "related": [[nm, c] for nm, c in sorted(related.items(), key=lambda kv: -kv[1])[:12]]})
        owners_out[area] = rows
        mrows = []
        for key, g in sorted(by_addr.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["v"])):
            if g["n"] < 2 or len(mrows) >= 60:
                break
            names = sorted(g["names"].items(), key=lambda kv: -kv[1])
            lead = names[0][0]
            mrows.append({"name": top(g["mail"])[0] if g["mail"] else key, "n": g["n"], "v": round(g["v"]),
                          "entities": len({owner_key(nm) or nm for nm, _ in names}),
                          "pub": 1 if PUBLIC.search(store.norm_owner(lead)) else 0, "pins": g["pins"],
                          "mailto": top(g["mailto"], 3), "related": [[nm, c] for nm, c in names[:12]]})
        mail_out[area] = mrows

    # ---- search / parcel index ----
    index = []
    for pin in sorted(parcels):
        p = parcels[pin]
        names = set()
        for t in hist.get(pin, []):
            names.update(t.get("ge") or [])
            names.update(t.get("gr") or [])
        cur = p.get("o") or p.get("o0") or ""
        names.discard(cur)
        sales = [t for t in hist.get(pin, []) if (t.get("p") or 0) >= 10000 and (t.get("n") or 1) == 1
                 and not re.search(r"quit|sheriff", t.get("t") or "", re.I)]
        last = max(sales, key=lambda t: t.get("d") or "") if sales else None
        index.append([pin, p.get("a", ""), p.get("c", ""), cur, "|".join(sorted(names)), p.get("cls", ""),
                      p.get("luc", ""), p.get("v") or 0, p.get("sf") or 0, p.get("lat"), p.get("lng"),
                      last["p"] if last else 0, (last.get("d") or "")[:7] if last else ""])

    with_hist = sum(1 for p in parcels.values() if p.get("hs"))
    meta = {
        "built": store.now_iso(),
        "base": state.get("base"),
        "sweep": state.get("sweep"),
        "history": state.get("history"),
        "parcels": len(parcels),
        "withHistory": with_hist,
        "cities": sorted({p.get("c") for p in parcels.values() if p.get("c")}),
    }

    def dump(name, obj):
        with open(os.path.join(data_dir, name), "w") as f:
            json.dump(obj, f, separators=(",", ":"))

    dump("meta.json", meta)
    dump("recent.json", recent)
    dump("changes.json", changes)
    dump("prices.json", price_out)
    dump("owners.json", {"name": owners_out, "mail": mail_out})
    dump("index.json", index)
    from . import police as _police
    pol = []
    geo = _police.Geocoder(parcels)
    for r in _police.load().values():
        if r["d"] < d400:
            continue
        ll = geo.locate(r["c"], r["loc"], street=_police.is_traffic(r)) or ((r["lat"], r["lng"]) if r.get("lat") else None)
        if ll:
            pol.append([r["c"], r["d"], r["t"][:1], "; ".join(r.get("off") or []), r["loc"],
                        round(ll[0], 6), round(ll[1], 6), r.get("pdf", ""), r["n"]])
    pol.sort(key=lambda x: x[1], reverse=True)
    dump("police.json", {"base": _police.BASE, "reports": pol, "state": state.get("police")})
    if os.path.isdir(store.HIST):
        for fn in os.listdir(store.HIST):
            if fn.endswith(".json"):
                with open(os.path.join(store.HIST, fn)) as f:
                    h = json.load(f)
                dump(os.path.join("h", fn), h)
    print(f"built {out}: {len(parcels)} parcels, {with_hist} with history, {len(recent)} recent transfers, "
          f"{len(changes)} changes (30d)")
