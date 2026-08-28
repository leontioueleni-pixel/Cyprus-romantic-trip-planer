from __future__ import annotations
import json, re, sqlite3, uuid, os, urllib.request, urllib.error
from datetime import date, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DB=ROOT/"planner.sqlite3"
app=FastAPI(title="Cyprus Romantic Trip Planner – Strict Realistic",version="v62")

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

PREF_RULES={
"sea":["sea","coast","parasail","watersport"],
"swimming":["beach","blue flag","lagoon","swim","bathing"],
"beach":["beach","blue flag"],
"boat":["boat","cruise","lagoon","yacht"],
"diving":["diving","scuba","wreck"],
"golf":["golf"],
"horse":["horse","riding","equestrian"],
"cycling":["cycling","biking","bike"],
"hiking":["hiking","trail","walk"],
"viewpoints":["viewpoint","scenic view","sunset","panorama","cape aspro","edro"],
"winery":["winery","wine tasting","wine /"],
"local_food":["local food","product","loukoumi","gastronomy","tasting"],
"crafts":["craft","pottery","ceramic","weaving","basket","embroidery","creative tourism"],
"cooking":["cooking workshop","cookery"],
"museums":["museum","environmental centre","information centre"],
"archaeology":["archae","unesco","castle","ruins","basilica","medieval"],
"villages":["village","square","rural"],
"religious":["church","monastery","religious","byzantine"],
"wellness":["wellness","spa","massage","thermal","sulphur springs"]
}

class TripRequest(BaseModel):
    hotel_id:str
    start_date:date
    nights:int=Field(3,ge=1,le=7)
    pace:str="relaxed"
    weather_mode:str="auto"
    max_drive_min:int=20
    couple_style:str="mixed"   # mixed / romantic_luxury / authentic / active / sea_lovers / food_wine
    budget:str="mid"           # value / mid / premium
    special_occasion:str="none" # none / honeymoon / anniversary / proposal
    prefs:dict[str,str]={}  # yes / no / any

BAND_MAX={"0–15 min":15,"15–30 min":30,"30–45 min":45}
BAND_PLAN={"0–15 min":10,"15–30 min":25,"30–45 min":40}
PROX={"0–15 min":20,"15–30 min":12,"30–45 min":5}

CLUSTER_COORDS={
"C01":(34.7580,32.4070),"C02":(34.7070,32.5750),"C03":(34.8540,32.3730),
"C04":(34.9300,32.4200),"C05":(35.0350,32.4250),"C06":(35.1050,32.5450),
"C07":(34.9950,32.5100),"C08":(34.8050,32.5000),"C09":(34.9200,32.6200),
"C10":(34.8850,32.6250),"C11":(34.7850,32.6750),"C12":(34.9900,32.6900),
"C13":(34.6700,32.7050)
}


def trip_season(d):
    if d.month in [3,4,5]:return "spring"
    if d.month in [6,7,8]:return "summer"
    if d.month in [9,10,11]:return "autumn"
    return "winter"

def combined_weather_season(d,weather_mode):
    return {"season":trip_season(d),"weather":weather_mode}

def owner_hotel_id(r):
    return str(pld(r).get("Owner_Hotel_ID") or "").strip() or None

def selected_hotel_has_spa(hotel):
    return str(pld(hotel).get("Spa") or "").lower()=="yes"

def activity_hotel_compatible(r,hotel,req):
    owner=owner_hotel_id(r)
    tg=tags(r)
    # Never send guests to another hotel's spa.
    if "wellness" in tg and owner and owner!=hotel["hotel_id"]:
        return False
    # If selected hotel has spa, external HOTEL-OWNED spa experiences are not used.
    if "wellness" in tg and selected_hotel_has_spa(hotel) and owner and owner!=hotel["hotel_id"]:
        return False
    return True

def restaurant_hotel_compatible(r,hotel):
    owner=owner_hotel_id(r)
    # Hotel-owned restaurants are only valid for guests of that hotel.
    if owner and owner!=hotel["hotel_id"]:
        return False
    return True

def hotel_location(hotel):
    return f"{hotel['name']}, {hotel['area'] or ''}, Cyprus"

def row_location(r):
    p=pld(r)
    return str(p.get("Full_Address_or_Access_Point") or f"{r['name']}, {p.get('Area') or ''}, Cyprus")

def live_route(origin,destination,cache):
    key=os.getenv("GOOGLE_MAPS_API_KEY","").strip()
    if not key:return None
    ck=(origin,destination)
    if ck in cache:return cache[ck]
    body={"origin":{"address":origin},"destination":{"address":destination},
          "travelMode":"DRIVE","routingPreference":"TRAFFIC_AWARE",
          "computeAlternativeRoutes":False,"languageCode":"en","units":"METRIC"}
    req=urllib.request.Request("https://routes.googleapis.com/directions/v2:computeRoutes",
        data=json.dumps(body).encode("utf-8"),method="POST",
        headers={"Content-Type":"application/json","X-Goog-Api-Key":key,
                 "X-Goog-FieldMask":"routes.duration,routes.distanceMeters"})
    try:
        with urllib.request.urlopen(req,timeout=6) as resp:
            d=json.loads(resp.read().decode("utf-8"))
        if not d.get("routes"):return None
        rt=d["routes"][0]
        sec=float(str(rt.get("duration","0s")).rstrip("s"))
        result={"minutes":max(1,round(sec/60)),"distance_km":round(float(rt.get("distanceMeters",0))/1000,1),"provider":"GOOGLE_ROUTES"}
        cache[ck]=result
        return result
    except Exception:
        cache[ck]=None
        return None

def live_weather(cluster_id,start_date,total_days):
    lat,lon=CLUSTER_COORDS.get(cluster_id,CLUSTER_COORDS["C01"])
    end_date=start_date+timedelta(days=max(0,total_days-1))
    url=("https://api.open-meteo.com/v1/forecast?latitude="+str(lat)+"&longitude="+str(lon)+
         "&daily=temperature_2m_max,precipitation_probability_max,weather_code,wind_speed_10m_max"+
         "&timezone=Europe%2FNicosia&start_date="+str(start_date)+"&end_date="+str(end_date))
    try:
        with urllib.request.urlopen(url,timeout=6) as resp:
            d=json.loads(resp.read().decode("utf-8"))
        daily=d.get("daily") or {}; dates=daily.get("time") or []
        out={}
        for i,ds in enumerate(dates):
            temp=(daily.get("temperature_2m_max") or [None]*len(dates))[i]
            rain=(daily.get("precipitation_probability_max") or [None]*len(dates))[i]
            code=(daily.get("weather_code") or [None]*len(dates))[i]
            wind=(daily.get("wind_speed_10m_max") or [None]*len(dates))[i]
            mode="normal"
            if temp is not None and temp>=35:mode="heatwave"
            if (rain is not None and rain>=50) or (code is not None and code>=51):mode="rainy"
            if start_date.month in [12,1,2] and mode=="normal":mode="winter"
            out[ds]={"mode":mode,"temp_max_c":temp,"rain_probability_max":rain,"weather_code":code,
                     "wind_max_kmh":wind,"provider":"OPEN_METEO"}
        return out
    except Exception:
        return {}


def sunset_local(cluster_id,d):
    # NOAA-style solar approximation; Cyprus local civil time, sufficient for scheduling a 30–45 min sunset block.
    import math
    lat,lon=CLUSTER_COORDS.get(cluster_id,CLUSTER_COORDS["C01"])
    n=d.timetuple().tm_yday
    lng_hour=lon/15.0
    t=n+((18-lng_hour)/24)
    M=(0.9856*t)-3.289
    L=M+(1.916*math.sin(math.radians(M)))+(0.020*math.sin(math.radians(2*M)))+282.634
    L%=360
    RA=math.degrees(math.atan(0.91764*math.tan(math.radians(L))))%360
    Lq=(math.floor(L/90))*90; RAq=(math.floor(RA/90))*90
    RA=(RA+(Lq-RAq))/15
    sinDec=0.39782*math.sin(math.radians(L))
    cosDec=math.cos(math.asin(sinDec))
    cosH=(math.cos(math.radians(90.833))-(sinDec*math.sin(math.radians(lat))))/(cosDec*math.cos(math.radians(lat)))
    if cosH>1 or cosH<-1:return "18:30"
    H=math.degrees(math.acos(cosH))/15
    T=H+RA-(0.06571*t)-6.622
    UT=(T-lng_hour)%24
    # Cyprus: UTC+3 roughly Apr-Oct, UTC+2 otherwise.
    offset=3 if d.month in [4,5,6,7,8,9,10] else 2
    local=(UT+offset)%24
    mins=round(local*60)
    return f"{mins//60:02d}:{mins%60:02d}"

