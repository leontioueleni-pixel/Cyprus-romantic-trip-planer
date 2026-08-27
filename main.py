from __future__ import annotations
import json, os, re, sqlite3, uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DB=ROOT/"planner.sqlite3"
app=FastAPI(title="Cyprus Romantic Trip Planner – Full Logic Cloud",version="2.0")

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

class TripRequest(BaseModel):
    hotel_id:str
    start_date:date
    nights:int=Field(3,ge=1,le=7)
    arrival_time_local:str="11:00"
    pace:str="relaxed"
    weather_mode:str="normal"
    max_drive_min:int=20
    interest_sea:bool=True
    interest_wine_food:bool=True
    interest_nature:bool=True
    interest_culture:bool=True
    interest_wellness:bool=False
    interest_active:bool=False
    authentic_priority:bool=True

BAND_MIN={"0–15 min":10,"15–30 min":25,"30–45 min":40}
PROX={"0–15 min":20,"15–30 min":15,"30–45 min":8}
THEMES=[
("Arrival & Romantic Light",["spa","wellness","sunset","view","beach","village"]),
("Sea & Scenic Nature",["sea","beach","coast","parasail","cape","view","lagoon"]),
("Authentic Cyprus & Food/Wine",["wine","winery","traditional","food","loukoumi","pottery","weaving"]),
("Culture & Heritage",["museum","heritage","castle","archae","church","basilica"]),
("Nature & Wellness",["nature","forest","trail","spa","wellness","spring","view"]),
("Active & Adventure",["horse","riding","cycling","golf","hiking","adventure","dive"]),
("Romantic Finale",["sunset","wine","sea","view","village","beach"])
]

def payload(row):
    try:return json.loads(row["payload"] or "{}")
    except Exception:return {}

def add_minutes(hhmm,mins):
    h,m=map(int,hhmm.split(":"))
    total=h*60+m+mins
    return f"{total//60:02d}:{total%60:02d}"

def minutes_between(a,b):
    ah,am=map(int,a.split(":")); bh,bm=map(int,b.split(":"))
    return max(0,(bh*60+bm)-(ah*60+am))

def frac_time(v):
    if v in ("",None):return None
    try:
        mins=round(float(v)*24*60)
        return f"{(mins//60)%24:02d}:{mins%60:02d}"
    except:return None

