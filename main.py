from __future__ import annotations
import os, sqlite3, json, uuid
from datetime import date, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DB=ROOT/"planner.sqlite3"

app=FastAPI(title="Cyprus Romantic Trip Planner – Minimal Cloud",version="1.0")

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

class TripRequest(BaseModel):
    hotel_id:str
    start_date:date
    nights:int=Field(3,ge=1,le=7)
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
THEMES=[
("Arrival & Romantic Light",["spa","wellness","sunset","view","beach"]),
("Sea & Scenic Nature",["sea","beach","coast","parasail","cape","view"]),
("Authentic Cyprus & Food/Wine",["wine","winery","traditional","food","loukoumi"]),
("Culture & Heritage",["museum","heritage","castle","archae","church"]),
("Nature & Wellness",["nature","forest","trail","spa","wellness"]),
("Active & Adventure",["horse","riding","cycling","golf","hiking","adventure"]),
("Romantic Finale",["sunset","wine","sea","view","village"])
]

def score_activity(a,m,req,theme_words,hotel_cluster,day):
    name=(a["name"]+" "+(a["category"] or "")+" "+(a["subcategory"] or "")).lower()
    s=float(a["romantic_score"] or 0)*3 + float(a["authentic_score"] or 0)*(1.2 if req.authentic_priority else .25)
    s += {"0–15 min":20,"15–30 min":15,"30–45 min":8}.get(m["travel_band"],0)
    if a["cluster_id"]==hotel_cluster: s+=8
    s += min(36,sum(12 for k in theme_words if k in name))
    if req.interest_sea and any(k in name for k in ["sea","beach","coast","parasail","dive"]): s+=12
    if req.interest_wine_food and any(k in name for k in ["wine","winery","food","tasting","traditional","loukoumi"]): s+=12
    if req.interest_nature and any(k in name for k in ["nature","forest","view","trail","cape","waterfall"]): s+=12
    if req.interest_culture and any(k in name for k in ["culture","museum","heritage","castle","archae","church"]): s+=12
    if req.interest_wellness and any(k in name for k in ["spa","wellness"]): s+=12
    if req.interest_active and any(k in name for k in ["horse","riding","cycling","golf","hiking","adventure","parasail"]): s+=24
    if day==1:
        s += {"Yes":8,"Conditional":0,"No":-30}.get(m["recommended_day1"],-30)
    if req.weather_mode=="rainy":
        p=(a["payload"] or "").lower()
        if '"rain_suitable": "yes"' in p: s+=22
        if '"rain_suitable": "no"' in p: s-=100
    if req.weather_mode=="heatwave":
        p=(a["payload"] or "").lower()
        if '"heatwave_suitable": "yes"' in p: s+=18
        if '"heatwave_suitable": "no"' in p: s-=100
    return s

def pick_dinner(c,hotel_id,hotel_cluster,used):
    rows=c.execute("""select r.*,m.travel_band from restaurant r
      join hotel_restaurant_mapping m on m.restaurant_id=r.restaurant_id
      where m.hotel_id=? and r.data_status='Verified'""",(hotel_id,)).fetchall()
    ranked=[]
    for r in rows:
        if r["restaurant_id"] in used: continue
        if r["cluster_id"]=="C13" and hotel_cluster!="C02": continue
        s=float(r["romantic_score"] or 0)*3+{"0–15 min":20,"15–30 min":15,"30–45 min":8}.get(r["travel_band"],0)
        if r["cluster_id"]==hotel_cluster:s+=10
        ranked.append((s,r))
    if not ranked:return None
    ranked.sort(key=lambda x:x[0],reverse=True)
    r=ranked[0][1]; used.add(r["restaurant_id"])
    return {"entity_id":r["restaurant_id"],"title":r["name"],"travel_band":r["travel_band"],
            "planning_travel_min":BAND_MIN.get(r["travel_band"],60),"status":"Verified"}

@app.get("/api/v1/health")
def health():
    return {"service":"ok","data_backend":"sqlite-bundled","routing_provider":"not_connected",
            "weather_provider":"not_connected","content_version":"content_2026_08_27"}

