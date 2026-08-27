from __future__ import annotations
from datetime import date, datetime, time, timedelta
import json, re

WEEKDAYS=["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

def payload(row: dict) -> dict:
    raw=row.get("payload") or row.get("payload_json") or "{}"
    try:
        return json.loads(raw) if isinstance(raw,str) else dict(raw)
    except Exception:
        return {}

def _frac_to_time(v):
    if v in (None,""): return None
    try:
        mins=round(float(v)*24*60)
        return time((mins//60)%24,mins%60)
    except Exception:
        return None

def _parse_time_range(text: str | None):
    if not text: return (None,None)
    # first explicit HH:MM–HH:MM range
    m=re.search(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',str(text))
    if not m: return (None,None)
    return time(int(m.group(1)),int(m.group(2))), time(int(m.group(3)),int(m.group(4)))

def opening_window(row: dict, meal: str | None=None):
    p=payload(row)
    if meal=="dinner":
        dinner_text=str(p.get("Dinner_Hours") or "")
        # Phrases like "until approx. 22:00–22:30" describe closing variation,
        # not a dinner service start/end range; do not misread them as 22:00 start.
        ambiguous=any(k in dinner_text.lower() for k in ["until","confirm","yes","no","varies"])
        if not ambiguous:
            o,c=_parse_time_range(dinner_text)
            if o and c: return o,c,"EXPLICIT"
    if meal=="lunch":
        o,c=_parse_time_range(p.get("Lunch_Hours"))
        if o and c: return o,c,"EXPLICIT"
    o=_frac_to_time(p.get("Parsed_Open_Time"))
    c=_frac_to_time(p.get("Parsed_Close_Time"))
    if o and c and str(p.get("Time_Slot_Usable","")).lower()=="yes":
        return o,c,str(p.get("Time_Parse_Confidence") or "PARSED")
    o,c=_parse_time_range(p.get("Opening_Hours"))
    if o and c: return o,c,"TEXT"
    return None,None,"UNKNOWN"

def day_status(row: dict, target: date) -> str:
    p=payload(row)
    text=str(p.get("Opening_Days") or "").lower().strip()
    if not text:
        return "RECHECK"
    if any(k in text for k in ["daily","monday–sunday","monday-sunday","public access"]):
        return "OPEN"
    day=target.strftime("%A").lower()
    # explicit "closed <day>"
    if re.search(rf'\b{day}\b\s*closed|closed\s*{day}',text):
        return "CLOSED"
    # common abbreviated/listed schedule
    aliases={
      "monday":["mon","monday"],"tuesday":["tue","tues","tuesday"],"wednesday":["wed","wednesday"],
      "thursday":["thu","thur","thurs","thursday"],"friday":["fri","friday"],
      "saturday":["sat","saturday"],"sunday":["sun","sunday"]
    }
    if any(re.search(rf'\b{re.escape(a)}\b',text) for a in aliases[day]):
        return "OPEN"
    # seasonal/reservation/event-specific text cannot be safely inferred
    return "RECHECK"

def weather_eligibility(row: dict, weather_mode: str, proposed_start: time) -> tuple[bool,str]:
    p=payload(row)
    io=str(p.get("Indoor_Outdoor_Both") or "").lower()
    rain=str(p.get("Rain_Suitable") or "")
    heat=str(p.get("Heatwave_Suitable") or "")
    winter=str(p.get("Winter_Suitable") or "")
    if weather_mode=="rainy" and rain=="No":
        return False,"Not rain-suitable"
    if weather_mode=="winter" and winter=="No":
        return False,"Not winter-suitable"
    if weather_mode=="heatwave":
        if heat=="No":
            return False,"Not heatwave-suitable"
        # R001: long/outdoor activities avoid 12:00–16:30.
        # "Conditional" remains eligible only when the proposed slot itself is safe.
        dur=int(float(row.get("duration_min") or 0))
        if "outdoor" in io and dur>=60 and time(12,0) <= proposed_start < time(16,30):
            return False,"Long outdoor activity blocked 12:00–16:30 in heatwave"
    return True,"OK"

def preferred_activity_start(row: dict, day_number: int, arrival_time: time, weather_mode: str) -> time:
    p=payload(row)
    best=str(p.get("Best_Time_of_Day") or "").lower()
    if day_number==1:
        base=time(17,0) if weather_mode=="heatwave" else time(16,30)
    else:
        base=time(10,30)
    if "late afternoon" in best or "sunset" in best:
        base=time(17,30) if weather_mode!="winter" else time(16,0)
    elif "afternoon" in best:
        base=time(16,0)
    elif "morning" in best and "late afternoon" not in best:
        base=time(10,0)
    # Day 1 protects check-in/lunch/rest even if source says morning.
    if day_number==1 and base < time(16,30):
        base=time(17,0) if weather_mode=="heatwave" else time(16,30)
    return base

def schedule_activity(row: dict, target: date, day_number: int, arrival_time: time, weather_mode: str, proposed_start: time | None=None) -> dict:
    p=payload(row)
    start=proposed_start or preferred_activity_start(row,day_number,arrival_time,weather_mode)
    duration=max(15,int(float(row.get("duration_min") or p.get("Typical_Duration_Min") or 60)))
    o,c,confidence=opening_window(row)
    ds=day_status(row,target)
    warnings=[]
    status="PASS"

    if ds=="CLOSED":
        return {"eligible":False,"status":"CLOSED","start":None,"end":None,"warnings":["Closed on selected day"]}
    if ds=="RECHECK":
        warnings.append("Opening day requires confirmation")
        status="RECHECK"

    if o and c:
        if start < o: start=o
        end_dt=datetime.combine(target,start)+timedelta(minutes=duration)
        end=end_dt.time()
        if end > c:
            latest=(datetime.combine(target,c)-timedelta(minutes=duration)).time()
            if latest < o:
                return {"eligible":False,"status":"TIME_FAIL","start":None,"end":None,"warnings":["Activity duration does not fit operating window"]}
            start=latest
            end=(datetime.combine(target,start)+timedelta(minutes=duration)).time()
        if confidence in ("LOW","UNKNOWN"):
            warnings.append("Operating time requires confirmation")
            status="RECHECK"
    else:
        end=(datetime.combine(target,start)+timedelta(minutes=duration)).time()
        warnings.append("Exact operating time requires confirmation")
        status="RECHECK"

    ok,reason=weather_eligibility(row,weather_mode,start)
    if not ok:
        return {"eligible":False,"status":"WEATHER_FAIL","start":None,"end":None,"warnings":[reason]}

    booking=str(p.get("Booking_Required") or "").lower()=="yes"
    if booking:
        warnings.append("Advance booking required")
    return {"eligible":True,"status":status,"start":start,"end":end,"warnings":warnings,
            "booking_required":booking,"day_status":ds,"window_confidence":confidence}

def schedule_dinner(row: dict, target: date, preferred=time(20,0)) -> dict:
    p=payload(row)
    ds=day_status(row,target)
    if ds=="CLOSED":
        return {"eligible":False,"status":"CLOSED","start":None,"end":None,"warnings":["Closed on selected day"]}
    o,c,confidence=opening_window(row,"dinner")
    start=preferred
    warnings=[]
    status="PASS"
    if o and start<o: start=o
    if c and start>=c:
        return {"eligible":False,"status":"TIME_FAIL","start":None,"end":None,"warnings":["Dinner service unavailable at proposed time"]}
    end=(datetime.combine(target,start)+timedelta(minutes=90)).time()
    if c and end>c:
        end=c
    if ds=="RECHECK":
        warnings.append("Opening day requires confirmation"); status="RECHECK"
    if not o or not c:
        warnings.append("Dinner service time requires confirmation"); status="RECHECK"
    reservation=str(p.get("Reservation_Recommended") or "").lower()
    if reservation in ("yes","recommended","mandatory"):
        warnings.append("Dinner reservation recommended" if reservation!="mandatory" else "Dinner reservation required")
    return {"eligible":True,"status":status,"start":start,"end":end,"warnings":warnings}
