import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlencode

MAX_INIT_AGE_SECONDS = 3600
MAX_INIT_FUTURE_SECONDS = 30
_MAX_INIT_CHARS = 4096


@dataclass(frozen=True)
class WebAppUser:
    user_id: int
    first_name: str


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    if "hash" in fields:
        raise ValueError("hash считается отдельно")
    ordered = sorted(fields.items())
    launch = "\n".join(f"{key}={value}" for key, value in ordered)
    signed = [*ordered, ("hash", _signature(launch, bot_token))]
    return urlencode(signed, quote_via=quote)


def validate_init_data(init_data: str, bot_token: str, now: float) -> WebAppUser | None:
    token = bot_token.strip()
    if not token or not init_data or len(init_data) > _MAX_INIT_CHARS:
        return None
    try:
        fields = _fields(init_data)
    except ValueError:
        return None
    received = fields.pop("hash", None)
    if received is None or len(fields) != len(set(fields)):
        return None
    launch = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    expected = _signature(launch, token)
    if not _same_hash(received, expected):
        return None
    auth_date = _auth_date(fields.get("auth_date", ""), now)
    if auth_date is None:
        return None
    return _user(fields.get("user", ""))


def _signature(launch: str, bot_token: str) -> str:
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, launch.encode(), hashlib.sha256).hexdigest()


def _same_hash(received: str, expected: str) -> bool:
    if len(received) != len(expected):
        return False
    if any(char not in "0123456789abcdef" for char in received.lower()):
        return False
    return hmac.compare_digest(received.lower(), expected)


def _fields(init_data: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in init_data.split("&"):
        if not part or "=" not in part:
            raise ValueError
        key, value = part.split("=", 1)
        if not key or key in fields:
            raise ValueError
        fields[key] = unquote(value)
    if "hash" not in fields:
        raise ValueError
    return fields


def _auth_date(raw: str, now: float) -> int | None:
    if not raw.isdigit():
        return None
    auth_date = int(raw)
    if auth_date > now + MAX_INIT_FUTURE_SECONDS:
        return None
    if now - auth_date > MAX_INIT_AGE_SECONDS:
        return None
    return auth_date


def _user(raw: str) -> WebAppUser | None:
    try:
        user = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(user, dict):
        return None
    user_id = user.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        return None
    if not 0 < user_id < 2**63:
        return None
    name = user.get("first_name") or ""
    if not isinstance(name, str):
        name = ""
    return WebAppUser(user_id, " ".join(name.split())[:128])