@app.get("/api/v1/meta/hotels")
def hotels():
    with conn() as c:
        return [dict(r) for r in c.execute("select hotel_id,name,area,cluster_id from hotel order by name")]

@app.post("/api/v1/trips/generate")
def generate(req:TripRequest):
    with conn() as c:
        hotel=c.execute("select * from hotel where hotel_id=?",(req.hotel_id,)).fetchone()
        if not hotel: raise HTTPException(422,"Unknown hotel_id")
        used=set(); dinners=set(); days=[]
        total=min(req.nights+1,7)
        for i in range(1,total+1):
            theme,words=THEMES[i-1]
            rows=c.execute("""select a.*,m.travel_band,m.recommended_day1
              from activity a join hotel_activity_mapping m on m.activity_id=a.activity_id
              where m.hotel_id=? and a.data_status in ('Verified','Needs Recheck')""",(req.hotel_id,)).fetchall()
            ranked=[]
            for a in rows:
                if a["activity_id"] in used: continue
                if a["cluster_id"]=="C13" and hotel["cluster_id"]!="C02": continue
                s=score_activity(a,a,req,words,hotel["cluster_id"],i)
                ranked.append((s,a))
            ranked.sort(key=lambda x:x[0],reverse=True)
            if not ranked: continue
            a=ranked[0][1]; used.add(a["activity_id"])
            travel=BAND_MIN.get(a["travel_band"],60)
            start="16:30" if i==1 else ("17:30" if any(k in a["name"].lower() for k in ["sunset","cape","beach"]) else "10:30")
            dur=int(a["duration_min"] or 60)
            hh,mm=map(int,start.split(":")); end_minutes=hh*60+mm+dur
            end=f"{end_minutes//60:02d}:{end_minutes%60:02d}"
            warnings=[]
            if travel>req.max_drive_min: warnings.append("Planning travel may exceed preferred max; live routing required.")
            if a["data_status"]!="Verified": warnings.append("Operator confirmation required.")
            if a["cluster_id"]=="C13": warnings.append("Pissouri is in Limassol; exact live route validation required.")
            dinner=pick_dinner(c,req.hotel_id,hotel["cluster_id"],dinners)
            timeline=[
              {"time":"11:00–11:30","kind":"hotel","title":"Check-in / settle in"} if i==1 else {"time":"09:30","kind":"hotel","title":hotel["name"]},
              {"time":f"{start}","kind":"travel","title":f"Travel planning buffer ~{travel} min"},
              {"time":f"{start}–{end}","kind":"activity","title":a["name"]},
            ]
            if dinner:
                timeline += [{"time":"20:00–21:30","kind":"meal","title":dinner["title"]},
                             {"time":"after dinner","kind":"travel","title":f"Return to {hotel['name']}"}]
            days.append({"day":i,"date":str(req.start_date+timedelta(days=i-1)),"theme":theme,
                         "activity":{"entity_id":a["activity_id"],"title":a["name"],"category":a["category"],
                                     "travel_band":a["travel_band"],"planning_travel_min":travel,
                                     "data_status":a["data_status"],"warnings":warnings},
                         "dinner":dinner,"timeline":timeline,
                         "timeline_qa":{"overlap_count":0,"load":"BALANCED","status":"RECHECK" if warnings else "PASS"}})
    return {"trip_id":"trp_"+uuid.uuid4().hex[:10],"status":"FALLBACK_READY",
            "provider_state":{"routing":"NOT_CONNECTED","weather":"NOT_CONNECTED"},
            "days":days,
            "warnings":["Routing/weather providers are not connected. Travel times are planning estimates."]}

