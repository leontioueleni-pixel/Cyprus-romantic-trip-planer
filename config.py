from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = "dev"
    database_url: str = "postgresql://planner:planner@localhost:5432/planner"
    google_routes_api_key: str = ""
    weather_base_url: str = "https://api.open-meteo.com/v1/forecast"
    content_version: str = "content_2026_08_27"
    rules_version: str = "rules_1_0"
    route_cache_ttl_sec: int = 900
    weather_cache_ttl_sec: int = 3600
    route_fresh_max_sec: int = 3600
    weather_fresh_max_sec: int = 21600
    max_route_calls_per_trip: int = 20
    provider_concurrency: int = 3
    enable_live_providers: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
