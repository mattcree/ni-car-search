from __future__ import annotations

from pydantic import BaseModel, Field


# ── request models ──────────────────────────────────────────────────────────


class WatchCreate(BaseModel):
    make: str
    model: str
    location: str = "lisburn"
    radius: int = Field(80, ge=1, le=500)
    min_price: int | None = Field(None, ge=0)
    max_price: int | None = Field(None, ge=0)
    min_year: int | None = Field(None, ge=1900, le=2100)
    max_year: int | None = Field(None, ge=1900, le=2100)
    poll_interval_minutes: int = Field(30, ge=5, le=1440)


class WatchUpdate(BaseModel):
    make: str | None = None
    model: str | None = None
    location: str | None = None
    radius: int | None = Field(None, ge=1, le=500)
    min_price: int | None = Field(None, ge=0)
    max_price: int | None = Field(None, ge=0)
    min_year: int | None = Field(None, ge=1900, le=2100)
    max_year: int | None = Field(None, ge=1900, le=2100)
    poll_interval_minutes: int | None = Field(None, ge=5, le=1440)
    enabled: bool | None = None


class SettingsUpdate(BaseModel):
    ntfy_url: str = ""
    ntfy_topic: str = ""
    app_url: str = ""


# ── response models ─────────────────────────────────────────────────────────


class WatchResponse(BaseModel):
    id: int
    make: str
    model: str
    make_display: str = ""
    model_display: str = ""
    location: str
    radius: int | None
    min_price: int | None
    max_price: int | None
    min_year: int | None
    max_year: int | None
    poll_interval_minutes: int
    enabled: int
    created_at: str
    last_polled_at: str | None
    vehicle_count: int = 0
    active_count: int = 0
    gone_count: int = 0
    health: str = "unknown"
    next_run: str | None = None
    in_flight: bool = False


class EventResponse(BaseModel):
    id: int
    vehicle_id: int
    listing_id: int | None
    event_type: str
    timestamp: str
    price: int | None
    old_price: int | None
    source: str | None


class ListingResponse(BaseModel):
    id: int
    vehicle_id: int
    watch_id: int
    url: str
    source: str
    title: str
    price: int | None
    year: int | None
    mileage: str | None
    location: str | None
    transmission: str | None
    body_type: str | None
    fuel_type: str | None
    image_url: str | None
    status: str
    first_seen_at: str
    last_seen_at: str
    gone_at: str | None


class VehicleResponse(BaseModel):
    id: int
    watch_id: int
    fingerprint: str
    year: int | None
    mileage_bucket: int | None
    transmission: str | None
    status: str
    first_seen_at: str
    last_seen_at: str
    gone_at: str | None
    # Computed from listings
    best_title: str = ""
    best_price: int | None = None
    listing_count: int = 0
    sources: list[str] = []
    price_direction: str | None = None
    price_delta: int | None = None
    is_new: bool = False


class VehicleDetailResponse(VehicleResponse):
    listings: list[ListingResponse]
    events: list[EventResponse]


class ScrapeRunResponse(BaseModel):
    id: int
    watch_id: int
    started_at: str
    finished_at: str | None
    total_found: int | None
    new_count: int | None
    new_source_count: int | None
    gone_count: int | None
    price_changed_count: int | None
    returned_count: int | None
    errors: str | None
    scraper_counts: dict[str, int] = {}


class WatchStatsResponse(BaseModel):
    active: int
    gone: int
    total_vehicles: int
    total_price_changes: int
    last_run: ScrapeRunResponse | None


class ScrapeResultResponse(BaseModel):
    run_id: int
    total_found: int
    new: int
    new_sources: int
    gone: int
    price_changed: int
    returned: int
    errors: dict[str, str]


class RunEventResponse(BaseModel):
    id: int
    run_id: int
    event_type: str
    timestamp: str
    source: str | None
    count: int | None
    message: str | None


class RunDetailResponse(ScrapeRunResponse):
    run_events: list[RunEventResponse]
    vehicle_events: list[EventResponse]


class FeedEventResponse(BaseModel):
    id: int
    event_type: str
    timestamp: str
    vehicle_id: int
    vehicle_title: str
    vehicle_year: int | None
    vehicle_price: int | None
    price: int | None
    old_price: int | None
    source: str | None
    watch_id: int
    watch_make: str
    watch_model: str
    watch_make_display: str = ""
    watch_model_display: str = ""


class SchedulerJobResponse(BaseModel):
    id: str
    name: str | None
    next_run: str | None
    in_flight: bool
