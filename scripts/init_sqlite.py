from __future__ import annotations
import csv
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.getenv("SQLITE_DB_PATH", ROOT / "data" / "planner_dev.sqlite3"))
SEED = ROOT / "seed"

DB.parent.mkdir(parents=True, exist_ok=True)

if DB.exists():
    DB.unlink()

schema = """
PRAGMA foreign_keys=ON;

CREATE TABLE content_release(
    content_version TEXT PRIMARY KEY,
    published_at TEXT DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE TABLE rules_release(
    rules_version TEXT PRIMARY KEY,
    config TEXT NOT NULL DEFAULT '{}',
    notes TEXT
);

CREATE TABLE cluster(
    cluster_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    district TEXT NOT NULL,
    cross_district INTEGER NOT NULL DEFAULT 0,
    content_version TEXT NOT NULL REFERENCES content_release(content_version),
    main_areas TEXT,
    profile TEXT,
    notes TEXT,
    CHECK(cluster_id <> 'C13' OR (district='Limassol' AND cross_district=1))
);

CREATE TABLE hotel(
    hotel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cluster_id TEXT NOT NULL REFERENCES cluster(cluster_id),
    status TEXT NOT NULL,
    romantic_score REAL,
    cleanliness_score REAL,
    area TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE activity(
    activity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cluster_id TEXT NOT NULL REFERENCES cluster(cluster_id),
    category TEXT NOT NULL,
    subcategory TEXT,
    data_status TEXT NOT NULL CHECK(data_status IN ('Verified','Needs Recheck','Draft','Inactive')),
    romantic_score REAL,
    authentic_score REAL,
    duration_min INTEGER,
    area TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE restaurant(
    restaurant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cluster_id TEXT NOT NULL REFERENCES cluster(cluster_id),
    data_status TEXT NOT NULL CHECK(data_status IN ('Verified','Needs Recheck','Draft','Inactive')),
    area TEXT,
    meal_type TEXT,
    romantic_score REAL,
    authentic_score REAL,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE hotel_activity_mapping(
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hotel_id TEXT NOT NULL REFERENCES hotel(hotel_id),
    activity_id TEXT NOT NULL REFERENCES activity(activity_id),
    content_version TEXT NOT NULL REFERENCES content_release(content_version),
    travel_band TEXT NOT NULL,
    recommended_day1 TEXT NOT NULL,
    weather_fit TEXT,
    romantic_score REAL,
    data_status TEXT,
    UNIQUE(hotel_id,activity_id,content_version)
);

CREATE TABLE hotel_restaurant_mapping(
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hotel_id TEXT NOT NULL REFERENCES hotel(hotel_id),
    restaurant_id TEXT NOT NULL REFERENCES restaurant(restaurant_id),
    content_version TEXT NOT NULL REFERENCES content_release(content_version),
    travel_band TEXT NOT NULL,
    meal_type TEXT,
    romantic_score REAL,
    data_status TEXT,
    UNIQUE(hotel_id,restaurant_id,content_version)
);
"""

def rows(name):
    with (SEED / name).open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)

def num(v):
    return None if v in ("", None) else float(v)

CONTENT = "content_2026_08_27"
RULES = "rules_1_0"

with sqlite3.connect(DB) as c:
    c.executescript(schema)

    c.execute(
        "INSERT INTO content_release(content_version,notes) VALUES (?,?)",
        (CONTENT, "v37 curated seed"),
    )

    c.execute(
        "INSERT INTO rules_release(rules_version,config,notes) VALUES (?,?,?)",
        (
            RULES,
            json.dumps({
                "R001": {"heatwave_temp_c": 35},
                "XD001": {"C13_district": "Limassol"}
            }),
            "initial rules",
        ),
    )

    for r in rows("clusters.csv"):
        c.execute(
            "INSERT INTO cluster VALUES (?,?,?,?,?,?,?,?)",
            (
                r["cluster_id"],
                r["name"],
                r["district"],
                1 if r["cross_district"] == "true" else 0,
                CONTENT,
                r["main_areas"],
                r["profile"],
                r["notes"],
            ),
        )

    for r in rows("hotels.csv"):
        c.execute(
            "INSERT INTO hotel VALUES (?,?,?,?,?,?,?,?)",
            (
                r["hotel_id"],
                r["name"],
                r["cluster_id"],
                r["status"],
                num(r["romantic_score"]),
                num(r["cleanliness_score"]),
                r["area"],
                r["payload_json"],
            ),
        )

    for r in rows("activities.csv"):
        c.execute(
            "INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["activity_id"],
                r["name"],
                r["cluster_id"],
                r["category"],
                r["subcategory"],
                r["data_status"],
                num(r["romantic_score"]),
                num(r["authentic_score"]),
                None if not r["duration_min"] else int(float(r["duration_min"])),
                r["area"],
                r["payload_json"],
            ),
        )

    for r in rows("restaurants.csv"):
       
