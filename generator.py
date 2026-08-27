from __future__ import annotations
import uuid
from datetime import timedelta, datetime, time
from .repository import hotels, activities, restaurants, hotel_activity, hotel_restaurant
from .rules import proximity_score, status_score, eligible_status, c13_allowed
from .operational import schedule_activity, schedule_dinner
from .schemas import TripRequest, TripResponse, ProviderState, ItineraryDay, ItineraryStop, TimeBlock
from .config import settings
from .timeline import build_day_timeline


def _planning_travel_min(band: str | None) -> int:
    return {"0–15 min":10,"15–30 min":25,"30–45 min":40}.get(band or "",60)

def _day1_blocks(req: TripRequest):
    arrival=req.arrival_time_local
    checkin_end=(datetime.combine(req.start_date,arrival)+timedelta(minutes=30)).time()
    # Preserve the established romantic-arrival pattern: lunch + rest before external activity.
    lunch_start=time(13,0) if arrival <= time(12,30) else checkin_end
    lunch_end=(datetime.combine(req.start_date,lunch_start)+timedelta(minutes=60)).time()
    rest_start=max(lunch_end,time(14,0))
    rest_end=time(16,0)
    return [
        TimeBlock(title="Arrival at hotel",start_time=arrival,end_time=arrival,kind="arrival"),
        TimeBlock(title="Check-in / settle in",start_time=arrival,end_time=checkin_end,kind="checkin"),
        TimeBlock(title="Lunch",start_time=lunch_start,end_time=lunch_end,kind="meal"),
        TimeBlock(title="Rest / room time",start_time=rest_start,end_time=rest_end,kind="rest"),
    ]

THEMES=[
("Arrival & Romantic Light",["view","sunset","village","castle","beach","wellness","spa"]),
("Sea & Scenic Nature",["sea","beach","coast","cruise","snork","parasail","cape","lagoon","view"]),
("Authentic Cyprus & Food/Wine",["wine","winery","loukoumi","weaving","pottery","village","traditional","food"]),
("Culture & Heritage",["culture","heritage","museum","castle","basilica","archae","monastery","church"]),
("Nature & Wellness",["nature","forest","waterfall","trail","spa","wellness","spring","view"]),
("Active & Adventure",["horse","riding","hiking","cycling","golf","dive","parasail","adventure"]),
("Romantic Finale",["sunset","view","sea","village","wine","beach","romantic","cape"]),
]

def _theme_bonus(a, keywords):
    text=" ".join([a.get("name",""),a.get("category",""),a.get("subcategory","")]).lower()
    return min(36, sum(12 for k in keywords if k in text))

def _interest_bonus(a, req):
    text=" ".join([a.get("name",""),a.get("category",""),a.get("subcategory","")]).lower()
    b=0
    if req.interest_sea and any(k in text for k in ["sea","beach","coast","snork","dive","parasail"]): b+=12
    if req.interest_wine_food and any(k in text for k in ["wine","food","tasting","winery","traditional"]): b+=12
    if req.interest_nature and any(k in text for k in ["nature","view","forest","trail","waterfall","cape"]): b+=12
    if req.interest_culture and any(k in text for k in ["culture","heritage","museum","church","archae","castle"]): b+=12
    if req.interest_wellness and any(k in text for k in ["wellness","spa"]): b+=12
    if req.interest_active and any(k in text for k in ["horse","riding","cycling","golf","hiking","adventure","dive"]): b+=18
    return b

def _band_warning(band: str, max_drive_min: int) -> str | None:
    upper={"0–15 min":15,"15–30 min":30,"30–45 min":45}.get(band,999)
    if upper > max_drive_min:
        return f"Planning travel band {band} may exceed preferred {max_drive_min} min; live routing required"
    return None