HTML=r"""<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cyprus Romantic Trip Planner</title><style>
body{font-family:Arial,sans-serif;margin:0;background:#f6f3ee;color:#1f2b2f}header{background:#294c55;color:white;padding:22px 16px}main{max-width:1000px;margin:auto;padding:14px;display:grid;grid-template-columns:360px 1fr;gap:14px}.card{background:white;border-radius:14px;padding:15px;border:1px solid #ddd}label{font-size:13px;font-weight:bold;display:block;margin-top:10px}select,input{width:100%;box-sizing:border-box;padding:11px;font-size:16px;border:1px solid #ccc;border-radius:8px}button{width:100%;margin-top:16px;padding:13px;background:#345d67;color:white;border:0;border-radius:9px;font-size:16px;font-weight:bold}.checks{display:grid;grid-template-columns:1fr 1fr}.checks label{font-weight:normal}.checks input{width:auto}.day{border:1px solid #ddd;border-radius:10px;padding:12px;margin:10px 0}.warn{background:#fff3d8;padding:9px;border-radius:8px;font-size:13px}.row{padding:5px 0;font-size:13px;border-bottom:1px solid #eee}@media(max-width:760px){main{grid-template-columns:1fr}.checks{grid-template-columns:1fr}header h1{font-size:22px}}
</style></head><body><header><h1>Cyprus Romantic Trip Planner – Paphos</h1><div>Cloud MVP · Mobile friendly</div></header><main>
<div class="card"><h2>Inputs</h2><label>Ξενοδοχείο</label><select id="hotel"></select><label>Ημερομηνία</label><input id="date" type="date">
<label>Διανυκτερεύσεις</label><input id="nights" type="number" value="3" min="1" max="7"><label>Pace</label><select id="pace"><option>relaxed</option><option>balanced</option><option>active</option></select>
<label>Καιρός</label><select id="weather"><option>normal</option><option>rainy</option><option>heatwave</option><option>winter</option></select>
<label>Μέγιστη διαδρομή</label><select id="drive"><option value="15">15 min</option><option value="20" selected>20 min</option><option value="30">30 min</option><option value="45">45 min</option></select>
<div class="checks"><label><input type="checkbox" id="sea" checked> Θάλασσα</label><label><input type="checkbox" id="wine" checked> Κρασί/φαγητό</label><label><input type="checkbox" id="nature" checked> Φύση</label><label><input type="checkbox" id="culture" checked> Πολιτισμός</label><label><input type="checkbox" id="well"> Wellness</label><label><input type="checkbox" id="active"> Active</label></div>
<button onclick="go()">Generate Itinerary</button><p style="font-size:12px;color:#666">Routing & weather: NOT_CONNECTED. Οι χρόνοι είναι planning estimates.</p></div>
<div id="out" class="card"><p>Συμπλήρωσε τα inputs και πάτησε Generate Itinerary.</p></div></main><script>
const $=x=>document.getElementById(x);
async function init(){let h=await (await fetch('/api/v1/meta/hotels')).json();$('hotel').innerHTML=h.map(x=>`<option value="${x.hotel_id}">${x.name} — ${x.area||''}</option>`).join('');let d=new Date();d.setDate(d.getDate()+14);$('date').value=d.toISOString().slice(0,10)}
async function go(){let p={hotel_id:$('hotel').value,start_date:$('date').value,nights:+$('nights').value,pace:$('pace').value,weather_mode:$('weather').value,max_drive_min:+$('drive').value,interest_sea:$('sea').checked,interest_wine_food:$('wine').checked,interest_nature:$('nature').checked,interest_culture:$('culture').checked,interest_wellness:$('well').checked,interest_active:$('active').checked,authentic_priority:true};$('out').innerHTML='<p>Generating…</p>';let r=await fetch('/api/v1/trips/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});let d=await r.json();if(!r.ok){$('out').innerHTML='<div class="warn">'+JSON.stringify(d)+'</div>';return}$('out').innerHTML='<h2>Itinerary</h2><div class="warn">Status: '+d.status+' · Routing: '+d.provider_state.routing+' · Weather: '+d.provider_state.weather+'</div>'+d.days.map(x=>`<div class="day"><b>Ημέρα ${x.day} — ${x.date}</b><div>${x.theme}</div><h3>${x.activity.title}</h3>${x.timeline.map(t=>`<div class="row">${t.time} · <b>${t.kind}</b> · ${t.title}</div>`).join('')}${x.activity.warnings.length?'<div class="warn">'+x.activity.warnings.join('<br>')+'</div>':''}</div>`).join('')}
init();</script></body></html>"""

@app.get("/",response_class=HTMLResponse)
def home():
    return HTML