def style_bonus(r,req):
    tg=tags(r); b=0
    st=req.couple_style
    if st=="romantic_luxury":
        if any(k in tg for k in ["viewpoints","boat","wellness","winery"]):b+=22
    elif st=="authentic":
        if any(k in tg for k in ["crafts","local_food","villages","winery","cooking"]):b+=28
    elif st=="active":
        if any(k in tg for k in ["golf","horse","cycling","hiking","diving"]):b+=30
    elif st=="sea_lovers":
        if any(k in tg for k in ["sea","swimming","beach","boat","diving"]):b+=30
    elif st=="food_wine":
        if any(k in tg for k in ["winery","local_food","cooking"]):b+=32
    if req.special_occasion in ["honeymoon","anniversary","proposal"]:
        if any(k in tg for k in ["viewpoints","boat","winery"]):b+=12
    return b

def budget_ok_activity(r,req):
    p=pld(r); price=str(p.get("Price_Range") or "").lower()
    # We only hard-exclude obvious premium/private experiences for value budgets.
    if req.budget=="value" and any(x in (txt(r)+" "+price) for x in ["private charter","private yacht","premium","luxury charter"]):
        return False
    return True

def category_family(r):
    tg=tags(r)
    for k in ["wellness","golf","horse","cycling","hiking","viewpoints","winery","crafts","cooking","museums",
              "archaeology","villages","religious","boat","diving","swimming","beach","sea","local_food"]:
        if k in tg:return k
    return (r["category"] or "other").lower()

def pld(r):
    try:return json.loads(r["payload"] or "{}")
    except:return {}

def txt(r):
    return (" ".join([str(r["name"] or ""),str(r["category"] or ""),str(r["subcategory"] or "")])).lower()

def tags(r):
    t=txt(r); return {k for k,words in PREF_RULES.items() if any(w in t for w in words)}

def addm(hhmm,m):
    h,mi=map(int,hhmm.split(":")); x=h*60+mi+m
    return f"{x//60:02d}:{x%60:02d}"