def opening_window(row):
    p=payload(row)
    if str(p.get("Time_Slot_Usable","")).lower()=="yes":
        o=frac_time(p.get("Parsed_Open_Time")); c=frac_time(p.get("Parsed_Close_Time"))
        if o and c:return o,c,"PARSED"
    text=str(p.get("Opening_Hours") or "")
    m=re.search(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',text)
    if m:return f"{int(m.group(1)):02d}:{m.group(2)}",f"{int(m.group(3)):02d}:{m.group(4)}","TEXT"
    return None,None,"UNKNOWN"

def day_status(row,target):
    p=payload(row); text=str(p.get("Opening_Days") or "").lower()
    if not text:return "RECHECK"
    if any(k in text for k in ["daily","monday–sunday","monday-sunday","public access"]):return "OPEN"
    day=target.strftime("%A").lower()
    aliases={"monday":["mon","monday"],"tuesday":["tue","tues","tuesday"],"wednesday":["wed","wednesday"],
             "thursday":["thu","thur","thurs","thursday"],"friday":["fri","friday"],
             "saturday":["sat","saturday"],"sunday":["sun","sunday"]}
    if re.search(rf'closed\s+{day}|{day}\s+closed',text):return "CLOSED"
    if any(re.search(rf'\b{a}\b',text) for a in aliases[day]):return "OPEN"
    return "RECHECK"

def weather_ok(row,mode,start):
    p=payload(row)
    rain=str(p.get("Rain_Suitable") or "")
    heat=str(p.get("Heatwave_Suitable") or "")
    winter=str(p.get("Winter_Suitable") or "")
    io=str(p.get("Indoor_Outdoor_Both") or "").lower()
    dur=int(row["duration_min"] or 60)
    if mode=="rainy" and rain=="No":return False,"Not rain-suitable"
    if mode=="winter" and winter=="No":return False,"Not winter-suitable"
    if mode=="heatwave":
        if heat=="No":return False,"Not heatwave-suitable"
        if "outdoor" in io and dur>=60 and "12:00"<=start<"16:30":return False,"Long outdoor activity blocked 12:00–16:30"
    return True,"OK"

def preferred_start(row,day,mode):
    p=payload(row); best=str(p.get("Best_Time_of_Day") or "").lower()
    if day==1:return "17:00" if mode=="heatwave" else "16:30"
    if "sunset" in best or "late afternoon" in best:return "17:30"
    if "afternoon" in best:return "16:00"
    return "10:30"

def operational_fit(row,target,day,mode,proposed=None):
    ds=day_status(row,target)
    if ds=="CLOSED":return None
    start=proposed or preferred_start(row,day,mode)
    dur=max(15,int(row["duration_min"] or 60))
    o,c,confidence=opening_window(row)
    warnings=[]
    status="PASS"
    if ds=="RECHECK":
        warnings.append("Opening day requires confirmation"); status="RECHECK"
    if o and start<o:start=o
    end=add_minutes(start,dur)
    if c and end>c:
        # fit backwards if possible
        ch,cm=map(int,c.split(":")); latest=ch*60+cm-dur
        oh,om=map(int,o.split(":")) if o else (0,0)
        if latest < oh*60+om:return None
        start=f"{latest//60:02d}:{latest%60:02d}"; end=add_minutes(start,dur)
    if not o or not c:
        warnings.append("Exact operating time requires confirmation"); status="RECHECK"
    ok,reason=weather_ok(row,mode,start)
    if not ok:return None
    p=payload(row)
    booking=str(p.get("Booking_Required") or "").lower()=="yes"
    if booking:warnings.append("Advance booking required")
    return {"start":start,"end":end,"status":status,"warnings":warnings,"booking_required":booking}

def activity_score(a,m,req,theme_words,hotel_cluster,day,op):
    name=(a["name"]+" "+(a["category"] or "")+" "+(a["subcategory"] or "")).lower()
    s=float(a["romantic_score"] or 0)*3 + float(a["authentic_score"] or 0)*(1.2 if req.authentic_priority else .25)
    s += PROX.get(m["travel_band"],0)
    if a["cluster_id"]==hotel_cluster:s+=8
    s += min(36,sum(12 for k in theme_words if k in name))
    if req.interest_sea and any(k in name for k in ["sea","beach","coast","parasail","dive","lagoon"]):s+=12
    if req.interest_wine_food and any(k in name for k in ["wine","winery","food","tasting","traditional","loukoumi"]):s+=12
    if req.interest_nature and any(k in name for k in ["nature","forest","view","trail","cape","waterfall"]):s+=12
    if req.interest_culture and any(k in name for k in ["culture","museum","heritage","castle","archae","church"]):s+=12
    if req.interest_wellness and any(k in name for k in ["spa","wellness"]):s+=12
    if req.interest_active and any(k in name for k in ["horse","riding","cycling","golf","hiking","adventure","parasail","dive"]):s+=20
    if req.pace=="active" and any(k in name for k in ["horse","riding","cycling","hiking","parasail","dive"]):s+=10
    if day==1:s += {"Yes":8,"Conditional":0,"No":-30}.get(m["recommended_day1"],-30)
    if op["status"]=="PASS":s+=8
    else:s-=5
    ptxt=(a["payload"] or "").lower()
    if req.weather_mode=="rainy":
        if '"rain_suitable": "yes"' in ptxt:s+=18
        if '"indoor_outdoor_both": "indoor"' in ptxt:s+=15
    if req.weather_mode=="heatwave":
        if '"heatwave_suitable": "yes"' in ptxt:s+=14
        if '"indoor_outdoor_both": "indoor"' in ptxt:s+=12
    return s

def pick_primary(c,req,hotel,target,day,used):
    theme,words=THEMES[day-1]
    rows=c.execute("""select a.*,m.travel_band,m.recommended_day1
      from activity a join hotel_activity_mapping m on m.activity_id=a.activity_id
      where m.hotel_id=? and a.data_status in ('Verified','Needs Recheck')""",(req.hotel_id,)).fetchall()
    ranked=[]
    mode="normal" if req.weather_mode=="auto" else req.weather_mode
    for a in rows:
        if a["activity_id"] in used:continue
        if a["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
        op=operational_fit(a,target,day,mode)
        if not op:continue
        s=activity_score(a,a,req,words,hotel["cluster_id"],day,op)
        ranked.append((s,a,op))
    ranked.sort(key=lambda x:x[0],reverse=True)
    return ranked[0] if ranked else None

def pick_secondary(c,req,hotel,target,primary,used):
    if primary is None:return None
    rows=c.execute("""select a.*,m.travel_band,m.recommended_day1
      from activity a join hotel_activity_mapping m on m.activity_id=a.activity_id
      where m.hotel_id=? and a.data_status in ('Verified','Needs Recheck')""",(req.hotel_id,)).fetchall()
    ranked=[]; mode="normal" if req.weather_mode=="auto" else req.weather_mode
    earliest=add_minutes(primary["op"]["end"],max(primary["travel"],10))
    proposed=max("16:00",earliest)
    if proposed>="18:00":return None
    for a in rows:
        if a["activity_id"] in used or a["activity_id"]==primary["row"]["activity_id"]:continue
        if a["cluster_id"]=="C13" and hotel["cluster_id"]!="C02":continue
        if a["category"]==primary["row"]["category"]:continue
        if int(a["duration_min"] or 60)>90:continue
        if a["travel_band"]=="30–45 min" and req.max_drive_min<=20:continue
        op=operational_fit(a,target,2,mode,proposed)
        if not op or op["start"]<earliest or op["end"]>="19:00":continue
        s=float(a["romantic_score"] or 0)*2+float(a["authentic_score"] or 0)+PROX.get(a["travel_band"],0)
        if a["cluster_id"]==primary["row"]["cluster_id"]:s+=12
        if a["cluster_id"]==hotel["cluster_id"]:s+=8
        if op["status"]=="PASS":s+=6
        ranked.append((s,a,op))
    ranked.sort(key=lambda x:x[0],reverse=True)
    return ranked[0] if ranked else None

def pick_dinner(c,hotel_id,hotel_cluster,used):
    rows=c.execute("""select r.*,m.travel_band from restaurant r
      join hotel_restaurant_mapping m on m.restaurant_id=r.restaurant_id
      where m.hotel_id=? and r.data_status='Verified'""",(hotel_id,)).fetchall()
    ranked=[]
    for r in rows:
        if r["restaurant_id"] in used:continue
        if r["cluster_id"]=="C13" and hotel_cluster!="C02":continue
        s=float(r["romantic_score"] or 0)*3+PROX.get(r["travel_band"],0)
        if r["cluster_id"]==hotel_cluster:s+=10
        ranked.append((s,r))
    if not ranked:return None
    ranked.sort(key=lambda x:x[0],reverse=True)
    r=ranked[0][1]; used.add(r["restaurant_id"])
    return r

def make_activity(row,op,req):
    warnings=list(op["warnings"])
    travel=BAND_MIN.get(row["travel_band"],60)
    if travel>req.max_drive_min:warnings.append("Planning travel may exceed preferred max; live routing required.")
    if row["data_status"]!="Verified":warnings.append("Operator confirmation required.")
    if row["cluster_id"]=="C13":warnings.append("Pissouri is in Limassol; exact live route validation required.")
    return {"entity_id":row["activity_id"],"title":row["name"],"category":row["category"],"cluster_id":row["cluster_id"],
            "start_time":op["start"],"end_time":op["end"],"travel_band":row["travel_band"],
            "planning_travel_min":travel,"data_status":row["data_status"],"booking_required":op["booking_required"],
            "operational_status":op["status"],"warnings":warnings}

def build_timeline(hotel,day,primary,secondary,dinner):
    items=[]
    if day==1:
        items += [
          {"time":"11:00–11:30","kind":"hotel","title":"Check-in / settle in"},
          {"time":"13:00–14:00","kind":"meal","title":"Lunch at/near hotel"},
          {"time":"14:00–16:00","kind":"rest","title":"Rest / room time"},
        ]
    travel=primary["planning_travel_min"]
    depart=add_minutes(primary["start_time"],-travel)
    items.append({"time":f"{depart}–{primary['start_time']}","kind":"travel","title":f"Travel to {primary['title']} (~{travel} min planning)"})
    items.append({"time":f"{primary['start_time']}–{primary['end_time']}","kind":"activity","title":primary["title"]})
    cursor=primary["end_time"]
    if secondary:
        t=max(primary["planning_travel_min"],secondary["planning_travel_min"])
        dep=add_minutes(secondary["start_time"],-t)
        if minutes_between(cursor,dep)>=45:
            items.append({"time":f"{cursor}–{dep}","kind":"buffer","title":"Lunch / coffee / free time"})
        items.append({"time":f"{dep}–{secondary['start_time']}","kind":"travel","title":f"Travel to {secondary['title']} (~{t} min planning)"})
        items.append({"time":f"{secondary['start_time']}–{secondary['end_time']}","kind":"activity","title":secondary["title"]})
        cursor=secondary["end_time"]
    if dinner:
        dtravel=BAND_MIN.get(dinner["travel_band"],60)
        dstart="20:00"; dep=add_minutes(dstart,-dtravel)
        if minutes_between(cursor,dep)>=60:
            items.append({"time":f"{cursor}–{dep}","kind":"buffer","title":"Free time / coffee / rest"})
        items.append({"time":f"{dep}–{dstart}","kind":"travel","title":f"Travel to {dinner['name']} (~{dtravel} min planning)"})
        items.append({"time":"20:00–21:30","kind":"meal","title":dinner["name"]})
        items.append({"time":"after dinner","kind":"travel","title":f"Return to {hotel['name']}"})
    return items

@app.get("/api/v1/health")
def health():
    return {"service":"ok","data_backend":"sqlite-bundled","routing_provider":"not_connected",
            "weather_provider":"not_connected","content_version":"content_2026_08_27","logic_version":"v50"}

@app.get("/api/v1/meta/hotels")
def hotels():
    with conn() as c:
        return [dict(r) for r in c.execute("select hotel_id,name,area,cluster_id from hotel order by name")]

@app.post("/api/v1/trips/generate")
def generate(req:TripRequest):
    with conn() as c:
        hotel=c.execute("select * from hotel where hotel_id=?",(req.hotel_id,)).fetchone()
        if not hotel:raise HTTPException(422,"Unknown hotel_id")
        used=set(); dinner_used=set(); days=[]
        total=min(req.nights+1,7)
        for i in range(1,total+1):
            target=req.start_date+timedelta(days=i-1)
            p=pick_primary(c,req,hotel,target,i,used)
            if not p:continue
            _,prow,pop=p; ptravel=BAND_MIN.get(prow["travel_band"],60)
            used.add(prow["activity_id"])
            primary_obj={"row":prow,"op":pop,"travel":ptravel}
            primary=make_activity(prow,pop,req)
            secondary=None
            if i>1:
                s=pick_secondary(c,req,hotel,target,primary_obj,used)
                if s:
                    _,srow,sop=s; used.add(srow["activity_id"]); secondary=make_activity(srow,sop,req)
            dinner=pick_dinner(c,req.hotel_id,hotel["cluster_id"],dinner_used)
            timeline=build_timeline(hotel,i,primary,secondary,dinner)
            travel_total=sum(int(re.search(r'~(\d+) min',x["title"]).group(1)) for x in timeline if x["kind"]=="travel" and "~" in x["title"])
            external=1+(1 if secondary else 0)+(1 if dinner else 0)
            load="LIGHT" if external<=1 else ("BALANCED" if external<=3 and travel_total<=120 else "BUSY")
            warnings=primary["warnings"]+(secondary["warnings"] if secondary else [])
            status="RECHECK" if warnings or any(x["operational_status"]=="RECHECK" for x in [primary]+([secondary] if secondary else [])) else "PASS"
            days.append({"day":i,"date":str(target),"theme":THEMES[i-1][0],"activity":primary,"secondary_activity":secondary,
                         "dinner":{"entity_id":dinner["restaurant_id"],"title":dinner["name"],"travel_band":dinner["travel_band"]} if dinner else None,
                         "timeline":timeline,"timeline_qa":{"overlap_count":0,"total_travel_minutes":travel_total,
                         "external_stop_count":external,"load":load,"status":status}})
    return {"trip_id":"trp_"+uuid.uuid4().hex[:10],"status":"FALLBACK_READY",
            "provider_state":{"routing":"NOT_CONNECTED","weather":"NOT_CONNECTED"},
            "days":days,"warnings":["Routing/weather providers are not connected. Travel times are planning estimates."]}

HTML=r"""<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cyprus Romantic Trip Planner</title><style>
body{font-family:Arial,sans-serif;margin:0;background:#f6f3ee;color:#1f2b2f}header{background:#294c55;color:white;padding:22px 16px}main{max-width:1050px;margin:auto;padding:14px;display:grid;grid-template-columns:370px 1fr;gap:14px}.card{background:white;border-radius:14px;padding:15px;border:1px solid #ddd}label{font-size:13px;font-weight:bold;display:block;margin-top:10px}select,input{width:100%;box-sizing:border-box;padding:11px;font-size:16px;border:1px solid #ccc;border-radius:8px}button{width:100%;margin-top:16px;padding:13px;background:#345d67;color:white;border:0;border-radius:9px;font-size:16px;font-weight:bold}.checks{display:grid;grid-template-columns:1fr 1fr}.checks label{font-weight:normal}.checks input{width:auto}.day{border:1px solid #ddd;border-radius:10px;padding:12px;margin:10px 0}.warn{background:#fff3d8;padding:9px;border-radius:8px;font-size:13px}.row{padding:5px 0;font-size:13px;border-bottom:1px solid #eee}.secondary{background:#eef5f6;padding:8px;border-radius:8px;margin:8px 0}@media(max-width:760px){main{grid-template-columns:1fr}.checks{grid-template-columns:1fr}header h1{font-size:22px}}
</style></head><body><header><h1>Cyprus Romantic Trip Planner – Paphos</h1><div>Cloud MVP · Full Logic v50</div></header><main>
<div class="card"><h2>Inputs</h2><label>Ξενοδοχείο</label><select id="hotel"></select><label>Ημερομηνία</label><input id="date" type="date">
<label>Διανυκτερεύσεις</label><input id="nights" type="number" value="3" min="1" max="7"><label>Pace</label><select id="pace"><option>relaxed</option><option>balanced</option><option>active</option></select>
<label>Καιρός</label><select id="weather"><option>normal</option><option>rainy</option><option>heatwave</option><option>winter</option></select>
<label>Μέγιστη διαδρομή</label><select id="drive"><option value="15">15 min</option><option value="20" selected>20 min</option><option value="30">30 min</option><option value="45">45 min</option></select>
<div class="checks"><label><input type="checkbox" id="sea" checked> Θάλασσα</label><label><input type="checkbox" id="wine" checked> Κρασί/φαγητό</label><label><input type="checkbox" id="nature" checked> Φύση</label><label><input type="checkbox" id="culture" checked> Πολιτισμός</label><label><input type="checkbox" id="well"> Wellness</label><label><input type="checkbox" id="active"> Active</label></div>
<button onclick="go()">Generate Itinerary</button><p style="font-size:12px;color:#666">Routing & weather: NOT_CONNECTED. Οι χρόνοι είναι planning estimates.</p></div>
<div id="out" class="card"><p>Συμπλήρωσε τα inputs και πάτησε Generate Itinerary.</p></div></main><script>
const $=x=>document.getElementById(x);
async function init(){let h=await (await fetch('/api/v1/meta/hotels')).json();$('hotel').innerHTML=h.map(x=>`<option value="${x.hotel_id}">${x.name} — ${x.area||''}</option>`).join('');let d=new Date();d.setDate(d.getDate()+14);$('date').value=d.toISOString().slice(0,10)}
async function go(){let p={hotel_id:$('hotel').value,start_date:$('date').value,nights:+$('nights').value,pace:$('pace').value,weather_mode:$('weather').value,max_drive_min:+$('drive').value,interest_sea:$('sea').checked,interest_wine_food:$('wine').checked,interest_nature:$('nature').checked,interest_culture:$('culture').checked,interest_wellness:$('well').checked,interest_active:$('active').checked,authentic_priority:true};$('out').innerHTML='<p>Generating…</p>';let r=await fetch('/api/v1/trips/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});let d=await r.json();if(!r.ok){$('out').innerHTML='<div class="warn">'+JSON.stringify(d)+'</div>';return}$('out').innerHTML='<h2>Itinerary</h2><div class="warn">Status: '+d.status+' · Routing: '+d.provider_state.routing+' · Weather: '+d.provider_state.weather+'</div>'+d.days.map(x=>`<div class="day"><b>Ημέρα ${x.day} — ${x.date}</b><div>${x.theme}</div><h3>${x.activity.title}</h3>${x.secondary_activity?`<div class="secondary"><b>2η δραστηριότητα:</b> ${x.secondary_activity.title}</div>`:''}${x.timeline.map(t=>`<div class="row">${t.time} · <b>${t.kind}</b> · ${t.title}</div>`).join('')}<div style="font-size:12px;color:#666;margin-top:7px">Stops: ${x.timeline_qa.external_stop_count} · Travel: ${x.timeline_qa.total_travel_minutes}′ · Load: ${x.timeline_qa.load}</div>${x.activity.warnings.length?'<div class="warn">'+x.activity.warnings.join('<br>')+'</div>':''}</div>`).join('')}
init();</script></body></html>"""

@app.get("/",response_class=HTMLResponse)
def home():return HTML
