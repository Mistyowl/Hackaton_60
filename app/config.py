import re
from urllib.parse import quote, urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ACCESS_CLOSED = "Бот пока открыт только для участников теста."
_WEBHOOK_SECRET = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


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


def webhook_path(url: str) -> str:
    path = urlparse(url.strip()).path.rstrip("/")
    return path or "/max-webhook"


def validate_webhook(url: str, secret: str) -> tuple[str, str]:
    cleaned_url = url.strip()
    cleaned_secret = secret.strip()
    if not cleaned_url:
        return "", ""
    parsed = urlparse(cleaned_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("MAX_WEBHOOK_URL нужен как https://host/path")
    if parsed.port not in (None, 443):
        raise ValueError("MAX_WEBHOOK_URL должен быть на порту 443")
    if not _WEBHOOK_SECRET.fullmatch(cleaned_secret):
        raise ValueError(
            "MAX_WEBHOOK_SECRET: 5–256 символов A-Z a-z 0-9 _ -"
        )
    return cleaned_url, cleaned_secret


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    max_bot_token: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Запасные модели через запятую, если основная отвечает 429/503.
    gemini_fallback_model: str = "gemini-3.8-flash,gemma-4-31b-it"
    database_path: str = "data/tutor.db"
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    cabinet_url: str = ""
    # Пустой URL — long polling. Для нативных голосовых нужен HTTPS webhook:
    # в polling MAX часто отдаёт пустой message_created без вложения.
    max_webhook_url: str = ""
    max_webhook_secret: str = ""
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_username: str = ""
    proxy_password: str = ""
    allowed_user_ids: str = ""
    week_quiz_goal: int = Field(default=5, ge=1, le=50)

    def allowed_ids(self) -> set[int]:
        return parse_allowed_user_ids(self.allowed_user_ids)

    def gemini_proxy_url(self) -> str:
        return gemini_proxy_url(
            self.proxy_host,
            self.proxy_port,
            self.proxy_username,
            self.proxy_password,
        )

    def webhook(self) -> tuple[str, str]:
        return validate_webhook(self.max_webhook_url, self.max_webhook_secret)


def get_settings() -> Settings:
    return Settings()
