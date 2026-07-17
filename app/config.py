from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Runway Safe Intelligence"
    database_url: str = "sqlite:///./data/runway_safe.db"
    debug: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