MONTHS={"jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,
"may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,"sep":9,"sept":9,"september":9,
"oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12}

def explicit_selected_date_match(text,d):
    low=text.lower()
    if "selected" not in low and "published" not in low and "dates include" not in low:return None
    # Isolate the section belonging to the target month and collect day numbers before the next month token.
    month_names=[k for k,v in MONTHS.items() if v==d.month]
    starts=[]
    for mn in month_names:
        for m in re.finditer(rf'\b{re.escape(mn)}\b',low):
            starts.append((m.start(),m.end()))
    if not starts:return False
    all_month_matches=sorted((m.start(),m.end()) for mn in MONTHS for m in re.finditer(rf'\b{re.escape(mn)}\b',low))
    for st,en in starts:
        nxt=min([a for a,b in all_month_matches if a>st],default=len(low))
        seg=low[en:nxt]
        # Remove years and clock times before extracting day numbers.
        seg=re.sub(r'\b20\d{2}\b',' ',seg)
        seg=re.sub(r'\b\d{1,2}:\d{2}\b',' ',seg)
        nums=[int(x) for x in re.findall(r'\b([0-3]?\d)\b',seg)]
        if d.day in nums:return True
    return False

def weekday_allowed(text,d):
    low=text.lower()
    day=d.strftime("%A").lower()
    aliases={"monday":["monday","mon"],"tuesday":["tuesday","tue"],"wednesday":["wednesday","wed"],
             "thursday":["thursday","thu"],"friday":["friday","fri"],"saturday":["saturday","sat"],"sunday":["sunday","sun"]}
    if re.search(rf'closed[^.;,]*\b{day}\b|\b{day}\b[^.;,]*closed',low):return False
    # Ranges such as Monday–Friday / Mon–Sat.
    order=["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    short={"mon":"monday","tue":"tuesday","wed":"wednesday","thu":"thursday","fri":"friday","sat":"saturday","sun":"sunday"}
    for a,b in re.findall(r'\b(mon|tue|wed|thu|fri|sat|sun)(?:day)?\s*[–-]\s*(mon|tue|wed|thu|fri|sat|sun)(?:day)?\b',low):
        ia=order.index(short[a]); ib=order.index(short[b]); di=order.index(day)
        if ia<=di<=ib:return True
    for fulla,fullb in re.findall(r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*[–-]\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',low):
        ia=order.index(fulla); ib=order.index(fullb); di=order.index(day)
        if ia<=di<=ib:return True
    if any(re.search(rf'\b{a}\b',low) for a in aliases[day]):return True
    return None


def hotel_date_status(hotel,start_date,end_date):
    p=pld(hotel)
    current=str(p.get("Current_Status") or "").strip().lower()
    if current and not (current=="operating" or current=="open" or current.startswith("operating ") or current.startswith("operating /") or current.startswith("open ")):
        return {"status":"CLOSED","reason":f"Current hotel status: {p.get('Current_Status')}"}
    months=p.get("Operating_Months")
    if months:
        allowed=set()
        if isinstance(months,list):
            for x in months:
                try:allowed.add(int(x))
                except:pass
        else:
            for x in re.findall(r'\b(1[0-2]|[1-9])\b',str(months)):
                allowed.add(int(x))
        d=start_date
        while d<=end_date:
            if allowed and d.month not in allowed:
                return {"status":"CLOSED","reason":f"Hotel not operating in month {d.month} according to stored schedule."}
            d+=timedelta(days=1)
        return {"status":"VERIFIED_OPEN","reason":"Stored operating months cover the trip dates."}
    # The current hotel dataset does not yet contain exact seasonal opening calendars.
    # Be explicit rather than pretending the selected dates are verified.
    return {"status":"RECHECK","reason":"Exact hotel seasonal operating calendar is not stored yet; current status is Operating."}

def day_open(r,d):
    p=pld(r); od=str(p.get("Opening_Days") or "").strip(); oh=str(p.get("Opening_Hours") or "")
    s=od.lower()
    if not s:return False,"UNKNOWN"
    selected=explicit_selected_date_match(od+"; "+oh,d)
    if selected is not None:
        return (selected,"EXACT_DATE" if selected else "NOT_SELECTED_DATE")
    # Explicit closures.
    if d.month==12 and d.day==25 and "christmas" in s:return False,"HOLIDAY_CLOSED"
    if d.month==1 and d.day==1 and "new year" in s:return False,"HOLIDAY_CLOSED"
    if any(x in s for x in ["daily","monday–sunday","monday-sunday","7 days","every day","public access","year-round","year round"]):
        return True,"VERIFIED_RULE"
    wd=weekday_allowed(od+"; "+oh,d)
    if wd is True:return True,"VERIFIED_RULE"
    if wd is False:return False,"CLOSED"
    # Season-dependent schedules can still be accepted if the hours text contains an explicit weekday rule for that season.
    if "season-dependent" in s or "season dependent" in s:
        wd=weekday_allowed(oh,d)
        if wd is not None:return wd,"SEASONAL_WEEKDAY_RULE" if wd else "CLOSED"
    # Generic by-reservation / operator-specific activities without a dated schedule remain excluded.
    return False,"UNKNOWN"

def month_ok(r,d,mode):
    p=pld(r); season=trip_season(d)
    if season=="winter" and str(p.get("Winter_Suitable") or "")=="No":return False
    if season=="summer" and str(p.get("Summer_Suitable") or "")=="No":return False
    # Seasonal text constraints in opening data.
    od=(str(p.get("Opening_Days") or "")+" "+str(p.get("Opening_Hours") or "")).lower()
    if "summer only" in od and season!="summer":return False
    if "winter only" in od and season!="winter":return False
    return True

def _first_time_range(text):
    m=re.search(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',text)
    if not m:return None
    return f"{int(m.group(1)):02d}:{m.group(2)}",f"{int(m.group(3)):02d}:{m.group(4)}"

def time_window(r,d):
    p=pld(r)
    s=str(p.get("Opening_Hours") or "")
    low=s.lower()
    # Exact selected-date workshop: use a time range from the target-month/date section when available.
    if explicit_selected_date_match(str(p.get("Opening_Days") or "")+"; "+s,d):
        tr=_first_time_range(s)
        if tr:return tr[0],tr[1],"EXACT_DATE_TEXT"
    # Exact recurring seasonal date ranges commonly used by Cyprus sites.
    exact_pairs=[
      (((4,16),(9,15)),"16 apr","15 sep"),
      (((3,1),(9,30)),"1 mar","30 sep"),
      (((4,2),(8,31)),"2 apr","31 aug"),
    ]
    md=(d.month,d.day)
    for (a,b),ka,kb in exact_pairs:
        if ka in low and kb in low:
            # first segment covers a..b; following segment covers the complementary season.
            semis=s.split(";")
            first=semis[0] if semis else s
            second=semis[1] if len(semis)>1 else ""
            chosen=first if a<=md<=b else second
            tr=_first_time_range(chosen)
            if tr:return tr[0],tr[1],"EXACT_SEASON_RANGE"
    # 16 Sep–15 Apr spans the year boundary and is usually paired with 16 Apr–15 Sep.
    if "16 sep" in low and "15 apr" in low and "16 apr" in low and "15 sep" in low:
        semis=s.split(";")
        chosen=(semis[0] if ((4,16)<=md<=(9,15)) else (semis[1] if len(semis)>1 else s))
        tr=_first_time_range(chosen)
        if tr:return tr[0],tr[1],"EXACT_SEASON_RANGE"
    # Date-season ranges such as 16 Apr–15 Sep / 16 Sep–15 Apr.
    if d.month in [4,5,6,7,8,9]:
        for key in ["summer","apr–oct","apr-oct","may–oct","may-oct","2 apr–31 aug","2 apr-31 aug","16 apr–15 sep","16 apr-15 sep","1 mar–30 sep","1 mar-30 sep","jul–aug","jul-aug"]:
            if key in low:
                seg=low[low.index(key):]
                nxt=seg.find(";")
                if nxt!=-1:seg=seg[:nxt]
                tr=_first_time_range(seg)
                if tr:return tr[0],tr[1],"SEASON_TEXT"
    else:
        for key in ["winter","nov–mar","nov-mar","sep–jun","sep-jun","1 sep–1 apr","1 sep-1 apr","16 sep–15 apr","16 sep-15 apr","1 oct–28 feb","1 oct-28 feb"]:
            if key in low:
                seg=low[low.index(key):]
                nxt=seg.find(";")
                if nxt!=-1:seg=seg[:nxt]
                tr=_first_time_range(seg)
                if tr:return tr[0],tr[1],"SEASON_TEXT"
    # Weekday-specific segments.
    day=d.strftime("%A").lower()
    short={"monday":"mon","tuesday":"tue","wednesday":"wed","thursday":"thu","friday":"fri","saturday":"sat","sunday":"sun"}[day]
    parts=re.split(r';',s)
    matching=[]
    for part in parts:
        pl=part.lower()
        if re.search(rf'\b{day}\b|\b{short}\b',pl):
            matching.append(part)
        # Accept weekday ranges if they include the selected day.
        if weekday_allowed(part,d) is True:
            matching.append(part)
    for part in matching:
        tr=_first_time_range(part)
        if tr:return tr[0],tr[1],"WEEKDAY_TEXT"
    # Parsed generic times.
    if str(p.get("Time_Slot_Usable") or "").lower()=="yes":
        try:
            o=round(float(p["Parsed_Open_Time"])*1440); c=round(float(p["Parsed_Close_Time"])*1440)
            return f"{o//60:02d}:{o%60:02d}",f"{c//60:02d}:{c%60:02d}","PARSED"
        except:pass
    tr=_first_time_range(s)
    if tr:return tr[0],tr[1],"TEXT"
    if "daylight" in low or "24" in low or "public beach access" in low:return "08:00","19:00","DAYLIGHT"
    return None,None,"UNKNOWN"

def weather_ok(r,mode,start):
    p=pld(r); io=str(p.get("Indoor_Outdoor_Both") or "").lower()
    if mode=="rainy" and str(p.get("Rain_Suitable") or "")!="Yes":return False
    if mode=="heatwave":
        hs=str(p.get("Heatwave_Suitable") or "")
        if hs=="No":return False
        if "outdoor" in io and int(r["duration_min"] or 60)>=60 and "12:00"<=start<"16:30":return False
    return True

def hard_pref_ok(r,prefs):
    rt=tags(r)
    for k,v in prefs.items():
        v=(v or "any").lower()
        if k not in PREF_RULES:continue
        if v=="no" and k in rt:return False
    return True

def yes_bonus(r,prefs):
    rt=tags(r); yes={k for k,v in prefs.items() if (v or "").lower()=="yes"}
    return 22*len(rt & yes), bool(rt & yes)

def strict_drive_ok(r,max_drive,live_enabled=False):
    if live_enabled:
        lo={"0–15 min":0,"15–30 min":15,"30–45 min":30}.get(r["travel_band"],999)
        return lo <= max_drive
    return BAND_MAX.get(r["travel_band"],999) <= max_drive

def fit(r,d,mode,desired,allow_backshift=True):
    opened,reason=day_open(r,d)
    if not opened:return None
    if not month_ok(r,d,mode):return None
    o,c,conf=time_window(r,d)
    if not o or not c:return None  # strict: unknown operating time excluded
    dur=max(15,int(r["duration_min"] or 60))
    start=max(desired,o)
    end=addm(start,dur)
    if end>c:
        if not allow_backshift:return None
        ch,cm=map(int,c.split(":")); latest=ch*60+cm-dur
        oh,om=map(int,o.split(":"))
        if latest<oh*60+om:return None
        start=f"{latest//60:02d}:{latest%60:02d}"; end=addm(start,dur)
    if not weather_ok(r,mode,start):return None
    p=pld(r)
    return {"start":start,"end":end,"booking":str(p.get("Booking_Required") or "").lower()=="yes",
            "opening_confidence":conf}

def score(r,hotel_cluster,prefs):
    b,_=yes_bonus(r,prefs)
    s=float(r["romantic_score"] or 0)*3+float(r["authentic_score"] or 0)+PROX.get(r["travel_band"],0)+b
    if r["cluster_id"]==hotel_cluster:s+=8
    return s

def candidates(c,req,hotel,d,desired,used,route_cache=None,allow_backshift=True):
    mode=req.weather_mode
    route_cache=route_cache if route_cache is not None else {}
    live_enabled=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    rows=c.execute("""select a.*,m.travel_band,m.recommended_day1
      from activity a join hotel_activity_mapping m on m.activity_id=a.activity_id
      where m.hotel_id=? and a.data_status='Verified'""",(req.hotel_id,)).fetchall()
    pre=[]
    for r in rows:
        if r["activity_id"] in used:continue
        if r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
        if not strict_drive_ok(r,req.max_drive_min,live_enabled):continue
        if not hard_pref_ok(r,req.prefs):continue
        if not activity_hotel_compatible(r,hotel,req):continue
        if trip_season(d)=="winter":
            explicit_sea=any((req.prefs.get(k,"any") or "any").lower()=="yes" for k in ["sea","swimming","beach","boat","diving"])
            if not explicit_sea and any(k in tags(r) for k in ["sea","swimming","beach","boat","diving"]):
                continue
        if not budget_ok_activity(r,req):continue
        op=fit(r,d,mode,desired,allow_backshift=allow_backshift)
        if not op:continue
        pre.append((score(r,hotel["cluster_id"],req.prefs),r,op))
    pre.sort(key=lambda x:x[0],reverse=True)
    if not live_enabled:return pre
    out=[]
    origin=hotel_location(hotel)
    # Query only the highest-ranked candidates to control API usage.
    for base,r,op in pre[:18]:
        rt=live_route(origin,row_location(r),route_cache)
        if not rt:continue
        if rt["minutes"]>req.max_drive_min:continue
        op=dict(op); op["live_route"]=rt
        out.append((base,r,op))
    return out

def is_swim_experience(r):
    t=txt(r); tg=tags(r)
    # A real sea/swim block: beach, lagoon/charter/cruise with swimming potential, not merely "sea" in a spa/museum name.
    return ("swimming" in tg) or any(x in t for x in ["blue lagoon","private charter","boat cruise"])

def is_indoor(r):
    return "indoor" in str(pld(r).get("Indoor_Outdoor_Both") or "").lower()

def weather_priority(r,req,d):
    mode=req.weather_mode
    t=txt(r); bonus=0
    if mode=="rainy":
        if is_indoor(r): bonus += 40
        if any(k in tags(r) for k in ["museums","crafts","cooking","wellness"]): bonus += 25
    elif mode=="heatwave":
        if is_swim_experience(r): bonus += 35
        if is_indoor(r): bonus += 28
    elif mode=="winter":
        if is_indoor(r): bonus += 28
        if any(k in tags(r) for k in ["museums","crafts","winery","local_food","wellness","villages"]): bonus += 22
        explicit_sea=any((req.prefs.get(k,"any") or "any").lower()=="yes" for k in ["sea","swimming","beach","boat","diving"])
        if not explicit_sea and any(k in tags(r) for k in ["swimming","beach","sea","boat","diving"]):
            bonus -= 45
    else:
        # warm-season normal weather: make sea/swim materially visible
        if d.month in [5,6,7,8,9,10] and is_swim_experience(r): bonus += 20
    return bonus

def pick_primary(c,req,hotel,d,day,used,covered_yes=None,force_swim=False,avoid_swim=False,route_cache=None,family_counts=None,last_cluster=None):
    if req.weather_mode=="heatwave":
        desired="17:00" if day==1 else "09:30"
    elif req.weather_mode=="rainy":
        desired="16:00" if day==1 else "10:30"
    else:
        desired="16:30" if day==1 else "10:30"
    cs=candidates(c,req,hotel,d,desired,used,route_cache,allow_backshift=(day!=1))
    yes={k for k,v in req.prefs.items() if (v or "").lower()=="yes"}
    covered_yes=covered_yes or set()
    uncovered=yes-covered_yes
    rescored=[]
    for base,r,op in cs:
        sc=base+weather_priority(r,req,d)+style_bonus(r,req)
        if tags(r)&uncovered: sc+=55
        fam=category_family(r)
        if family_counts:
            sc -= family_counts.get(fam,0)*35
        if last_cluster and r["cluster_id"]==last_cluster:sc+=10
        if force_swim and is_swim_experience(r): sc+=1000
        rescored.append((sc,r,op))
    rescored.sort(key=lambda x:x[0],reverse=True)
    # If an uncovered YES preference has a valid candidate on this exact date,
    # primary selection must come from those preferred candidates.
    preferred_now=[x for x in rescored if tags(x[1]) & uncovered]
    if preferred_now:
        rescored=preferred_now
    if day==1:
        local_day1=[x for x in rescored if x[1]["cluster_id"]==hotel["cluster_id"]]
        if local_day1:
            rescored=local_day1
    if force_swim:
        swim=[x for x in rescored if is_swim_experience(x[1])]
        if swim:return swim[0]
    if avoid_swim:
        non=[x for x in rescored if not is_swim_experience(x[1])]
        if non:return non[0]
    return rescored[0] if rescored else None

def pick_secondary(c,req,hotel,d,primary,used,covered_yes=None,route_cache=None,family_counts=None,not_before=None):
    # Full days should normally contain a second nearby experience when it genuinely fits.
    base_desired="16:45" if req.weather_mode=="heatwave" else ("15:00" if req.weather_mode=="rainy" else "15:30")
    earliest=addm(primary["op"]["end"],45)
    desired=max(base_desired,earliest,not_before or "00:00")
    if desired>="18:15":return None
    cs=candidates(c,req,hotel,d,desired,used,route_cache)
    yes={k for k,v in req.prefs.items() if (v or "").lower()=="yes"}
    uncovered=yes-(covered_yes or set())
    live_enabled=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    rescored=[]
    for base,r,op in cs:
        # Without live routing, require same geographic cluster for the second activity.
        if not live_enabled and r["cluster_id"]!=primary["row"]["cluster_id"]:
            continue
        # With live routing, require the real leg from activity 1 to activity 2 to respect max drive.
        seg=None
        if live_enabled:
            seg=live_route(row_location(primary["row"]),row_location(r),route_cache or {})
            if not seg or seg["minutes"]>req.max_drive_min:
                continue
        dur=int(r["duration_min"] or 60)
        if req.pace=="relaxed" and dur>75:continue
        if req.pace=="balanced" and dur>120:continue
        sc=base+weather_priority(r,req,d)+style_bonus(r,req)
        if tags(r)&uncovered:sc+=45
        if r["cluster_id"]==primary["row"]["cluster_id"]:sc+=55
        if family_counts:sc-=family_counts.get(category_family(r),0)*30
        op=dict(op)
        if seg:op["segment_live_route"]=seg
        rescored.append((sc,r,op))
    rescored.sort(key=lambda x:x[0],reverse=True)
    preferred_nearby=[x for x in rescored if tags(x[1]) & uncovered]
    if preferred_nearby:
        rescored=preferred_nearby
    sunset=sunset_local(primary["row"]["cluster_id"],d)
    sunset_start=addm(sunset,-40)
    for sc,r,op in rescored:
        if category_family(r)==category_family(primary["row"]):continue
        # Viewpoints are more attractive near sunset if they are open then.
        if "viewpoints" in tags(r) and req.weather_mode not in ["rainy"]:
            late=fit(r,d,req.weather_mode,sunset_start)
            if late and late["end"]<=addm(sunset,20):
                late=dict(late)
                if op.get("segment_live_route"):late["segment_live_route"]=op["segment_live_route"]
                op=late
        if op["start"]<desired or op["end"]>"19:15":continue
        return sc,r,op
    return None


def restaurant_open_at(r,d,hhmm):
    p=pld(r); days=str(p.get("Opening_Days") or "").lower(); hrs=str(p.get("Opening_Hours") or "").lower()
    if not days or "confirm" in days or "check weekly" in days or "schedule applies" in days:return False
    if "summer only" in days and d.month not in [5,6,7,8,9,10]:return False
    if "may–october" in days or "may-october" in days:
        if d.month not in [5,6,7,8,9,10]:return False
    elif not any(x in days for x in ["daily","monday–sunday","monday-sunday","7 days","every day","all year"]):
        day=d.strftime("%A").lower()
        aliases={"monday":["monday","mon"],"tuesday":["tuesday","tue"],"wednesday":["wednesday","wed"],
                 "thursday":["thursday","thu"],"friday":["friday","fri"],"saturday":["saturday","sat"],"sunday":["sunday","sun"]}
        if re.search(rf'closed[^.;,]*\b{day}\b|\b{day}\b[^.;,]*closed',days):return False
        if not any(re.search(rf'\b{a}\b',days) for a in aliases[day]):return False
    target=int(hhmm[:2])*60+int(hhmm[3:])
    spans=re.findall(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',hrs)
    for h1,m1,h2,m2 in spans:
        a=int(h1)*60+int(m1); b=int(h2)*60+int(m2)
        if a<=target<=b:return True
    return False


def public_rating_bonus(r):
    p=pld(r)
    try: rating=float(p.get("Current_Rating_5") or 0)
    except: rating=0
    try: reviews=int(p.get("Current_Review_Count") or 0)
    except: reviews=0
    b=0
    if rating>=4.8:b+=18
    elif rating>=4.6:b+=14
    elif rating>=4.4:b+=8
    if reviews>=2000:b+=14
    elif reviews>=500:b+=10
    elif reviews>=150:b+=6
    return b

def venue_public_meta(r):
    p=pld(r)
    return {"rating_5":p.get("Current_Rating_5"),"review_count":p.get("Current_Review_Count"),
            "venue_type":p.get("Venue_Type"),"price_range":p.get("Price_Range")}


def hotel_restaurant_repeat_exception(r,hotel):
    """Allow dinner repetition only for the selected hotel's own restaurant
    when stored public-review evidence is genuinely strong."""
    if owner_hotel_id(r)!=hotel["hotel_id"]:
        return False
    p=pld(r)
    try: rating=float(p.get("Current_Rating_5") or 0)
    except: rating=0
    try: reviews=int(p.get("Current_Review_Count") or 0)
    except: reviews=0
    return rating>=4.6 and reviews>=150


def owner_hotel_open_at(r,hotel,d,hhmm):
    if owner_hotel_id(r)!=hotel["hotel_id"]:
        return restaurant_open_at(r,d,hhmm)
    p=pld(r); hrs=str(p.get("Opening_Hours") or "")
    target=int(hhmm[:2])*60+int(hhmm[3:])
    spans=re.findall(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',hrs)
    for h1,m1,h2,m2 in spans:
        a=int(h1)*60+int(m1); b=int(h2)*60+int(m2)
        if a<=target<=b:return True
    return restaurant_open_at(r,d,hhmm)

def owner_hotel_open_for_dinner(r,hotel,d):
    if owner_hotel_id(r)!=hotel["hotel_id"]:
        return restaurant_open_for_dinner(r,d)
    return owner_hotel_open_at(r,hotel,d,"20:00")

def lunch_stop(c,req,hotel,d,used,route_cache=None,preferred_cluster=None,avoid_ids=None,target_time='13:15'):
    route_cache=route_cache if route_cache is not None else {}
    avoid_ids=set(avoid_ids or [])
    live_enabled=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    rows=c.execute("""select r.*,m.travel_band from restaurant r join hotel_restaurant_mapping m
      on m.restaurant_id=r.restaurant_id where m.hotel_id=? and r.data_status='Verified'""",(req.hotel_id,)).fetchall()
    valid=[]
    for r in rows:
        if not restaurant_hotel_compatible(r,hotel):continue
        if r["restaurant_id"] in avoid_ids or r["restaurant_id"] in used:continue
        if "lunch" not in (r["meal_type"] or "").lower():continue
        if r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
        if not owner_hotel_open_at(r,hotel,d,target_time):continue
        if not strict_drive_ok(r,req.max_drive_min,live_enabled):continue
        rt=None
        if live_enabled:
            rt=live_route(hotel_location(hotel),row_location(r),route_cache)
            if not rt or rt["minutes"]>req.max_drive_min:continue
        sc=float(r["romantic_score"] or 0)*2.5+float(r["authentic_score"] or 0)*(1.8 if req.couple_style in ["authentic","food_wine"] else .6)+public_rating_bonus(r)
        if owner_hotel_id(r)==hotel["hotel_id"]:sc+=45
        if preferred_cluster and r["cluster_id"]==preferred_cluster:sc+=28
        valid.append((sc,r,rt))
    if not valid:
        # Allow a repeat from another day if it is genuinely open; never invent a lunch venue.
        for r in rows:
            if r["restaurant_id"] in avoid_ids:continue
            if "lunch" not in (r["meal_type"] or "").lower():continue
            if r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
            if not owner_hotel_open_at(r,hotel,d,target_time):continue
            if not strict_drive_ok(r,req.max_drive_min,live_enabled):continue
            rt=None
            if live_enabled:
                rt=live_route(hotel_location(hotel),row_location(r),route_cache)
                if not rt or rt["minutes"]>req.max_drive_min:continue
            sc=float(r["romantic_score"] or 0)*2.5+float(r["authentic_score"] or 0)+public_rating_bonus(r)
            if preferred_cluster and r["cluster_id"]==preferred_cluster:sc+=28
            valid.append((sc,r,rt))
    if not valid:return None
    valid.sort(key=lambda x:x[0],reverse=True)
    _,r,rt=valid[0]; used.add(r["restaurant_id"])
    return {"row":r,"live_route":rt}

def coffee_stop(c,req,hotel,d,used,route_cache=None,preferred_cluster=None,avoid_ids=None):
    route_cache=route_cache if route_cache is not None else {}
    avoid_ids=set(avoid_ids or [])
    live_enabled=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    rows=c.execute("""select r.*,m.travel_band from restaurant r join hotel_restaurant_mapping m
      on m.restaurant_id=r.restaurant_id where m.hotel_id=? and r.data_status='Verified'""",(req.hotel_id,)).fetchall()
    valid=[]
    for r in rows:
        if not restaurant_hotel_compatible(r,hotel):continue
        meal=(r["meal_type"] or "").lower()
        if not any(k in meal for k in ["coffee","drinks","light"]):continue
        if r["restaurant_id"] in avoid_ids or r["restaurant_id"] in used:continue
        if not owner_hotel_open_at(r,hotel,d,"16:30"):continue
        if not strict_drive_ok(r,req.max_drive_min,live_enabled):continue
        if r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
        rt=None
        if live_enabled:
            rt=live_route(hotel_location(hotel),row_location(r),route_cache)
            if not rt or rt["minutes"]>req.max_drive_min:continue
        sc=float(r["romantic_score"] or 0)*3+float(r["authentic_score"] or 0)*1.5+public_rating_bonus(r)
        if owner_hotel_id(r)==hotel["hotel_id"]:sc+=35
        vp=str(pld(r).get("Venue_Type") or "").lower()+" "+(r["meal_type"] or "").lower()
        if any(k in vp for k in ["view","sunset","sea-view"]):sc+=18
        if preferred_cluster and r["cluster_id"]==preferred_cluster:sc+=25
        if req.couple_style=="authentic" and float(r["authentic_score"] or 0)>=8:sc+=15
        valid.append((sc,r,rt))
    if not valid:return None
    valid.sort(key=lambda x:x[0],reverse=True)
    _,r,rt=valid[0]; used.add(r["restaurant_id"])
    return {"row":r,"live_route":rt}

def restaurant_open_for_dinner(r,d):
    p=pld(r); days=str(p.get("Opening_Days") or "").lower(); hrs=str(p.get("Opening_Hours") or "").lower()
    # strict day/month checks
    if "check weekly" in days or not days:return False
    if "may–october" in days or "may-october" in days:
        if d.month not in [5,6,7,8,9,10]:return False
    elif not any(x in days for x in ["daily","monday–sunday","monday-sunday","7 days","every day"]):
        day=d.strftime("%A").lower()
        aliases={"monday":["monday","mon"],"tuesday":["tuesday","tue"],"wednesday":["wednesday","wed"],
                 "thursday":["thursday","thu"],"friday":["friday","fri"],"saturday":["saturday","sat"],"sunday":["sunday","sun"]}
        if re.search(rf'closed[^.;,]*\b{day}\b|\b{day}\b[^.;,]*closed',days):return False
        if not any(re.search(rf'\b{a}\b',days) for a in aliases[day]):return False
    # dinner must include 20:00 within an explicit time window
    spans=re.findall(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',hrs)
    if not spans:return False
    for h1,m1,h2,m2 in spans:
        start=int(h1)*60+int(m1); end=int(h2)*60+int(m2)
        if start<=20*60<=end:return True
    return False

def dinner(c,req,hotel,used,d,route_cache=None,avoid_ids=None,dinner_counts=None,last_dinner_id=None):
    route_cache=route_cache if route_cache is not None else {}
    avoid_ids=set(avoid_ids or [])
    dinner_counts=dinner_counts if dinner_counts is not None else {}
    live_enabled=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    rows=c.execute("""select r.*,m.travel_band from restaurant r join hotel_restaurant_mapping m
      on m.restaurant_id=r.restaurant_id where m.hotel_id=? and r.data_status='Verified'""",(req.hotel_id,)).fetchall()

    def eligible(r):
        if not restaurant_hotel_compatible(r,hotel):return False
        if r["restaurant_id"] in avoid_ids:return False
        if "dinner" not in (r["meal_type"] or "").lower():return False
        if r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":return False
        if not owner_hotel_open_for_dinner(r,hotel,d):return False
        if not strict_drive_ok(r,req.max_drive_min,live_enabled):return False
        return True

    def route_if_ok(r):
        if not live_enabled:return None,True
        rt=live_route(hotel_location(hotel),row_location(r),route_cache)
        return rt,bool(rt and rt["minutes"]<=req.max_drive_min)

    def dinner_score(r,repeat_count=0):
        sc=(float(r["romantic_score"] or 0)*3+
            float(r["authentic_score"] or 0)*(2 if req.couple_style in ["authentic","food_wine"] else .7)+
            public_rating_bonus(r)+PROX.get(r["travel_band"],0)+
            (45 if owner_hotel_id(r)==hotel["hotel_id"] else 0)+
            (8 if r["cluster_id"]==hotel["cluster_id"] else 0))
        # Strong diversity pressure: even an allowed hotel repeat should lose
        # to a similarly suitable unused restaurant.
        sc-=repeat_count*65
        if last_dinner_id and r["restaurant_id"]==last_dinner_id:
            sc-=45
        return sc

    # First pass: never repeat a dinner venue while an unused valid venue exists.
    valid=[]
    for r in rows:
        if not eligible(r):continue
        if dinner_counts.get(r["restaurant_id"],0)>0 or r["restaurant_id"] in used:continue
        rt,ok=route_if_ok(r)
        if not ok:continue
        valid.append((dinner_score(r,0),r,rt,"NEW"))

    # Second pass: repetition is permitted ONLY for a highly reviewed restaurant
    # owned by the selected hotel. External restaurant repetition remains forbidden.
    if not valid:
        for r in rows:
            if not eligible(r):continue
            count=dinner_counts.get(r["restaurant_id"],0)
            if count<=0:continue
            if not hotel_restaurant_repeat_exception(r,hotel):continue
            rt,ok=route_if_ok(r)
            if not ok:continue
            valid.append((dinner_score(r,count),r,rt,"HOTEL_HIGH_RATING_REPEAT"))

    if not valid:return None
    valid.sort(key=lambda x:x[0],reverse=True)
    _,r,rt,repeat_policy=valid[0]
    rid=r["restaurant_id"]
    used.add(rid)
    dinner_counts[rid]=dinner_counts.get(rid,0)+1
    return {"row":r,"live_route":rt,"repeat_policy":repeat_policy,
            "dinner_use_count":dinner_counts[rid]}


def obj(r,op,req):
    rt=op.get("live_route")
    mins=rt["minutes"] if rt else BAND_PLAN.get(r["travel_band"],60)
    return {"entity_id":r["activity_id"],"title":r["name"],"category":r["category"],"subcategory":r["subcategory"],
      "tags":sorted(tags(r)),"cluster_id":r["cluster_id"],"start_time":op["start"],"end_time":op["end"],"travel_band":r["travel_band"],
      "travel_minutes":mins,"travel_distance_km":rt["distance_km"] if rt else None,
      "travel_source":rt["provider"] if rt else "PLANNING_BAND","location_text":row_location(r),
      "booking_required":op["booking"],"opening_check":"OPEN","opening_confidence":op["opening_confidence"],
      "data_status":r["data_status"]}

def timeline(hotel,day,a,b,lunch,din,coffee,weather_mode,route_cache=None):
    route_cache=route_cache if route_cache is not None else {}
    out=[]; live_enabled=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    if day==1:
        out += [{"time":"11:00–11:30","kind":"hotel","title":"Arrival, check-in & settle in"},
                {"time":"11:30–12:45","kind":"rest","title":"Room / hotel time"}]
    else:
        out.append({"time":"08:30–09:30","kind":"hotel","title":"Breakfast & unhurried start"})

    # Day 1 lunch comes before the late-afternoon primary. Full-day lunch follows the morning primary.
    if day==1:
        if lunch:
            rr=lunch["row"]; rt=lunch.get("live_route")
            lt=rt["minutes"] if rt else BAND_PLAN.get(rr["travel_band"],10)
            out += [{"time":f"{addm('13:00',-lt)}–13:00","kind":"travel","title":f"Hotel → {rr['name']} (~{lt} min {'live' if rt else 'planning'})"},
                    {"time":"13:00–14:15","kind":"meal","title":f"Lunch – {rr['name']}"},
                    {"time":"14:15–16:00","kind":"rest","title":"Return / rest / pool / room"}]
        else:
            out += [{"time":"13:00","kind":"warning","title":"No verified named lunch venue available in the current database for this date/time."},
                    {"time":"13:00–16:00","kind":"rest","title":"Hotel time / rest"}]

    at=a["travel_minutes"]; depart=addm(a["start_time"],-at)
    src="live" if a["travel_source"]=="GOOGLE_ROUTES" else "planning"
    out += [{"time":f"{depart}–{a['start_time']}","kind":"travel","title":f"Hotel → {a['title']} (~{at} min {src})"},
            {"time":f"{a['start_time']}–{a['end_time']}","kind":"activity","title":a["title"]}]
    cursor=a["end_time"]; current_loc=a["location_text"]

    if day>1:
        lunch_start=(lunch.get("planned_time") if lunch else None) or ("13:00" if weather_mode=="heatwave" else "13:15")
        lunch_end=addm(lunch_start,75)
        if lunch:
            rr=lunch["row"]
            seg=live_route(current_loc,row_location(rr),route_cache) if live_enabled else lunch.get("live_route")
            lt=seg["minutes"] if seg else BAND_PLAN.get(rr["travel_band"],10)
            depart_l=addm(lunch_start,-lt)
            if depart_l>cursor:
                out.append({"time":f"{cursor}–{depart_l}","kind":"buffer","title":"Short free time"})
            out += [{"time":f"{depart_l}–{lunch_start}","kind":"travel","title":f"Activity → {rr['name']} (~{lt} min {'live' if seg else 'planning'})"},
                    {"time":f"{lunch_start}–{lunch_end}","kind":"meal","title":f"Lunch – {rr['name']}"}]
            cursor=lunch_end; current_loc=row_location(rr)
        else:
            out.append({"time":lunch_start,"kind":"warning","title":"No verified named lunch venue available in the current database for this date/time."})
            cursor=lunch_end
        if weather_mode=="heatwave":
            out.append({"time":f"{cursor}–16:30","kind":"rest","title":"Hotel / shaded rest during peak heat"})
            cursor="16:30"; current_loc=hotel_location(hotel)

    if b:
        seg=live_route(current_loc,b["location_text"],route_cache) if live_enabled else b.get("segment_live_route")
        bt=seg["minutes"] if seg else b["travel_minutes"]
        dep=addm(b["start_time"],-bt)
        if dep>cursor:
            out.append({"time":f"{cursor}–{dep}","kind":"buffer","title":"Free time / short rest"})
        src="live" if seg else "planning"
        out += [{"time":f"{dep}–{b['start_time']}","kind":"travel","title":f"Nearby transfer → {b['title']} (~{bt} min {src})"},
                {"time":f"{b['start_time']}–{b['end_time']}","kind":"activity","title":b["title"]}]
        cursor=b["end_time"]; current_loc=b["location_text"]

    if coffee and cursor<="17:30":
        rr=coffee["row"]; rt=live_route(current_loc,row_location(rr),route_cache) if live_enabled else coffee.get("live_route")
        ct=rt["minutes"] if rt else BAND_PLAN.get(rr["travel_band"],10)
        cstart=max("16:30",addm(cursor,ct)); cend=addm(cstart,45)
        if cend<="18:45":
            out += [{"time":f"{addm(cstart,-ct)}–{cstart}","kind":"travel","title":f"Travel to {rr['name']} (~{ct} min {'live' if rt else 'planning'})"},
                    {"time":f"{cstart}–{cend}","kind":"coffee","title":f"{rr['name']} – coffee / drink"}]
            cursor=cend; current_loc=row_location(rr)

    if din:
        rr=din["row"]; restaurant_loc=row_location(rr)
        seg=live_route(current_loc,restaurant_loc,route_cache) if live_enabled else None
        dt=seg["minutes"] if seg else (din["live_route"]["minutes"] if din.get("live_route") else BAND_PLAN.get(rr["travel_band"],10))
        dep=addm("20:00",-dt)
        if dep>cursor:
            out.append({"time":f"{cursor}–{dep}","kind":"buffer","title":"Return / freshen up / pre-dinner rest"})
        out += [{"time":f"{dep}–20:00","kind":"travel","title":f"Current stop → {rr['name']} (~{dt} min {'live' if seg else 'planning'})"},
                {"time":"20:00–21:30","kind":"meal","title":f"Dinner – {rr['name']}"}]
        ret=live_route(restaurant_loc,hotel_location(hotel),route_cache) if live_enabled else None
        retmin=ret["minutes"] if ret else (din["live_route"]["minutes"] if din.get("live_route") else BAND_PLAN.get(rr["travel_band"],10))
        out.append({"time":"after dinner","kind":"travel","title":f"{rr['name']} → {hotel['name']} (~{retmin} min {'live' if ret else 'planning'})"})
    else:
        out.append({"time":"20:00","kind":"warning","title":"No unused verified dinner venue is available under the current rules. External restaurant repetition is not allowed; a hotel restaurant may repeat only with stored rating ≥4.6/5 and ≥150 reviews."})
    return out

@app.get("/api/v1/health")
def health():
    return {"service":"ok","logic_version":"v62-dinner-diversity",
            "routing_provider":"GOOGLE_ROUTES" if os.getenv("GOOGLE_MAPS_API_KEY","").strip() else "NOT_CONNECTED",
            "weather_provider":"OPEN_METEO_AUTO",
            "drive_filter":"LIVE_HARD_LIMIT" if os.getenv("GOOGLE_MAPS_API_KEY","").strip() else "STRICT_CONSERVATIVE_MAPPING_BANDS",
            "opening_filter":"STRICT_KNOWN_OPEN_ONLY"}

@app.get("/api/v1/meta/hotels")
def hotels():
    with conn() as c:return [dict(r) for r in c.execute("select hotel_id,name,area,cluster_id from hotel order by name")]

@app.post("/api/v1/trips/generate")
def generate(req:TripRequest):
    with conn() as c:
        hotel=c.execute("select * from hotel where hotel_id=?",(req.hotel_id,)).fetchone()
        if not hotel:raise HTTPException(422,"Unknown hotel_id")
        total_days=min(req.nights+1,7)
        trip_end=req.start_date+timedelta(days=total_days-1)
        hotel_op=hotel_date_status(hotel,req.start_date,trip_end)
        if hotel_op["status"]=="CLOSED":
            raise HTTPException(422,detail={"message":"Selected hotel is not operating for the requested dates.","hotel_operation":hotel_op})

        used=set(); lunch_used=set(); coffee_used=set(); dinner_used=set(); dinner_counts={}; last_dinner_id=None; days=[]; route_cache={}
        family_counts={}; last_cluster=None
        yes={k for k,v in req.prefs.items() if (v or "").lower()=="yes"}
        covered=set()
        forecast=live_weather(hotel["cluster_id"],req.start_date,total_days) if req.weather_mode=="auto" else {}

        def mode_for(d):
            if req.weather_mode!="auto":return req.weather_mode
            item=forecast.get(str(d))
            if item:return item["mode"]
            return "winter" if d.month in [12,1,2] else "normal"

        warm_trip=req.start_date.month in [5,6,7,8,9,10] and mode_for(req.start_date) in ["normal","heatwave"]
        sea_requested=("sea" in yes or "swimming" in yes or req.couple_style=="sea_lovers")
        require_swim=total_days>=4 and warm_trip and sea_requested
        swim_covered=False; swim_count=0; last_primary_was_swim=False

        for i in range(1,total_days+1):
            d=req.start_date+timedelta(days=i-1)
            day_mode=mode_for(d)
            day_req=req.model_copy(update={"weather_mode":day_mode})
            force_swim=require_swim and (not swim_covered) and i>=2 and day_mode!="rainy"
            swimming_explicit=(req.prefs.get("swimming","any") or "any").lower()=="yes"
            avoid_swim=last_primary_was_swim or (swim_count>=2 and not swimming_explicit)
            if force_swim:avoid_swim=False

            p=pick_primary(c,day_req,hotel,d,i,used,covered_yes=covered,force_swim=force_swim,
                           avoid_swim=avoid_swim,route_cache=route_cache,
                           family_counts=family_counts,last_cluster=last_cluster)
            if not p:
                days.append({"day":i,"date":str(d),"title":"No valid itinerary day",
                    "weather_mode_used":day_mode,
                    "operational_warning":"No Verified activity was confirmed open at a usable time for this exact date under the selected filters.",
                    "timeline":[]})
                continue

            _,r,op=p; used.add(r["activity_id"]); covered |= tags(r)&yes
            fam=category_family(r); family_counts[fam]=family_counts.get(fam,0)+1
            last_cluster=r["cluster_id"]
            if is_swim_experience(r):
                swim_covered=True; swim_count+=1; last_primary_was_swim=True
            else:last_primary_was_swim=False
            pa=obj(r,op,day_req)
            holder={"row":r,"op":op,"travel":pa["travel_minutes"]}

            # Named lunch is chosen for the exact date/time and preferably the same activity cluster.
            if i==1:
                lunch_time="13:00"
            elif op["end"]<="12:30":
                lunch_time="13:15"
            else:
                # Long morning activities push lunch later instead of overlapping it.
                lunch_time=addm(op["end"],30)
                if lunch_time>"15:30":lunch_time="15:30"
            lun=lunch_stop(c,day_req,hotel,d,lunch_used,route_cache=route_cache,
                           preferred_cluster=r["cluster_id"],target_time=lunch_time)
            if lun:lun["planned_time"]=lunch_time

            sec=None
            if i>1:
                secondary_not_before=addm(lunch_time,105)  # 75 min lunch + 30 min transition
                q=pick_secondary(c,day_req,hotel,d,holder,used,covered_yes=covered,
                                 route_cache=route_cache,family_counts=family_counts,
                                 not_before=secondary_not_before)
                if q:
                    _,rr,oo=q; used.add(rr["activity_id"]); covered |= tags(rr)&yes
                    sfam=category_family(rr); family_counts[sfam]=family_counts.get(sfam,0)+1
                    sec=obj(rr,oo,day_req)
                    if oo.get("segment_live_route"):sec["segment_live_route"]=oo["segment_live_route"]
                    if is_swim_experience(rr):swim_covered=True

            same_day={lun["row"]["restaurant_id"]} if lun else set()
            preferred_cluster=(sec["cluster_id"] if sec else r["cluster_id"])
            coff=coffee_stop(c,day_req,hotel,d,coffee_used,route_cache=route_cache,
                             preferred_cluster=preferred_cluster,avoid_ids=same_day)
            if coff:same_day.add(coff["row"]["restaurant_id"])
            din=dinner(c,day_req,hotel,dinner_used,d,route_cache=route_cache,avoid_ids=same_day,
                       dinner_counts=dinner_counts,last_dinner_id=last_dinner_id)
            if din:last_dinner_id=din["row"]["restaurant_id"]

            if lun and "local_food" in yes and float(lun["row"]["authentic_score"] or 0)>=8:covered.add("local_food")
            if din and "local_food" in yes and float(din["row"]["authentic_score"] or 0)>=8:covered.add("local_food")

            tl=timeline(hotel,i,pa,sec,lun,din,coff,day_mode,route_cache=route_cache)
            weather_info=forecast.get(str(d))
            sunset=sunset_local(r["cluster_id"],d)
            day_title="Romantic discovery"
            if is_swim_experience(r):day_title="Sea & slow romance"
            elif "viewpoints" in tags(r):day_title="Scenic & sunset"
            elif any(k in tags(r) for k in ["crafts","villages","local_food"]):day_title="Authentic Cyprus"
            elif "winery" in tags(r):day_title="Wine & countryside"
            elif any(k in tags(r) for k in ["museums","archaeology","religious"]):day_title="Culture & heritage"
            elif any(k in tags(r) for k in ["golf","horse","cycling","hiking","diving"]):day_title="Active couple day"

            days.append({"day":i,"date":str(d),"title":day_title,"season":trip_season(d),"weather_mode_used":day_mode,
              "sunset_local":sunset,
              "weather":{"source":"OPEN_METEO_LIVE_FORECAST",**weather_info} if weather_info else {"source":"SEASONAL_PLANNING_NOT_LIVE_FORECAST","mode":day_mode,"confidence":"seasonal"},
              "activity":pa,"secondary_activity":sec,
              "lunch":{"entity_id":lun["row"]["restaurant_id"],"title":lun["row"]["name"],**venue_public_meta(lun["row"])} if lun else None,
              "coffee":{"entity_id":coff["row"]["restaurant_id"],"title":coff["row"]["name"],**venue_public_meta(coff["row"])} if coff else None,
              "dinner":{"entity_id":din["row"]["restaurant_id"],"title":din["row"]["name"],**venue_public_meta(din["row"]),"travel_band":din["row"]["travel_band"],
                        "travel_minutes":din["live_route"]["minutes"] if din.get("live_route") else BAND_PLAN.get(din["row"]["travel_band"],60),
                        "travel_source":"GOOGLE_ROUTES" if din.get("live_route") else "PLANNING_BAND",
                         "repeat_policy":din.get("repeat_policy"),"trip_dinner_use_count":din.get("dinner_use_count")} if din else None,
              "timeline":tl,
              "timeline_qa":{"external_activity_count":1+(1 if sec else 0),
                 "activity_family":fam,"primary_cluster":r["cluster_id"],
                 "secondary_pairing":"SAME_CLUSTER_OR_LIVE_NEARBY" if sec else "NO_VALID_NEARBY_SECONDARY",
                 "status":"PASS"}})

        unmet=sorted(yes-covered)
        if require_swim and not swim_covered and "swimming / sea bathing" not in unmet:unmet.append("swimming / sea bathing")

    routing_live=bool(os.getenv("GOOGLE_MAPS_API_KEY","").strip())
    warnings=[]
    if hotel_op["status"]=="RECHECK":
        warnings.append(hotel_op["reason"])
    if not routing_live:
        warnings.append("Google Routes is not connected. Nearby pairing uses strict geographic clusters and conservative travel bands.")
    if not forecast and req.weather_mode=="auto":
        warnings.append("The trip dates are outside the live forecast window; this is seasonal planning, not an actual weather forecast. The itinerary should refresh automatically when the dates enter the live forecast window.")

    return {"trip_id":"trp_"+uuid.uuid4().hex[:10],
      "status":"LIVE_READY" if routing_live else "STRICT_FALLBACK_READY",
      "hotel_operation":hotel_op,
      "experience_profile":{"couple_style":req.couple_style,"budget":req.budget,"special_occasion":req.special_occasion},
      "provider_state":{"routing":"GOOGLE_ROUTES_LIVE" if routing_live else "NOT_CONNECTED",
                        "weather":"OPEN_METEO_LIVE" if forecast else ("USER_SCENARIO" if req.weather_mode!="auto" else "SEASONAL_PLANNING_NOT_FORECAST")},
      "strict_rules":{"activity_date":"EXACT_DAY_AND_USABLE_HOURS_REQUIRED","day1_sequence":"ARRIVAL_LUNCH_REST_THEN_ACTIVITY_NO_OVERLAP","yes_preferences":"MUST_COVER_WHEN_FEASIBLE","hotel_spa":"NEVER_USE_OTHER_HOTEL_SPA","hotel_dining":"NEVER_USE_OTHER_HOTEL_RESTAURANT","season_weather":"DATE_SEASON_PLUS_WEATHER_COMBINED",
        "closed_or_unknown_opening":"EXCLUDED",
        "max_drive":"LIVE_HARD_LIMIT" if routing_live else "HARD_CONSERVATIVE_BAND_FILTER",
        "explicit_no_preferences":"EXCLUDED","unverified_activities":"EXCLUDED",
        "lunch":"NAMED_VERIFIED_OPEN_VENUE_ONLY",
        "dinner_diversity":"NO_EXTERNAL_REPEATS; HOTEL REPEAT ONLY WITH STORED RATING >=4.6 AND >=150 REVIEWS",
        "secondary_activity":"SAME_CLUSTER_OR_LIVE_NEARBY_AND_TIME_FIT"},
      "unmet_yes_preferences":unmet,"days":days,"warnings":warnings}

HTML=r"""<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cyprus Romantic Trip Planner</title><style>
body{font-family:Arial;margin:0;background:#f6f3ee;color:#203036}header{background:#294c55;color:white;padding:20px}main{max-width:1100px;margin:auto;padding:14px;display:grid;grid-template-columns:390px 1fr;gap:14px}.card{background:white;border:1px solid #ddd;border-radius:14px;padding:15px}label{font-weight:bold;font-size:13px;display:block;margin-top:9px}select,input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:8px;font-size:15px}button{width:100%;padding:13px;margin-top:15px;background:#345d67;color:white;border:0;border-radius:8px;font-size:16px;font-weight:bold}.prefs{display:grid;grid-template-columns:1fr 120px;gap:6px;align-items:center}.prefs span{font-size:13px}.prefs select{padding:7px}.day{border:1px solid #ddd;border-radius:10px;padding:11px;margin:10px 0}.row{border-bottom:1px solid #eee;padding:5px 0;font-size:13px}.warn{background:#fff3d8;padding:9px;border-radius:8px;font-size:13px;margin:8px 0}.ok{background:#eaf5ee;padding:8px;border-radius:8px;font-size:13px}@media(max-width:760px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>Cyprus Romantic Trip Planner – Paphos</h1><div>v62 Hotel-Aware + Dinner Diversity</div></header><main>
<div class="card"><h2>Trip inputs</h2><label>Ξενοδοχείο</label><select id="hotel"></select><label>Ημερομηνία</label><input id="date" type="date">
<label>Διανυκτερεύσεις</label><input id="nights" type="number" value="3" min="1" max="7"><label>Pace</label><select id="pace"><option>relaxed</option><option selected>balanced</option><option>active</option></select>
<label>Καιρός</label><select id="weather"><option value="auto" selected>Auto live forecast</option><option>normal</option><option>rainy</option><option>heatwave</option><option>winter</option></select>
<label>Μέγιστη διαδρομή</label><select id="drive"><option value="15">15 min</option><option value="20" selected>20 min</option><option value="30">30 min</option><option value="45">45 min</option></select>
<label>Couple style</label><select id="style"><option value="mixed" selected>Mixed</option><option value="romantic_luxury">Romantic luxury</option><option value="authentic">Authentic Cyprus</option><option value="active">Active couple</option><option value="sea_lovers">Sea lovers</option><option value="food_wine">Food & wine</option></select>
<label>Budget</label><select id="budget"><option value="value">Value</option><option value="mid" selected>Mid-range</option><option value="premium">Premium</option></select>
<label>Special occasion</label><select id="occasion"><option value="none" selected>None</option><option value="honeymoon">Honeymoon</option><option value="anniversary">Anniversary</option><option value="proposal">Proposal</option></select>
<h3>Δραστηριότητες</h3><div class="prefs" id="prefs"></div><button onclick="go()">Generate Strict Itinerary</button>
<div class="warn">Με 20′ αποκλείεται συντηρητικά ολόκληρη η ζώνη 15–30′ μέχρι να συνδεθεί live routing.<br>
Ο καιρός αλλάζει πλέον τις προτεραιότητες: rainy → indoor, heatwave → θάλασσα νωρίς/αργά + indoor, winter → winter/indoor. Σε ζεστό 4ήμερο με Sea/Swimming = Yes απαιτείται πραγματικό sea/swim block, αν υπάρχει επιλέξιμη ανοικτή επιλογή.</div></div>
<div class="card" id="out"><p>Επίλεξε Yes / No / No preference και δημιούργησε πρόγραμμα.</p></div></main><script>
const P={sea:"Sea activities",swimming:"Swimming / sea bathing",beach:"Beaches",boat:"Boat / cruises",diving:"Diving / snorkelling",golf:"Golf",horse:"Horse riding",cycling:"Cycling",hiking:"Hiking / walking",viewpoints:"Viewpoints / photography",winery:"Winery / wine tasting",local_food:"Local gastronomy / products",crafts:"Χειροτεχνίες / βιωματικά εργαστήρια",cooking:"Cooking workshops",museums:"Museums / indoor culture",archaeology:"Archaeology / heritage",villages:"Traditional villages",religious:"Churches / monasteries",wellness:"Spa / wellness"};
const $=x=>document.getElementById(x);
async function init(){let h=await (await fetch('/api/v1/meta/hotels')).json();$('hotel').innerHTML=h.map(x=>`<option value="${x.hotel_id}">${x.name} — ${x.area||''}</option>`).join('');$('prefs').innerHTML=Object.entries(P).map(([k,v])=>`<span>${v}</span><select id="p_${k}"><option value="any">No preference</option><option value="yes">Yes</option><option value="no">No</option></select>`).join('');let d=new Date();d.setDate(d.getDate()+14);$('date').value=d.toISOString().slice(0,10)}
async function go(){let prefs={};Object.keys(P).forEach(k=>prefs[k]=$('p_'+k).value);let p={hotel_id:$('hotel').value,start_date:$('date').value,nights:+$('nights').value,pace:$('pace').value,weather_mode:$('weather').value,max_drive_min:+$('drive').value,couple_style:$('style').value,budget:$('budget').value,special_occasion:$('occasion').value,prefs};$('out').innerHTML='<p>Checking strict rules…</p>';let r=await fetch('/api/v1/trips/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});let d=await r.json();if(!r.ok){$('out').innerHTML='<div class=warn>'+JSON.stringify(d)+'</div>';return}$('out').innerHTML='<h2>Strict itinerary</h2><div class=ok>Status: '+d.status+' · Routing: '+d.provider_state.routing+' · Weather: '+d.provider_state.weather+'</div><div class="'+(d.hotel_operation.status==='VERIFIED_OPEN'?'ok':'warn')+'">Hotel date check: '+d.hotel_operation.status+' · '+d.hotel_operation.reason+'</div>'+(d.unmet_yes_preferences.length?'<div class=warn>Δεν βρέθηκε κατάλληλη ανοικτή επιλογή για: '+d.unmet_yes_preferences.join(', ')+'</div>':'')+d.days.map(x=>x.activity?`<div class=day><b>Ημέρα ${x.day} — ${x.date}</b><h3>${x.title}</h3><div style="font-size:12px">Season: ${x.season} · Weather planning: ${x.weather_mode_used} · Sunset: ${x.sunset_local} · ${x.weather.source==='SEASONAL_PLANNING_NOT_LIVE_FORECAST'?'Seasonal estimate — not live forecast':x.weather.source}</div><h4>${x.activity.title}</h4><div style="font-size:12px">Open check: ${x.activity.opening_check} · ${x.activity.travel_band}${x.activity.booking_required?' · Booking required':''}</div>${x.secondary_activity?`<div class=ok><b>2η κοντινή δραστηριότητα:</b> ${x.secondary_activity.title}</div>`:''}${x.lunch?`<div class=ok><b>Lunch:</b> ${x.lunch.title}</div>`:'<div class=warn>Δεν βρέθηκε επαληθευμένο συγκεκριμένο lunch venue.</div>'}${x.timeline.map(t=>`<div class=row>${t.time} · <b>${t.kind}</b> · ${t.title}</div>`).join('')}</div>`:`<div class=day><b>Ημέρα ${x.day} — ${x.date}</b><div class=warn>${x.operational_warning}</div></div>`).join('')+(d.warnings.length?'<div class=warn>'+d.warnings.join('<br>')+'</div>':'')}
init();</script></body></html>"""

@app.get("/",response_class=HTMLResponse)
def home():return HTML
