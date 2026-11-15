"""Police reports from the departments' online report portal (TAC Computer "webreports").

Each department posts a list of reports (number, Incident/Accident, date, location) with a PDF per report.
We keep the list fields and, for incidents, the offense descriptions read from the PDF. Names are not stored.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from . import store

BASE = "https://webreports.taccomputer.net/policeReport/"
AGENCIES = {  # portal id -> city (South Euclid and Shaker Heights stopped posting in Oct 2023; kept in case they resume)
    52: "UNIVERSITY HEIGHTS",
    38: "BEACHWOOD",
    44: "CLEVELAND HEIGHTS",
    69: "SOUTH EUCLID",
    73: "SHAKER HEIGHTS",
}
POLICE = os.path.join(store.DATA, "police.jsonl")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 Deedwatch/1.0 (+github.com/Nikles19/deedwatch)")
KEEP_DAYS = 400


def load() -> dict[str, dict]:
    out = {}
    if os.path.exists(POLICE):
        with open(POLICE) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out[r["id"]] = r
    return out


def save(reports: dict[str, dict]):
    cutoff = (datetime.now(store.TZ) - timedelta(days=KEEP_DAYS + 30)).strftime("%Y-%m-%d")
    tmp = POLICE + ".tmp"
    with open(tmp, "w") as f:
        for k in sorted(reports, key=lambda k: (reports[k]["d"], k)):
            if reports[k]["d"] >= cutoff:
                f.write(json.dumps(reports[k], separators=(",", ":"), sort_keys=True) + "\n")
    os.replace(tmp, POLICE)


class Blocked(RuntimeError):
    pass


class Portal:
    """Talks to the portal with requests; if the server resets Python's connections, falls back to curl."""

    def __init__(self, min_interval=0.6):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "text/html,application/pdf,*/*;q=0.8",
                               "Accept-Language": "en-US,en;q=0.9"})
        self.min_interval, self._last, self.requests = min_interval, 0.0, 0
        self.use_curl, self.fails = False, 0

    def _curl(self, method, url, data=None):
        import subprocess
        cmd = ["curl", "-sS", "--max-time", "60", "-A", UA, "-o", "-", "-w", "\n%{http_code}", url]
        if method == "POST":
            for k, v in (data or {}).items():
                cmd += ["--data-urlencode", f"{k}={v}"]
        out = subprocess.run(cmd, capture_output=True, timeout=90)
        if out.returncode:
            raise RuntimeError(f"curl exit {out.returncode}: {out.stderr.decode()[:200]}")
        body, _, code = out.stdout.rpartition(b"\n")
        if code != b"200":
            raise RuntimeError(f"curl HTTP {code.decode()} {url}")
        r = requests.models.Response()
        r.status_code, r._content, r.encoding = 200, body, "utf-8"
        return r

    def _req(self, method, url, **kw):
        if self.fails >= 3:
            raise Blocked("portal keeps refusing connections from this server")
        err = None
        for attempt in range(3):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self._curl(method, url, kw.get("data")) if self.use_curl else self.s.request(method, url, timeout=45, **kw)
                self.requests += 1
                if r.status_code == 200:
                    self.fails = 0
                    return r
                err = RuntimeError(f"HTTP {r.status_code} {url}")
            except requests.ConnectionError as e:
                err = e
                if not self.use_curl:  # Python's connection was reset: try curl from here on
                    self.use_curl = True
                    print("police: switching to curl after", type(e).__name__, flush=True)
                    continue
            except Exception as e:  # noqa: BLE001
                err = e
            time.sleep(2 * (attempt + 1))
        self.fails += 1
        raise err

    def _old_req(self, method, url, **kw):
        err = None
        for attempt in range(4):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.request(method, url, timeout=60, **kw)
                self.requests += 1
                if r.status_code == 200:
                    return r
                err = RuntimeError(f"HTTP {r.status_code} {url}")
            except requests.RequestException as e:
                err = e
            time.sleep(3 * (attempt + 1) ** 2)
        raise err

    def by_date(self, agency: int, day: datetime) -> list[dict]:
        r = self._req("POST", BASE + "reports_results.asp",
                      data={"agencyId": str(agency), "Date": f"{day.month}/{day.day}/{day.year}"})
        return parse_list(r.text)

    def pdf(self, href: str) -> bytes:
        return self._req("GET", BASE + href).content


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        num = tds[1].get_text(strip=True)
        if not num.isdigit():
            continue
        a = tr.find("a", href=True)
        try:
            d = datetime.strptime(tds[3].get_text(strip=True), "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        out.append({"n": num, "t": tds[2].get_text(strip=True), "d": d,
                    "loc": " ".join(tds[5].get_text(" ", strip=True).split()), "pdf": a["href"] if a else ""})
    return out


JUNK = re.compile(r"^(Codes:|Descriptions:|Weapons Used:|Trade Marks:|NONE|\d+|[A-Z]\d+)$")


def parse_offenses(text: str) -> list[str]:
    m = re.search(r"OFFENSES(.*?)(Codes:|Descriptions:|Weapons Used:)", text, re.S)
    if not m:
        return []
    out = []
    for line in re.split(r"[\n|]+", m.group(1)):
        line = " ".join(line.split())
        if line and not JUNK.match(line) and line not in out:
            out.append(line)
    return out[:6]


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:2])


