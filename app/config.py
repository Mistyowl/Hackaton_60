from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


ACCESS_CLOSED = "Бот пока открыт только для участников теста."


def parse_allowed_user_ids(raw: str) -> set[int]:
    found: set[int] = set()
    for part in (raw or "").split(","):
        piece = part.strip()
        if not piece:
            continue
        if not piece.isdigit():
            raise ValueError("ALLOWED_USER_IDS содержит нечисловой id")
        found.add(int(piece))
    return found


def user_allowed(user_id: int, allowed: set[int]) -> bool:
    return not allowed or user_id in allowed


def gemini_proxy_url(host: str, port: int, username: str, password: str) -> str:
    cleaned = host.strip()
    if not cleaned:
        return ""
    if not 1 <= port <= 65535:
        raise ValueError("PROXY_PORT нужен, когда задан PROXY_HOST")
    auth = ""
    user = username.strip()
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    return f"socks5://{auth}{cleaned}:{port}"


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
    cabinet_url: str = ""
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_username: str = ""
    proxy_password: str = ""
    allowed_user_ids: str = ""

    def allowed_ids(self) -> set[int]:
        return parse_allowed_user_ids(self.allowed_user_ids)

    def gemini_proxy_url(self) -> str:
        return gemini_proxy_url(
            self.proxy_host,
            self.proxy_port,
            self.proxy_username,
            self.proxy_password,
        )


def get_settings() -> Settings:
    return Settings()
