"""Flat-file storage kept in git: small, diffable, no database to host."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PARCELS = os.path.join(DATA, "parcels.jsonl")
EVENTS = os.path.join(DATA, "events.jsonl")
STATE = os.path.join(DATA, "state.json")
HIST = os.path.join(DATA, "history")
TZ = ZoneInfo("America/New_York")


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def norm_owner(s: str) -> str:
    s = (s or "").upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(s.split())


def load_parcels() -> dict[str, dict]:
    out = {}
    if os.path.exists(PARCELS):
        with open(PARCELS) as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    out[r["pin"]] = r
    return out


def save_parcels(p: dict[str, dict]):
    os.makedirs(DATA, exist_ok=True)
    tmp = PARCELS + ".tmp"
    with open(tmp, "w") as f:
        for pin in sorted(p):
            f.write(json.dumps(p[pin], separators=(",", ":"), sort_keys=True) + "\n")
    os.replace(tmp, PARCELS)


def load_events() -> list[dict]:
    if not os.path.exists(EVENTS):
        return []
    with open(EVENTS) as f:
        return [json.loads(x) for x in f if x.strip()]


def append_events(evs: list[dict]):
    if not evs:
        return
    os.makedirs(DATA, exist_ok=True)
    with open(EVENTS, "a") as f:
        for e in evs:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")


def book(pin: str) -> str:
    return pin[:3]


def load_history_book(b: str) -> dict[str, list]:
    path = os.path.join(HIST, f"{b}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_history_book(b: str, h: dict[str, list]):
    os.makedirs(HIST, exist_ok=True)
    path = os.path.join(HIST, f"{b}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("{\n")
        items = sorted(h.items())
        for i, (pin, tr) in enumerate(items):
            f.write(json.dumps(pin) + ":" + json.dumps(tr, separators=(",", ":")) + ("," if i < len(items) - 1 else "") + "\n")
        f.write("}\n")
    os.replace(tmp, path)


def load_all_history() -> dict[str, list]:
    out = {}
    if os.path.isdir(HIST):
        for fn in os.listdir(HIST):
            if fn.endswith(".json"):
                out.update(load_history_book(fn[:-5]))
    return out


def load_state() -> dict:
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {}


def save_state(s: dict):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(s, f, indent=1, sort_keys=True)