SUFFIX = re.compile(r"\b(RD|ROAD|AVE|AVENUE|ST|STREET|BLVD|BOULEVARD|DR|DRIVE|CT|COURT|LN|LANE|PL|PLACE|PKWY|PARKWAY|"
                    r"WAY|CIR|CIRCLE|TER|TERRACE|PK|PARK|SQ|OVAL|TRL|N|S|E|W)\b")


def street_key(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    s = SUFFIX.sub(" ", s)
    return " ".join(s.split())


class Geocoder:
    """Place a report at a parcel: exact house number on the street, else the nearest number on that street."""

    def __init__(self, parcels: dict[str, dict]):
        self.exact, self.by_street = {}, {}
        for p in parcels.values():
            if not p.get("lat"):
                continue
            m = re.match(r"^(\d+)\s+(.*)$", p.get("a", ""))
            if not m:
                continue
            num, st = int(m.group(1)), street_key(m.group(2).split("#")[0])
            key = (p.get("c", ""), st)
            self.exact.setdefault((key, num), (p["lat"], p["lng"]))
            self.by_street.setdefault(key, []).append((num, p["lat"], p["lng"]))

    def locate(self, city: str, loc: str):
        parts = [x.strip() for x in loc.replace("/", ",").split(",") if x.strip()]
        num = next((int(x) for x in parts if x.isdigit()), None)
        streets = [x for x in parts if not x.isdigit()]
        if not streets:
            return None
        key = (city, street_key(streets[0]))
        if num is not None and (key, num) in self.exact:
            return self.exact[(key, num)]
        pts = self.by_street.get(key)
        if not pts:
            return None
        if num is None:
            pts = sorted(pts)
            mid = pts[len(pts) // 2]
            return mid[1], mid[2]
        best = min(pts, key=lambda t: abs(t[0] - num))
        return (best[1], best[2]) if abs(best[0] - num) <= 200 else None


def run(minutes: float = 30, days: int = 10, backfill_days: int = KEEP_DAYS, interval: float = 0.6):
    reports = load()
    parcels = store.load_parcels()
    geo = Geocoder(parcels)
    st = store.load_state()
    pst = st.setdefault("police", {})
    portal = Portal(interval)
    deadline = time.monotonic() + minutes * 60
    today = datetime.now(store.TZ).replace(tzinfo=None)
    added = 0
    for ag, city in AGENCIES.items():
        done_back = pst.get(f"back_{ag}")
        span = days if done_back else backfill_days
        start = 0 if (done_back or not reports) else pst.get(f"back_pos_{ag}", 0)
        for i in range(start, span):
            if time.monotonic() > deadline:
                if not done_back:
                    pst[f"back_pos_{ag}"] = i
                break
            day = today - timedelta(days=i)
            try:
                rows = portal.by_date(ag, day)
            except Blocked:
                if not done_back:
                    pst[f"back_pos_{ag}"] = i
                break
            except Exception as e:  # noqa: BLE001
                print("police list failed", ag, day.date(), e, flush=True)
                if not done_back:  # retry this day next run rather than skipping it
                    pst[f"back_pos_{ag}"] = i
                break
            for r in rows:
                rid = f"{ag}-{r['n']}"
                if rid in reports:
                    continue
                ll = geo.locate(city, r["loc"])
                reports[rid] = {"id": rid, "ag": ag, "c": city, **r, "lat": ll[0] if ll else None,
                                "lng": ll[1] if ll else None, "off": [] if r["t"].lower().startswith("inc") else ["Traffic crash"],
                                "ok": 0 if r["t"].lower().startswith("inc") else 1}
                added += 1
        else:
            if not done_back:
                pst[f"back_{ag}"] = store.today()
    # offense descriptions from the PDFs, newest first
    cutoff = (today - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    todo = sorted((r for r in reports.values() if not r.get("ok") and r.get("pdf") and r["d"] >= cutoff),
                  key=lambda r: r["d"], reverse=True)
    parsed = failed = 0
    for r in todo:
        if time.monotonic() > deadline or portal.fails >= 3:
            break
        try:
            r["off"] = parse_offenses(pdf_text(portal.pdf(r["pdf"])))
            r["ok"] = 1
            parsed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            r["tries"] = r.get("tries", 0) + 1
            if r["tries"] >= 3:
                r["ok"] = 1
            print("police pdf failed", r["id"], e, flush=True)
    save(reports)
    pending = sum(1 for r in reports.values() if not r.get("ok") and r["d"] >= cutoff)
    pst["error"] = "portal refused connections" if (portal.fails >= 3 and not added and not parsed) else None
    pst["via"] = "curl" if portal.use_curl else "requests"
    pst.update({"at": store.now_iso(), "added": added, "parsed": parsed, "failed": failed, "pending": pending,
                "total": len(reports), "located": sum(1 for r in reports.values() if r.get("lat"))})
    store.save_state(st)
    print(f"police: {added} new reports, {parsed} PDFs read, {failed} failed, {pending} PDFs still to read, "
          f"{len(reports)} kept", flush=True)
