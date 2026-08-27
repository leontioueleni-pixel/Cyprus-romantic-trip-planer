from .generator import generate
from .schemas import TripRequest, ValidationResponse

def validate_request(req: TripRequest) -> ValidationResponse:
    trip=generate(req)
    blockers=[]
    rechecks=[]
    warnings=list(trip.warnings)

    if not trip.days:
        blockers.append("No operationally eligible itinerary days were generated")

    for day in trip.days:
        if day.operational_status=="BLOCKED":
            blockers.append(f"Day {day.day}: blocked")
        if day.activity.operational_status=="RECHECK":
            rechecks.append(f"Day {day.day} activity: {day.activity.title}")
        if day.dinner and day.dinner.operational_status=="RECHECK":
            rechecks.append(f"Day {day.day} dinner: {day.dinner.title}")
        if day.activity.cluster_id=="C13":
            rechecks.append(f"Day {day.day}: Pissouri C13 activity requires exact live route validation")
        if day.dinner and day.dinner.cluster_id=="C13":
            rechecks.append(f"Day {day.day}: Pissouri C13 dinner requires exact live route validation")
        if any("live routing required" in w.lower() for w in day.activity.warnings):
            rechecks.append(f"Day {day.day} activity travel time requires live routing")
        if day.dinner and any("live routing required" in w.lower() for w in day.dinner.warnings):
            rechecks.append(f"Day {day.day} dinner travel time requires live routing")

    status="BLOCKED" if blockers else ("RECHECK" if rechecks else "PASS")
    return ValidationResponse(
        status=status,
        itinerary_status=trip.status,
        blockers=blockers,rechecks=rechecks,warnings=warnings
    )
