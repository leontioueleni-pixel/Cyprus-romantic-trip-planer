from __future__ import annotations
import os, sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "planner_dev.sqlite3"

def db_path() -> Path:
    return Path(os.getenv("SQLITE_DB_PATH", str(DEFAULT_DB)))

def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