def generate(req: TripRequest) -> TripResponse:
    hs=hotels(); acts=activities(); maps=hotel_activity()
    if req.hotel_id not in hs:
        raise ValueError("Unknown hotel_id")

    h=hs[req.hotel_id]; hc=h["cluster_id"]
    used=set(); used_dinners=set(); days=[]; trip_warnings=[]
    total_days=min(req.nights+1,7)
    effective_weather="normal" if req.weather_mode=="auto" else req.weather_mode

    for day in range(1,total_days+1):
        target=req.start_date+timedelta(days=day-1)
        theme, words=THEMES[day-1]
        ranked=[]

        for aid,a in acts.items():
            if aid in used or not eligible_status(a["data_status"]):
                continue
            m=maps.get((req.hotel_id,aid))
            if not m:
                continue
            ac=a["cluster_id"]
            if not c13_allowed(hc,ac):
                continue

            op=schedule_activity(a,target,day,req.arrival_time_local,effective_weather)
            if not op["eligible"]:
                continue

            score=proximity_score(m["travel_band"])
            score += float(a.get("romantic_score") or 0)*3
            score += float(a.get("authentic_score") or 0)*(1.2 if req.authentic_priority else .25)
            score += status_score(a["data_status"])
            score += _theme_bonus(a,words)+_interest_bonus(a,req)
            if ac==hc:
                score+=8
            if op["status"]=="PASS":
                score+=8
            elif op["status"]=="RECHECK":
                score-=6

            # Operational weather preference, beyond hard exclusions.
            ptxt=(a.get("payload") or "").lower()
            if effective_weather=="rainy":
                if '"rain_suitable": "yes"' in ptxt:
                    score+=14
                if '"indoor_outdoor_both": "indoor"' in ptxt:
                    score+=18
                elif '"rain_suitable": "conditional"' in ptxt:
                    score-=12
            elif effective_weather=="heatwave":
                if '"heatwave_suitable": "yes"' in ptxt:
                    score+=12
                if '"indoor_outdoor_both": "indoor"' in ptxt:
                    score+=12
            if day==1:
                fit=m.get("recommended_day1","No")
                score += {"Yes":8,"Conditional":2,"No":-30}.get(fit,-30)
            if req.pace=="active" or req.interest_active:
                txt=(a["name"]+" "+a["subcategory"]).lower()
                if "horse" in txt or "riding" in txt:
                    score+=30
            ranked.append((score,aid,m,op))

        if not ranked:
            trip_warnings.append(f"Day {day}: no operationally eligible primary activity found")
            continue

        ranked.sort(reverse=True,key=lambda x:x[0])
        _,aid,m,op=ranked[0]; used.add(aid); a=acts[aid]
        warnings=list(op["warnings"])
        bw=_band_warning(m["travel_band"],req.max_drive_min)
        if bw: warnings.append(bw)
        if a["cluster_id"]=="C13":
            warnings.append("Pissouri is a Limassol cross-district extension; exact live route must pass before LIVE finalization")

        activity=ItineraryStop(
            entity_id=aid,title=a["name"],category=a["category"],cluster_id=a["cluster_id"],
            travel_band=m["travel_band"],data_status=a["data_status"],
            start_time=op["start"],end_time=op["end"],operational_status=op["status"],
            booking_required=bool(op.get("booking_required")),
            planning_travel_min=_planning_travel_min(m["travel_band"]),
            warning="; ".join(warnings) if warnings else None,warnings=warnings
        )

        secondary=None
        if day>1:
            secondary=_pick_secondary(req,req.hotel_id,hc,target,activity,used)
            if secondary:
                used.add(secondary.entity_id)

        dinner=_pick_dinner(req.hotel_id,hc,target,used_dinners,req.max_drive_min)
        if dinner:
            used_dinners.add(dinner.entity_id)

        day_status="PASS"
        if activity.operational_status=="RECHECK" or (secondary and secondary.operational_status=="RECHECK") or (dinner and dinner.operational_status=="RECHECK"):
            day_status="RECHECK"

        day_obj=ItineraryDay(
            day=day,date=target,theme=theme,fixed_blocks=_day1_blocks(req) if day==1 else [],
            activity=activity,secondary_activity=secondary,dinner=dinner,
            weather_mode=effective_weather,weather_status="FALLBACK",
            operational_status=day_status
        )
        timeline, timeline_qa = build_day_timeline(day_obj,h["name"],day)
        day_obj.timeline=timeline
        day_obj.timeline_qa=timeline_qa
        if timeline_qa.status=="BLOCKED":
            day_obj.operational_status="BLOCKED"
        elif timeline_qa.status=="RECHECK" and day_obj.operational_status=="PASS":
            day_obj.operational_status="RECHECK"
        days.append(day_obj)

    if not days:
        overall="BLOCKED"
    else:
        overall="FALLBACK_READY"

    trip_warnings.insert(0,"Routing/weather providers are not connected; planning fallbacks are being used.")
    if req.weather_mode=="auto":
        trip_warnings.append("Auto weather requested, but no live provider is connected; Normal planning mode used as fallback.")

    return TripResponse(
        trip_id=f"trp_{uuid.uuid4().hex[:12]}",
        trip_version_id=f"tv_{uuid.uuid4().hex[:12]}",
        status=overall,
        content_version=settings.content_version,
        rules_version=settings.rules_version,
        provider_state=ProviderState(routing="NOT_CONNECTED",weather="NOT_CONNECTED"),
        days=days,warnings=trip_warnings
    )

