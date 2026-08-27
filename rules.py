def proximity_score(band: str | None) -> int:
    return {"0–15 min":20,"15–30 min":15,"30–45 min":8}.get(band or "",0)

def status_score(status: str) -> int:
    return {"Verified":10,"Needs Recheck":-10,"Draft":-30,"Inactive":-50}.get(status,-50)

def eligible_status(status: str) -> bool:
    return status not in {"Draft","Inactive"}

def c13_allowed(hotel_cluster: str, activity_cluster: str) -> bool:
    return activity_cluster != "C13" or hotel_cluster == "C02"

def weather_score(payload: dict, mode: str) -> int:
    rain=payload.get("Rain_Suitable","")
    heat=payload.get("Heatwave_Suitable","")
    if mode=="heatwave":
        return 15 if heat=="Yes" else (4 if heat=="Conditional" else -30)
    if mode=="rainy":
        return 15 if rain=="Yes" else (4 if rain=="Conditional" else -30)
    return 5
