from __future__ import annotations
import httpx
from .config import settings

async def compute_route(payload: dict) -> dict:
    if not settings.enable_live_providers or not settings.google_routes_api_key:
        return {"status":"NOT_CONNECTED","duration_min":None,"distance_km":None}
    headers={
        "X-Goog-Api-Key":settings.google_routes_api_key,
        "X-Goog-FieldMask":"routes.duration,routes.distanceMeters",
        "Content-Type":"application/json",
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        r=await client.post("https://routes.googleapis.com/directions/v2:computeRoutes",headers=headers,json=payload)
        r.raise_for_status()
        data=r.json()
    route=(data.get("routes") or [{}])[0]
    dur=str(route.get("duration","0s")).rstrip("s")
    return {"status":"LIVE","duration_min":float(dur)/60 if dur else None,"distance_km":route.get("distanceMeters",0)/1000}

async def weather_forecast(params: dict) -> dict:
    if not settings.enable_live_providers:
        return {"status":"NOT_CONNECTED"}
    async with httpx.AsyncClient(timeout=5.0) as client:
        r=await client.get(settings.weather_base_url,params=params)
        r.raise_for_status()
        return {"status":"LIVE","raw":r.json()}
