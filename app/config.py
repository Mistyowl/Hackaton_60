from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    max_bot_token: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    database_path: str = "data/tutor.db"
    web_host: str = "127.0.0.1"
    web_port: int = 8000


def get_settings() -> Settings:
    return Settings()
