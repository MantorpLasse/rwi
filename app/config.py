from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Runway Safe Intelligence"
    database_url: str = "sqlite:///./data/runway_safe.db"
    debug: bool = True
    faa_emas_source_url: str = (
        "https://explore.dot.gov/t/FAA/views/EMASIncidentsandInstallations/Main"
        "?:embed=yes&:toolbar=no"
    )
    acquisition_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
