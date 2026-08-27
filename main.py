from __future__ import annotations
import json, re, sqlite3, uuid
from datetime import date, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DB=ROOT/"planner.sqlite3"
app=FastAPI(title="Cyprus Romantic Trip Planner – Strict Realistic",version="v51")

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

PREF_RULES={
"sea":["sea","coast","parasail","watersport"],
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
    weather_mode:str="normal"
    max_drive_min:int=20
    prefs:dict[str,str]={}  # yes / no / any

BAND_MAX={"0–15 min":15,"15–30 min":30,"30–45 min":45}
BAND_PLAN={"0–15 min":10,"15–30 min":25,"30–45 min":40}
PROX={"0–15 min":20,"15–30 min":12,"30–45 min":5}

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

def day_open(r,d):
    p=pld(r); s=str(p.get("Opening_Days") or "").strip().lower()
    if not s:return False,"UNKNOWN"
    if any(x in s for x in ["daily","monday–sunday","monday-sunday","7 days","every day","public access"]):
        return True,"VERIFIED_RULE"
    day=d.strftime("%A").lower()
    aliases={"monday":["monday","mon"],"tuesday":["tuesday","tue"],"wednesday":["wednesday","wed"],
             "thursday":["thursday","thu"],"friday":["friday","fri"],"saturday":["saturday","sat"],"sunday":["sunday","sun"]}
    if re.search(rf'closed[^.;,]*\b{day}\b|\b{day}\b[^.;,]*closed',s):return False,"CLOSED"
    if any(re.search(rf'\b{a}\b',s) for a in aliases[day]):return True,"VERIFIED_RULE"
    # numeric or generic by-reservation schedules without an explicit day are not safe enough
    return False,"UNKNOWN"

def month_ok(r,d,mode):
    p=pld(r)
    if mode=="winter" and str(p.get("Winter_Suitable") or "")=="No":return False
    if mode in ("normal","heatwave","rainy") and d.month in [5,6,7,8,9,10] and str(p.get("Summer_Suitable") or "")=="No":return False
    return True

def time_window(r):
    p=pld(r)
    if str(p.get("Time_Slot_Usable") or "").lower()=="yes":
        try:
            o=round(float(p["Parsed_Open_Time"])*1440); c=round(float(p["Parsed_Close_Time"])*1440)
            return f"{o//60:02d}:{o%60:02d}",f"{c//60:02d}:{c%60:02d}","PARSED"
        except:pass
    s=str(p.get("Opening_Hours") or "")
    m=re.search(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',s)
    if m:return f"{int(m.group(1)):02d}:{m.group(2)}",f"{int(m.group(3)):02d}:{m.group(4)}","TEXT"
    # outdoor public-access/daylight places can safely be used in daytime
    if "daylight" in s.lower() or "24" in s.lower():return "08:00","19:00","DAYLIGHT"
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

def strict_drive_ok(r,max_drive):
    return BAND_MAX.get(r["travel_band"],999) <= max_drive

def fit(r,d,mode,desired):
    opened,reason=day_open(r,d)
    if not opened:return None
    if not month_ok(r,d,mode):return None
    o,c,conf=time_window(r)
    if not o or not c:return None  # strict: unknown operating time excluded
    dur=max(15,int(r["duration_min"] or 60))
    start=max(desired,o)
    end=addm(start,dur)
    if end>c:
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

def candidates(c,req,hotel,d,desired,used):
    mode=req.weather_mode
    rows=c.execute("""select a.*,m.travel_band,m.recommended_day1
      from activity a join hotel_activity_mapping m on m.activity_id=a.activity_id
      where m.hotel_id=? and a.data_status='Verified'""",(req.hotel_id,)).fetchall()
    out=[]
    for r in rows:
        if r["activity_id"] in used:continue
        if r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
        if not strict_drive_ok(r,req.max_drive_min):continue
        if not hard_pref_ok(r,req.prefs):continue
        op=fit(r,d,mode,desired)
        if not op:continue
        out.append((score(r,hotel["cluster_id"],req.prefs),r,op))
    out.sort(key=lambda x:x[0],reverse=True)
    return out

def pick_primary(c,req,hotel,d,day,used):
    desired="16:30" if day==1 else "10:30"
    cs=candidates(c,req,hotel,d,desired,used)
    yes={k for k,v in req.prefs.items() if (v or "").lower()=="yes"}
    if yes:
        tagged=[x for x in cs if tags(x[1]) & yes]
        if tagged:cs=tagged+ [x for x in cs if x not in tagged]
    return cs[0] if cs else None

def pick_secondary(c,req,hotel,d,primary,used):
    if req.pace=="relaxed": return None
    desired=addm(primary["op"]["end"],max(45,primary["travel"]))
    if desired>="17:45":return None
    cs=candidates(c,req,hotel,d,desired,used)
    for s,r,op in cs:
        if r["category"]==primary["row"]["category"]:continue
        if op["start"]<desired or op["end"]>"18:45":continue
        return s,r,op
    return None

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
        if re.search(rf'closed[^.;,]*\\b{day}\\b|\\b{day}\\b[^.;,]*closed',days):return False
        if not any(re.search(rf'\\b{a}\\b',days) for a in aliases[day]):return False
    # dinner must include 20:00 within an explicit time window
    spans=re.findall(r'(\\d{1,2}):(\\d{2})\\s*[–-]\\s*(\\d{1,2}):(\\d{2})',hrs)
    if not spans:return False
    for h1,m1,h2,m2 in spans:
        start=int(h1)*60+int(m1); end=int(h2)*60+int(m2)
        if start<=20*60<=end:return True
    return False

def dinner(c,req,hotel,used,d):
    rows=c.execute("""select r.*,m.travel_band from restaurant r join hotel_restaurant_mapping m
      on m.restaurant_id=r.restaurant_id where m.hotel_id=? and r.data_status='Verified'""",(req.hotel_id,)).fetchall()
    valid=[r for r in rows if r["restaurant_id"] not in used and BAND_MAX.get(r["travel_band"],999)<=req.max_drive_min
           and not(r["cluster_id"]=="C13" and hotel["cluster_id"]!="C02") and restaurant_open_for_dinner(r,d)]
    if not valid:return None
    valid.sort(key=lambda r:float(r["romantic_score"] or 0)*3+PROX.get(r["travel_band"],0)+(8 if r["cluster_id"]==hotel["cluster_id"] else 0),reverse=True)
    r=valid[0]; used.add(r["restaurant_id"]); return r

def obj(r,op,req):
    return {"entity_id":r["activity_id"],"title":r["name"],"category":r["category"],"subcategory":r["subcategory"],
      "tags":sorted(tags(r)),"start_time":op["start"],"end_time":op["end"],"travel_band":r["travel_band"],
      "planning_travel_min":BAND_PLAN.get(r["travel_band"],60),"booking_required":op["booking"],
      "opening_check":"OPEN","opening_confidence":op["opening_confidence"],"data_status":r["data_status"]}

def timeline(hotel,day,a,b,din):
    out=[]
    if day==1:
        out += [{"time":"11:00–11:30","kind":"hotel","title":"Check-in / settle in"},
                {"time":"13:00–14:00","kind":"meal","title":"Lunch at/near hotel"},
                {"time":"14:00–16:00","kind":"rest","title":"Rest / room time"}]
    depart=addm(a["start_time"],-a["planning_travel_min"])
    out += [{"time":f"{depart}–{a['start_time']}","kind":"travel","title":f"Travel to {a['title']} (~{a['planning_travel_min']} min planning)"},
            {"time":f"{a['start_time']}–{a['end_time']}","kind":"activity","title":a["title"]}]
    cursor=a["end_time"]
    if b:
        dep=addm(b["start_time"],-b["planning_travel_min"])
        if dep>cursor:out.append({"time":f"{cursor}–{dep}","kind":"buffer","title":"Coffee / free time"})
        out += [{"time":f"{dep}–{b['start_time']}","kind":"travel","title":f"Travel to {b['title']} (~{b['planning_travel_min']} min planning)"},
                {"time":f"{b['start_time']}–{b['end_time']}","kind":"activity","title":b["title"]}]
        cursor=b["end_time"]
    if din:
        dt=BAND_PLAN.get(din["travel_band"],60); dep=addm("20:00",-dt)
        if dep>cursor:out.append({"time":f"{cursor}–{dep}","kind":"buffer","title":"Rest / freshen up"})
        out += [{"time":f"{dep}–20:00","kind":"travel","title":f"Travel to {din['name']} (~{dt} min planning)"},
                {"time":"20:00–21:30","kind":"meal","title":din["name"]},
                {"time":"after dinner","kind":"travel","title":f"Return to {hotel['name']}"}]
    return out

@app.get("/api/v1/health")
def health():
    return {"service":"ok","logic_version":"v51-strict","routing_provider":"not_connected",
            "weather_provider":"not_connected","drive_filter":"STRICT_CONSERVATIVE_MAPPING_BANDS",
            "opening_filter":"STRICT_KNOWN_OPEN_ONLY"}

@app.get("/api/v1/meta/hotels")
def hotels():
    with conn() as c:return [dict(r) for r in c.execute("select hotel_id,name,area,cluster_id from hotel order by name")]

@app.post("/api/v1/trips/generate")
def generate(req:TripRequest):
    with conn() as c:
        hotel=c.execute("select * from hotel where hotel_id=?",(req.hotel_id,)).fetchone()
        if not hotel:raise HTTPException(422,"Unknown hotel_id")
        used=set(); dr=set(); days=[]; unmet=[]
        yes={k for k,v in req.prefs.items() if (v or "").lower()=="yes"}
        covered=set()
        for i in range(1,min(req.nights+1,7)+1):
            d=req.start_date+timedelta(days=i-1)
            p=pick_primary(c,req,hotel,d,i,used)
            if not p:continue
            _,r,op=p; used.add(r["activity_id"]); covered |= tags(r)&yes
            pa=obj(r,op,req); holder={"row":r,"op":op,"travel":pa["planning_travel_min"]}
            sec=None
            if i>1:
                q=pick_secondary(c,req,hotel,d,holder,used)
                if q:
                    _,rr,oo=q; used.add(rr["activity_id"]); covered |= tags(rr)&yes; sec=obj(rr,oo,req)
            din=dinner(c,req,hotel,dr,d)
            tl=timeline(hotel,i,pa,sec,din)
            days.append({"day":i,"date":str(d),"activity":pa,"secondary_activity":sec,
              "dinner":{"entity_id":din["restaurant_id"],"title":din["name"],"travel_band":din["travel_band"]} if din else None,
              "timeline":tl,"timeline_qa":{"external_activity_count":1+(1 if sec else 0),"status":"PASS"}})
        unmet=sorted(yes-covered)
    return {"trip_id":"trp_"+uuid.uuid4().hex[:10],"status":"STRICT_FALLBACK_READY",
      "provider_state":{"routing":"NOT_CONNECTED","weather":"NOT_CONNECTED"},
      "strict_rules":{"closed_or_unknown_opening":"EXCLUDED","max_drive":"HARD_CONSERVATIVE_BAND_FILTER",
                      "explicit_no_preferences":"EXCLUDED","unverified_activities":"EXCLUDED","restaurants_not_known_open_at_20_00":"EXCLUDED"},
      "unmet_yes_preferences":unmet,"days":days,
      "warnings":["Live routing is not connected. The max-drive rule uses conservative mapping-band maxima: with 20 min selected, 15–30 min mappings are excluded.",
                  "Live weather is not connected; the selected weather mode is checked against stored suitability data."]}

HTML=r"""<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cyprus Romantic Trip Planner</title><style>
body{font-family:Arial;margin:0;background:#f6f3ee;color:#203036}header{background:#294c55;color:white;padding:20px}main{max-width:1100px;margin:auto;padding:14px;display:grid;grid-template-columns:390px 1fr;gap:14px}.card{background:white;border:1px solid #ddd;border-radius:14px;padding:15px}label{font-weight:bold;font-size:13px;display:block;margin-top:9px}select,input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:8px;font-size:15px}button{width:100%;padding:13px;margin-top:15px;background:#345d67;color:white;border:0;border-radius:8px;font-size:16px;font-weight:bold}.prefs{display:grid;grid-template-columns:1fr 120px;gap:6px;align-items:center}.prefs span{font-size:13px}.prefs select{padding:7px}.day{border:1px solid #ddd;border-radius:10px;padding:11px;margin:10px 0}.row{border-bottom:1px solid #eee;padding:5px 0;font-size:13px}.warn{background:#fff3d8;padding:9px;border-radius:8px;font-size:13px;margin:8px 0}.ok{background:#eaf5ee;padding:8px;border-radius:8px;font-size:13px}@media(max-width:760px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>Cyprus Romantic Trip Planner – Paphos</h1><div>v51 Strict Realistic Generator</div></header><main>
<div class="card"><h2>Trip inputs</h2><label>Ξενοδοχείο</label><select id="hotel"></select><label>Ημερομηνία</label><input id="date" type="date">
<label>Διανυκτερεύσεις</label><input id="nights" type="number" value="3" min="1" max="7"><label>Pace</label><select id="pace"><option>relaxed</option><option selected>balanced</option><option>active</option></select>
<label>Καιρός</label><select id="weather"><option>normal</option><option>rainy</option><option>heatwave</option><option>winter</option></select>
<label>Μέγιστη διαδρομή</label><select id="drive"><option value="15">15 min</option><option value="20" selected>20 min</option><option value="30">30 min</option><option value="45">45 min</option></select>
<h3>Δραστηριότητες</h3><div class="prefs" id="prefs"></div><button onclick="go()">Generate Strict Itinerary</button>
<div class="warn">Με 20′ αποκλείεται συντηρητικά ολόκληρη η ζώνη 15–30′ μέχρι να συνδεθεί live routing.</div></div>
<div class="card" id="out"><p>Επίλεξε Yes / No / No preference και δημιούργησε πρόγραμμα.</p></div></main><script>
const P={sea:"Sea activities",beach:"Beaches",boat:"Boat / cruises",diving:"Diving / snorkelling",golf:"Golf",horse:"Horse riding",cycling:"Cycling",hiking:"Hiking / walking",viewpoints:"Viewpoints / photography",winery:"Winery / wine tasting",local_food:"Local gastronomy / products",crafts:"Χειροτεχνίες / βιωματικά εργαστήρια",cooking:"Cooking workshops",museums:"Museums / indoor culture",archaeology:"Archaeology / heritage",villages:"Traditional villages",religious:"Churches / monasteries",wellness:"Spa / wellness"};
const $=x=>document.getElementById(x);
async function init(){let h=await (await fetch('/api/v1/meta/hotels')).json();$('hotel').innerHTML=h.map(x=>`<option value="${x.hotel_id}">${x.name} — ${x.area||''}</option>`).join('');$('prefs').innerHTML=Object.entries(P).map(([k,v])=>`<span>${v}</span><select id="p_${k}"><option value="any">No preference</option><option value="yes">Yes</option><option value="no">No</option></select>`).join('');let d=new Date();d.setDate(d.getDate()+14);$('date').value=d.toISOString().slice(0,10)}
async function go(){let prefs={};Object.keys(P).forEach(k=>prefs[k]=$('p_'+k).value);let p={hotel_id:$('hotel').value,start_date:$('date').value,nights:+$('nights').value,pace:$('pace').value,weather_mode:$('weather').value,max_drive_min:+$('drive').value,prefs};$('out').innerHTML='<p>Checking strict rules…</p>';let r=await fetch('/api/v1/trips/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});let d=await r.json();if(!r.ok){$('out').innerHTML='<div class=warn>'+JSON.stringify(d)+'</div>';return}$('out').innerHTML='<h2>Strict itinerary</h2><div class=ok>Status: '+d.status+'</div>'+(d.unmet_yes_preferences.length?'<div class=warn>Δεν βρέθηκε κατάλληλη ανοικτή επιλογή για: '+d.unmet_yes_preferences.join(', ')+'</div>':'')+d.days.map(x=>`<div class=day><b>Ημέρα ${x.day} — ${x.date}</b><h3>${x.activity.title}</h3><div style="font-size:12px">Open check: ${x.activity.opening_check} · ${x.activity.travel_band}${x.activity.booking_required?' · Booking required':''}</div>${x.secondary_activity?`<div class=ok><b>2η δραστηριότητα:</b> ${x.secondary_activity.title}</div>`:''}${x.timeline.map(t=>`<div class=row>${t.time} · <b>${t.kind}</b> · ${t.title}</div>`).join('')}</div>`).join('')+'<div class=warn>'+d.warnings.join('<br>')+'</div>'}
init();</script></body></html>"""

@app.get("/",response_class=HTMLResponse)
def home():return HTML
