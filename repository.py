from __future__ import annotations
import json
from functools import lru_cache
from .db import connect

@lru_cache
def clusters():
    with connect() as c:
        return {r["cluster_id"]: dict(r) for r in c.execute("SELECT * FROM cluster")}

@lru_cache
def hotels():
    with connect() as c:
        return {r["hotel_id"]: dict(r) for r in c.execute("SELECT * FROM hotel")}

@lru_cache
def activities():
    with connect() as c:
        return {r["activity_id"]: dict(r) for r in c.execute("SELECT * FROM activity")}

@lru_cache
def restaurants():
    with connect() as c:
        return {r["restaurant_id"]: dict(r) for r in c.execute("SELECT * FROM restaurant")}

@lru_cache
def hotel_activity():
    with connect() as c:
        rows=c.execute("SELECT * FROM hotel_activity_mapping")
        return {(r["hotel_id"],r["activity_id"]): dict(r) for r in rows}

@lru_cache
def hotel_restaurant():
    with connect() as c:
        rows=c.execute("SELECT * FROM hotel_restaurant_mapping")
        return {(r["hotel_id"],r["restaurant_id"]): dict(r) for r in rows}

def clear_caches():
    for f in (clusters,hotels,activities,restaurants,hotel_activity,hotel_restaurant):
        f.cache_clear()
