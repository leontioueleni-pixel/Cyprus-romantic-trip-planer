from __future__ import annotations
from datetime import datetime, time, timedelta
from .schemas import TimelineItem, DailyTimelineQA

def add_minutes(t: time, mins: int) -> time:
    return (datetime.combine(datetime.today(),t)+timedelta(minutes=mins)).time()

def minutes_between(a: time,b: time) -> int:
    dt1=datetime.combine(datetime.today(),a)
    dt2=datetime.combine(datetime.today(),b)
    return max(0,int((dt2-dt1).total_seconds()//60))

def _append(items, kind, title, start, end, entity_id=None, status="PASS", note=None):
    items.append(TimelineItem(kind=kind,title=title,start_time=start,end_time=end,
                              entity_id=entity_id,status=status,note=note))

def build_day_timeline(day, hotel_name: str, day_number: int) -> tuple[list[TimelineItem],DailyTimelineQA]:
    items=[]
    warnings=[]

    # Day 1 retains the protected arrival/check-in/lunch/rest structure.
    if day_number==1 and day.fixed_blocks:
        for b in day.fixed_blocks:
            status="PASS"
            _append(items,b.kind if b.kind in {"hotel","travel","activity","meal","coffee","rest","buffer"} else "buffer",
                    b.title,b.start_time,b.end_time,status=status)

    activity=day.activity
    # Anchor departure from hotel based on activity start and planning buffer.
    travel1=activity.planning_travel_min or 10
    depart=add_minutes(activity.start_time,-travel1)
    if not items or items[-1].end_time <= depart:
        _append(items,"hotel",f"At {hotel_name}",items[-1].end_time if items else time(9,30),depart,status="PASS")
    elif items[-1].end_time > depart:
        warnings.append("Activity departure overlaps previous fixed block")
    _append(items,"travel",f"Travel to {activity.title}",depart,activity.start_time,
            status="PLANNING",note="Planning buffer; requires live routing")
    _append(items,"activity",activity.title,activity.start_time,activity.end_time,
            entity_id=activity.entity_id,status="PASS" if activity.operational_status=="PASS" else "RECHECK",
            note=activity.warning)

    cursor=activity.end_time

    # Optional second stop on full days, only after primary activity and with an explicit transfer buffer.
    if getattr(day,"secondary_activity",None):
        sec=day.secondary_activity
        transfer=max(activity.planning_travel_min or 10,sec.planning_travel_min or 10)
        sec_depart=add_minutes(sec.start_time,-transfer)
        if sec_depart < cursor:
            warnings.append("Secondary stop transfer overlaps primary activity")
        else:
            if minutes_between(cursor,sec_depart)>=45:
                _append(items,"buffer","Lunch / free time / rest",cursor,sec_depart,status="PASS")
            _append(items,"travel",f"Travel to {sec.title}",sec_depart,sec.start_time,
                    status="PLANNING",note="Planning buffer; requires live routing")
            _append(items,"activity",sec.title,sec.start_time,sec.end_time,
                    entity_id=sec.entity_id,status="PASS" if sec.operational_status=="PASS" else "RECHECK",
                    note=sec.warning)
            cursor=sec.end_time

    # Dinner is placed next if it exists; otherwise return to hotel.
    if day.dinner:
        dinner=day.dinner
        transfer=max(activity.planning_travel_min or 10,dinner.planning_travel_min or 10)
        dinner_depart=add_minutes(dinner.start_time,-transfer)

        # If there is a meaningful gap, reserve it explicitly rather than leaving hidden time.
        if minutes_between(cursor,dinner_depart) >= 45:
            gap=minutes_between(cursor,dinner_depart)
            if gap >= 120:
                # Split the gap into free time → optional coffee → free time.
                coffee_start=add_minutes(cursor,30)
                coffee_end=min_time(add_minutes(coffee_start,45),dinner_depart)
                if coffee_start > cursor:
                    _append(items,"buffer","Free time / rest",cursor,coffee_start,status="PASS")
                if coffee_end > coffee_start:
                    _append(items,"coffee","Coffee / dessert or free time",coffee_start,coffee_end,status="PLANNING",
                            note="Flexible block; venue selected later")
                if dinner_depart > coffee_end:
                    _append(items,"buffer","Free time / rest",coffee_end,dinner_depart,status="PASS")
            else:
                _append(items,"buffer","Free time / rest",cursor,dinner_depart,status="PASS")

        if dinner_depart < cursor:
            warnings.append("Insufficient transfer time between activity and dinner")
            dinner_depart=cursor
        _append(items,"travel",f"Travel to {dinner.title}",dinner_depart,dinner.start_time,
                status="PLANNING",note="Planning buffer; requires live routing")
        _append(items,"meal",dinner.title,dinner.start_time,dinner.end_time,
                entity_id=dinner.entity_id,status="PASS" if dinner.operational_status=="PASS" else "RECHECK",
                note=dinner.warning)
        return_travel=dinner.planning_travel_min or 10
        ret_end=add_minutes(dinner.end_time,return_travel)
        _append(items,"travel",f"Return to {hotel_name}",dinner.end_time,ret_end,status="PLANNING",
                note="Planning buffer; requires live routing")
        _append(items,"hotel",f"Back at {hotel_name}",ret_end,ret_end,status="PASS")
    else:
        ret=activity.planning_travel_min or 10
        ret_end=add_minutes(cursor,ret)
        _append(items,"travel",f"Return to {hotel_name}",cursor,ret_end,status="PLANNING",
                note="Planning buffer; requires live routing")
        _append(items,"hotel",f"Back at {hotel_name}",ret_end,ret_end,status="PASS")

    qa=timeline_qa(items,warnings)
    return items,qa

def min_time(a: time,b: time) -> time:
    return a if a<=b else b

def timeline_qa(items: list[TimelineItem], warnings: list[str] | None=None) -> DailyTimelineQA:
    warnings=list(warnings or [])
    overlaps=0
    travel=0
    external=0
    start=None
    end=None
    prev_end=None
    for x in items:
        if start is None or x.start_time < start: start=x.start_time
        if end is None or x.end_time > end: end=x.end_time
        if prev_end is not None and x.start_time < prev_end:
            overlaps+=1
        prev_end=max_time(prev_end,x.end_time) if prev_end else x.end_time
        if x.kind=="travel":
            travel+=minutes_between(x.start_time,x.end_time)
        if x.kind in {"activity","meal","coffee"} and x.entity_id:
            external+=1
    # "Planned" load counts commitments, not idle hotel/free-time gaps.
    committed=0
    for x in items:
        if x.kind in {"travel","activity","meal","coffee"}:
            committed += minutes_between(x.start_time,x.end_time)
    total=committed
    load="LIGHT"
    if committed>=600 or external>=5: load="OVERLOADED"
    elif committed>=420 or external>=4: load="BUSY"
    elif committed>=180 or external>=2: load="BALANCED"
    if travel>120:
        warnings.append("More than 120 minutes of planning travel in one day")
    if overlaps:
        warnings.append(f"{overlaps} timeline overlap(s) detected")
    status="BLOCKED" if overlaps else ("RECHECK" if warnings else "PASS")
    return DailyTimelineQA(overlap_count=overlaps,total_planned_minutes=total,total_travel_minutes=travel,
                           external_stop_count=external,load=load,status=status,warnings=warnings)

def max_time(a: time,b: time) -> time:
    return a if a>=b else b
