from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Runway Safe Intelligence"
    database_url: str = "sqlite:///./data/runway_safe.db"
    debug: bool = True
    faa_emas_source_url: str = (
        "https://explore.dot.gov/t/FAA/views/EMASIncidentsandInstallations/Main"
        "?:embed=yes&:toolbar=no"
    )
    faa_emas_article_url: str = (
        "https://www.faa.gov/newsroom/engineered-material-arresting-system-emas-0"
    )
    faa_emas_tableau_view_url: str | None = faa_emas_source_url
    acquisition_timeout_seconds: float = 30.0
    acquisition_user_agent: str = "RunwaySafeIntelligence/1.0"
    # Optional: Discovery Search Foundation's Brave Search adapter
    # (app.discovery.brave_search_provider). No default value - absence
    # simply means the provider fails closed at call time (Mission #9D
    # Part H); never set a credential default here.
    brave_search_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
