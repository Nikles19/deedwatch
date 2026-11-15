"""Deedwatch data pipeline.

  python -m scraper base                 refresh map/land-use base from county Open Data
  python -m scraper sweep                nightly: current owners from MyPlace, log owner changes
  python -m scraper history --minutes N  fetch transfer histories (changed parcels first, then backfill)
  python -m scraper build [--out DIR]    write the website's JSON files
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from . import opendata, store
from .myplace import CITY_CODES, MyPlace

CITY_ORDER = ["UNIVERSITY HEIGHTS", "SOUTH EUCLID", "CLEVELAND HEIGHTS", "SHAKER HEIGHTS", "BEACHWOOD", "LYNDHURST"]
KEEP_FROM_PREV = ("o", "os", "hs", "hq")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def cmd_base(args):
    parcels = store.load_parcels()
    total = 0
    for city in CITY_ORDER:
        rows = opendata.fetch_city(city)
        log(f"base {city}: {len(rows)} parcels")
        for r in rows:
            prev = parcels.get(r["pin"], {})
            for k in KEEP_FROM_PREV:
                if k in prev:
                    r[k] = prev[k]
            parcels[r["pin"]] = r
        total += len(rows)
    store.save_parcels(parcels)
    st = store.load_state()
    st["base"] = store.now_iso()
    store.save_state(st)
    log(f"base done: {total} parcels")


def cmd_sweep(args):
    parcels = store.load_parcels()
    if not parcels:
        sys.exit("No parcels yet; run `base` first.")
    mp = MyPlace(min_interval=args.interval)
    day = store.today()
    events, seen, failures = [], set(), []
    cities = args.city or CITY_ORDER
    for city in cities:
        streets = sorted({p["st"] for p in parcels.values() if p["c"] == city and p.get("st")})
        log(f"sweep {city}: {len(streets)} streets")
        for st in streets:
            try:
                results = mp.search_address(st, city)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{city}/{st}: {e}")
                continue
            for r in results:
                pin = r["pin"]
                if pin in seen:
                    continue
                if not r["csz"].upper().startswith(city):
                    continue  # street-name match from a different municipality
                seen.add(pin)
                p = parcels.get(pin)
                if p is None:
                    p = parcels[pin] = {"pin": pin, "a": r["address"], "c": city, "st": st, "new": day}
                old = p.get("o")
                if old is None:
                    p["o"], p["os"] = r["owner"], day
                elif store.norm_owner(old) != store.norm_owner(r["owner"]):
                    events.append({"seen": day, "pin": pin, "from": old, "to": r["owner"]})
                    p["o"], p["os"] = r["owner"], day
                    p["hq"] = 1  # queue a transfer-history refresh
    store.save_parcels(parcels)
    store.append_events(events)
    st = store.load_state()
    st["sweep"] = {"at": store.now_iso(), "seen": len(seen), "changes": len(events), "requests": mp.requests,
                   "failures": len(failures)}
    store.save_state(st)
    log(f"sweep done: {len(seen)} parcels seen, {len(events)} owner changes, {len(failures)} failed searches")
    for f in failures[:20]:
        log("  failed:", f)
    if failures and len(failures) > 0.2 * max(1, len(seen)):
        sys.exit("Too many failed searches")


def cmd_history(args):
    parcels = store.load_parcels()
    order = {c: i for i, c in enumerate(CITY_ORDER)}
    queue = [p for p in parcels.values() if p.get("hq")]
    queue += sorted((p for p in parcels.values() if not p.get("hq") and not p.get("hs")),
                    key=lambda p: (order.get(p["c"], 99), p["pin"]))
    if args.refresh_days:
        cutoff = (datetime.now(store.TZ) - timedelta(days=args.refresh_days)).strftime("%Y-%m-%d")
        queue += sorted((p for p in parcels.values() if p.get("hs") and p["hs"] < cutoff and not p.get("hq")),
                        key=lambda p: p["hs"])
    log(f"history queue: {len(queue)} parcels")
    if not queue:
        return
    mp = MyPlace(min_interval=args.interval)
    deadline = time.monotonic() + args.minutes * 60
    books: dict[str, dict] = {}
    done = fail = 0
    day = store.today()

    def work(p):
        return p, mp.transfers(p["pin"])

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        it = iter(queue)
        futures = set()

        def top_up():
            while len(futures) < args.workers * 2 and time.monotonic() < deadline:
                p = next(it, None)
                if p is None:
                    return
                futures.add(ex.submit(work, p))

        top_up()
        while futures:
            for fut in as_completed(list(futures)):
                futures.discard(fut)
                try:
                    p, tr = fut.result()
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    log("  history failed:", e)
                    break
                if tr is None:
                    fail += 1
                else:
                    b = store.book(p["pin"])
                    if b not in books:
                        books[b] = store.load_history_book(b)
                    books[b][p["pin"]] = tr
                    p["hs"] = day
                    p.pop("hq", None)
                    done += 1
                    if done % 500 == 0:
                        log(f"  {done} histories fetched")
                break
            top_up()
    for b, h in books.items():
        store.save_history_book(b, h)
    store.save_parcels(parcels)
    remaining = sum(1 for p in parcels.values() if p.get("hq") or not p.get("hs"))
    st = store.load_state()
    st["history"] = {"at": store.now_iso(), "fetched": done, "failed": fail, "remaining": remaining}
    store.save_state(st)
    log(f"history done: {done} fetched, {fail} failed, {remaining} parcels still without history")


def cmd_police(args):
    from . import police
    police.run(minutes=args.minutes, days=args.days)


def cmd_build(args):
    from .build import build
    build(args.out)


def main():
    ap = argparse.ArgumentParser(prog="scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("base").set_defaults(fn=cmd_base)
    s = sub.add_parser("sweep")
    s.add_argument("--city", action="append", choices=list(CITY_CODES))
    s.add_argument("--interval", type=float, default=0.7)
    s.set_defaults(fn=cmd_sweep)
    h = sub.add_parser("history")
    h.add_argument("--minutes", type=float, default=60)
    h.add_argument("--workers", type=int, default=2)
    h.add_argument("--interval", type=float, default=0.5)
    h.add_argument("--refresh-days", type=int, default=0)
    h.set_defaults(fn=cmd_history)
    pol = sub.add_parser("police")
    pol.add_argument("--minutes", type=float, default=30)
    pol.add_argument("--days", type=int, default=10)
    pol.set_defaults(fn=cmd_police)
    b = sub.add_parser("build")
    b.add_argument("--out", default="_site")
    b.set_defaults(fn=cmd_build)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
