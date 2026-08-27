from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from .schemas import TripRequest, TripResponse, ValidationResponse
from .generator import generate
from .validation import validate_request
from .config import settings
from .repository import hotels
from pathlib import Path
import socket
import os

app=FastAPI(title="Cyprus Romantic Trip Planner MVP",version="0.1.0")

@app.get("/api/v1/health")
def health():
    return {
        "service":"ok",
        "data_backend":"sqlite-dev",
        "environment":settings.app_env,
        "routing_provider":"enabled" if settings.enable_live_providers and settings.google_routes_api_key else "not_connected",
        "weather_provider":"enabled" if settings.enable_live_providers else "not_connected",
        "content_version":settings.content_version,
        "rules_version":settings.rules_version,
    }

@app.post("/api/v1/trips/generate",response_model=TripResponse)
def create_trip(req: TripRequest):
    try:
        return generate(req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/v1/trips/validate",response_model=ValidationResponse)
def validate_trip(req: TripRequest):
    try:
        return validate_request(req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


STATIC_DIR = Path(__file__).resolve().parent / "static"

@app.get("/", include_in_schema=False)
def web_app():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/v1/meta/hotels")
def list_hotels():
    rows = []
    for hotel_id, h in hotels().items():
        rows.append({
            "hotel_id": hotel_id,
            "name": h["name"],
            "area": h.get("area"),
            "cluster_id": h.get("cluster_id"),
        })
    return sorted(rows, key=lambda x: x["name"])


@app.get("/api/v1/meta/network")
def network_info():
    public_url = os.getenv("PUBLIC_BASE_URL","").strip().rstrip("/")
    if public_url:
        return {
            "mode":"public_cloud",
            "host_ip":None,
            "mobile_url":public_url,
            "note":"Public HTTPS URL; accessible from mobile data or any network."
        }
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return {
        "mode":"local_lan",
        "host_ip": ip,
        "mobile_url": f"http://{ip}:8000",
        "note": "Use this URL from an Android device on the same Wi-Fi network."
    }
