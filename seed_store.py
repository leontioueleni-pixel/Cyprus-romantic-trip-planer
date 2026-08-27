from __future__ import annotations
import csv
from pathlib import Path
from functools import lru_cache

ROOT = Path(__file__).resolve().parents[1] / "seed"

def _read(name: str):
    with (ROOT / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

@lru_cache
def clusters(): return {r["cluster_id"]: r for r in _read("clusters.csv")}

@lru_cache
def hotels(): return {r["hotel_id"]: r for r in _read("hotels.csv")}

@lru_cache
def activities(): return {r["activity_id"]: r for r in _read("activities.csv")}

@lru_cache
def restaurants(): return {r["restaurant_id"]: r for r in _read("restaurants.csv")}

@lru_cache
def hotel_activity():
    out={}
    for r in _read("hotel_activity_mapping.csv"):
        out[(r["Hotel_ID"],r["Activity_ID"])] = r
    return out

@lru_cache
def hotel_restaurant():
    out={}
    for r in _read("hotel_restaurant_mapping.csv"):
        out[(r["Hotel_ID"],r["Restaurant_ID"])] = r
    return out
