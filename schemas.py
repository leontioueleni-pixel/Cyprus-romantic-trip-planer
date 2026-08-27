from datetime import date, time
from typing import Literal
from pydantic import BaseModel, Field

class TripRequest(BaseModel):
    hotel_id: str
    start_date: date
    nights: int = Field(3, ge=1, le=14)
    arrival_time_local: time = time(11, 0)
    early_checkin_assumed: bool = False
    transport: Literal["rental_car","own_car","taxi","no_car"] = "rental_car"
    max_drive_min: int = Field(20, ge=5, le=90)
    budget: Literal["budget","moderate","premium","luxury"] = "moderate"
    pace: Literal["very_relaxed","relaxed","balanced","active"] = "relaxed"
    authentic_priority: bool = True
    interest_sea: bool = True
    interest_wine_food: bool = True
    interest_nature: bool = True
    interest_culture: bool = True
    interest_wellness: bool = False
    interest_active: bool = False
    meal_preference: Literal["cypriot_local","mediterranean","any"] = "cypriot_local"
    mobility: Literal["no_limitation","light_walking","limited"] = "no_limitation"
    weather_mode: Literal["auto","normal","heatwave","rainy","winter"] = "auto"
    locale: str = "el-CY"
    currency: str = "EUR"

class ProviderState(BaseModel):
    routing: Literal["LIVE","STALE","FALLBACK","INVALID","NOT_CONNECTED"] = "NOT_CONNECTED"
    weather: Literal["LIVE","STALE","FALLBACK","INVALID","NOT_CONNECTED"] = "NOT_CONNECTED"

class TimeBlock(BaseModel):
    title: str
    start_time: time
    end_time: time
    kind: Literal["arrival","checkin","meal","rest","buffer","note"]

class ItineraryStop(BaseModel):
    entity_id: str
    title: str
    category: str
    cluster_id: str
    travel_band: str | None = None
    data_status: str
    start_time: time | None = None
    end_time: time | None = None
    operational_status: Literal["PASS","RECHECK","CLOSED","TIME_FAIL","WEATHER_FAIL"] = "RECHECK"
    booking_required: bool = False
    planning_travel_min: int | None = None
    warning: str | None = None
    warnings: list[str] = []


class TimelineItem(BaseModel):
    kind: Literal["hotel","travel","activity","meal","coffee","rest","buffer"]
    title: str
    start_time: time
    end_time: time
    entity_id: str | None = None
    status: Literal["PASS","RECHECK","PLANNING","BLOCKED"] = "PASS"
    note: str | None = None

class DailyTimelineQA(BaseModel):
    overlap_count: int = 0
    total_planned_minutes: int = 0
    total_travel_minutes: int = 0
    external_stop_count: int = 0
    load: Literal["LIGHT","BALANCED","BUSY","OVERLOADED"] = "BALANCED"
    status: Literal["PASS","RECHECK","BLOCKED"] = "PASS"
    warnings: list[str] = []

class ItineraryDay(BaseModel):
    day: int
    date: date
    theme: str
    fixed_blocks: list[TimeBlock] = []
    activity: ItineraryStop
    secondary_activity: ItineraryStop | None = None
    dinner: ItineraryStop | None = None
    weather_mode: str = "normal"
    weather_status: str = "FALLBACK"
    operational_status: Literal["PASS","RECHECK","BLOCKED"] = "RECHECK"
    timeline: list[TimelineItem] = []
    timeline_qa: DailyTimelineQA | None = None

class TripResponse(BaseModel):
    trip_id: str
    trip_version_id: str
    status: Literal["DRAFT","VALIDATING","READY","FALLBACK_READY","BLOCKED","FINALIZED"]
    content_version: str
    rules_version: str
    provider_state: ProviderState
    days: list[ItineraryDay]
    warnings: list[str] = []


class ValidationResponse(BaseModel):
    status: Literal["PASS","RECHECK","BLOCKED"]
    itinerary_status: str
    blockers: list[str] = []
    rechecks: list[str] = []
    warnings: list[str] = []