def _pick_secondary(req,hotel_id,hc,target,primary,used):
    acts=activities(); maps=hotel_activity()
    candidates=[]
    primary_cat=primary.category
    for aid,a in acts.items():
        if aid in used or aid==primary.entity_id or not eligible_status(a["data_status"]):
            continue
        m=maps.get((hotel_id,aid))
        if not m:
            continue
        if not c13_allowed(hc,a["cluster_id"]):
            continue
        duration=int(float(a.get("duration_min") or 60))
        if duration>90:
            continue
        # A secondary stop should add variety and be reasonably close.
        if a["category"]==primary_cat:
            continue
        if m["travel_band"]=="30–45 min" and req.max_drive_min<=20:
            continue
        # Secondary must be after the primary plus a conservative transfer buffer.
        transfer_guess=max(primary.planning_travel_min or 10,_planning_travel_min(m["travel_band"]))
        earliest=(datetime.combine(target,primary.end_time)+timedelta(minutes=transfer_guess)).time()
        proposed=max(time(16,0),earliest)
        if proposed >= time(18,0):
            continue
        op=schedule_activity(a,target,2,req.arrival_time_local,
                             "normal" if req.weather_mode=="auto" else req.weather_mode,
                             proposed_start=proposed)
        if not op["eligible"]:
            continue
        if op["start"] < earliest or op["end"] >= time(19,0):
            continue
        score=proximity_score(m["travel_band"])
        score+=float(a.get("romantic_score") or 0)*2
        score+=float(a.get("authentic_score") or 0)
        if a["cluster_id"]==primary.cluster_id:
            score+=12
        elif a["cluster_id"]==hc:
            score+=8
        if op["status"]=="PASS":
            score+=8
        else:
            score-=8
        candidates.append((score,aid,m,op))
    if not candidates:
        return None
    candidates.sort(reverse=True,key=lambda x:x[0])
    _,aid,m,op=candidates[0]; a=acts[aid]
    warnings=list(op["warnings"])
    bw=_band_warning(m["travel_band"],req.max_drive_min)
    if bw: warnings.append(bw)
    if a["cluster_id"]=="C13":
        warnings.append("Pissouri cross-district secondary stop requires exact live route validation")
    return ItineraryStop(
        entity_id=aid,title=a["name"],category=a["category"],cluster_id=a["cluster_id"],
        travel_band=m["travel_band"],data_status=a["data_status"],
        start_time=op["start"],end_time=op["end"],operational_status=op["status"],
        booking_required=bool(op.get("booking_required")),
        planning_travel_min=_planning_travel_min(m["travel_band"]),
        warning="; ".join(warnings) if warnings else None,warnings=warnings
    )

def _pick_dinner(hotel_id,hc,target,used_dinners,max_drive_min):
    rests=restaurants(); maps=hotel_restaurant()
    candidates=[]
    for rid,r in rests.items():
        if rid in used_dinners or r["data_status"]!="Verified":
            continue
        if "Dinner" not in (r.get("meal_type") or ""):
            continue
        m=maps.get((hotel_id,rid))
        if not m:
            continue
        if r["cluster_id"]=="C13" and hc!="C02":
            continue
        op=schedule_dinner(r,target)
        if not op["eligible"]:
            continue
        score=proximity_score(m["travel_band"])+float(r.get("romantic_score") or 0)*3
        if r["cluster_id"]==hc:
            score+=10
        if op["status"]=="PASS":
            score+=6
        else:
            score-=4
        candidates.append((score,rid,m,op))
    candidates.sort(reverse=True,key=lambda x:x[0])
    if not candidates:
        return None
    _,rid,m,op=candidates[0]; r=rests[rid]
    warnings=list(op["warnings"])
    bw=_band_warning(m["travel_band"],max_drive_min)
    if bw: warnings.append(bw)
    return ItineraryStop(
        entity_id=rid,title=r["name"],category="Dining",cluster_id=r["cluster_id"],
        travel_band=m["travel_band"],data_status=r["data_status"],
        start_time=op["start"],end_time=op["end"],operational_status=op["status"],
        booking_required=any("reservation required" in x.lower() for x in warnings),
        planning_travel_min=_planning_travel_min(m["travel_band"]),
        warning="; ".join(warnings) if warnings else None,warnings=warnings
    )
