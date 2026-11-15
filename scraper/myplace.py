"""Client for MyPlace (myplace.cuyahogacounty.gov), the Cuyahoga County Fiscal Office property site.

MyPlace is server-rendered. Two pages are used:
  * address search:  GET /<b64(text)>?city=<b64(code)>&searchBy=<b64("Address")>
      -> list of parcels with the CURRENT deeded owner (updated daily by the county)
  * transfer history: POST /MainPage/PropertyData with the Transfers button
      -> every recorded transfer: date, deed type, price, grantee(s), grantor(s)
"""
from __future__ import annotations

import base64
import re
import threading
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE = "https://myplace.cuyahogacounty.gov"
CITY_CODES = {  # MyPlace city dropdown values
    "UNIVERSITY HEIGHTS": "67",
    "SOUTH EUCLID": "38",
    "BEACHWOOD": "87",
    "SHAKER HEIGHTS": "40",
    "LYNDHURST": "59",
    "CLEVELAND HEIGHTS": "74",
}
UA = "Deedwatch/1.0 (personal property-records project; github.com/Nikles19/deedwatch)"
PIN_RE = re.compile(r"^\s*(\d{3})-(\d{2})-(\d{3})\s*$")


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def norm_pin(s: str) -> str:
    return re.sub(r"\D", "", s or "")


class MyPlace:
    def __init__(self, min_interval: float = 0.6, timeout: float = 45, retries: int = 4):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._lock = threading.Lock()
        self._last = 0.0
        self.requests = 0

    def _throttle(self):
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _req(self, method: str, url: str, **kw) -> str:
        err = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                self.requests += 1
                if r.status_code == 200 and "page you are looking for is currently unavailable" not in r.text:
                    return r.text
                err = RuntimeError(f"HTTP {r.status_code} from {url}")
            except requests.RequestException as e:
                err = e
            time.sleep(3 * (attempt + 1) ** 2)
        raise err

    # ---------- search ----------
    def search_address(self, text: str, city: str) -> list[dict]:
        url = f"{BASE}/{b64(text)}?city={b64(CITY_CODES[city])}&searchBy={b64('Address')}"
        return parse_search(self._req("GET", url))

    # ---------- transfers ----------
    def transfers(self, pin: str) -> list[dict] | None:
        pin = norm_pin(pin)
        html = self._req("POST", f"{BASE}/MainPage/PropertyData", data={
            "hdnParcelId": pin, "hdnButtonClicked": "Transfers",
            "hdnSearchChoice": "Parcel", "hdnSearchText": pin, "hdnSearchCity": "99"})
        return parse_transfers(html)


# ---------------- parsers (pure functions, unit tested) ----------------

def parse_search(html: str) -> list[dict]:
    """Return [{pin, owner, address, csz}] from a MyPlace search results page."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select("ul.selectedAddress a.btn"):
        m = PIN_RE.match(a.get_text())
        if not m:
            continue
        pin = "".join(m.groups())
        li = a.find_parent("li")
        vals = []
        sib = li.find_next_sibling("li")
        while sib is not None and len(vals) < 3:
            vals.append(" ".join(sib.get_text(" ").split()))
            sib = sib.find_next_sibling("li")
        if pin in seen:
            continue
        seen.add(pin)
        owner, addr, csz = (vals + ["", "", ""])[:3]
        out.append({"pin": pin, "owner": owner, "address": addr, "csz": csz})
    return out


def _money(s: str) -> float:
    s = re.sub(r"[^\d.]", "", s or "")
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_transfers(html: str) -> list[dict] | None:
    """Return newest-first transfers, [] if the parcel has none, None if this isn't a transfer page."""
    soup = BeautifulSoup(html, "lxml")
    acc = soup.select_one("#accordion")
    if acc is None:
        heading = soup.find(lambda t: t.name == "h3" and t.get_text(strip=True) == "Transfer History")
        return [] if heading is not None else None
    out = []
    for h3 in acc.find_all("h3", recursive=False):
        body = h3.find_next_sibling("div")
        if body is None:
            continue
        labels = {}
        for lab in body.select("span.dataLabel"):
            val = lab.find_next_sibling("span", class_="dataValue")
            labels[lab.get_text(strip=True).rstrip(":")] = val.get_text(" ", strip=True) if val else ""
        d = _date(labels.get("Transfer Date")) or _date(h3.get_text().split(":", 1)[-1])
        row = {}
        tt = body.select_one("table.transferTable")
        if tt is not None:
            heads = [th.get_text(" ", strip=True) for th in tt.select("thead th")]
            first = tt.select_one("tbody tr")
            if first is not None:
                row = dict(zip(heads, [td.get_text(" ", strip=True) for td in first.find_all("td")]))
        ge, gr = [], []
        gt = body.select_one("table.grantees")
        if gt is not None:
            for tr in gt.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 2:
                    a, b = tds[0].get_text(" ", strip=True), tds[1].get_text(" ", strip=True)
                    if a:
                        ge.append(a)
                    if b:
                        gr.append(b)
        multi = row.get("Multiple Sale / No of Parcels", "")
        mm = re.match(r"\s*(\d+)\s*/\s*(\d+)", multi)
        nparcels = int(mm.group(2)) if mm else 1
        out.append({
            "d": d,
            "t": row.get("Deed Type", ""),
            "p": _money(row.get("Sales Amt", "")),
            "n": max(nparcels, 1),
            "ge": ge,
            "gr": gr,
        })
    out.sort(key=lambda x: x["d"] or "", reverse=True)
    return out
