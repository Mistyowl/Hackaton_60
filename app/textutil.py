import html
import re
from datetime import datetime, timezone

MESSAGE_LIMIT = 3900
MARK_START = "\x01"
MARK_END = "\x02"
LETTERS = ("А", "Б", "В", "Г")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def clip(value: str, limit: int = MESSAGE_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def button_label(value: str) -> str:
    clean = " ".join((value or "").split())
    if not clean:
        return "Конспект"
    if len(clean) > 40:
        return clean[:39] + "…"
    return clean


def format_stamp(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return moment.strftime("%d.%m.%Y %H:%M")


def format_elapsed(started_at: str, now: datetime | None = None) -> str:
    start = datetime.fromisoformat(started_at)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - start).total_seconds()))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {sec} сек"
    return f"{sec} сек"


def parse_command(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, tail = stripped.partition(" ")
    command = head.split("@", 1)[0].lower()
    return command, tail.strip()


def fts_match(query: str) -> str:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    cleaned = [token.replace('"', "") for token in tokens if token.strip()]
    if not cleaned:
        return ""
    return " AND ".join(f'"{token}"' for token in cleaned[:8])


def markers_to_html(value: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == MARK_START:
            end = value.find(MARK_END, index + 1)
            if end == -1:
                parts.append(esc(value[index:]))
                break
            parts.append("<b>" + esc(value[index + 1 : end]) + "</b>")
            index = end + 1
            continue
        end = value.find(MARK_START, index)
        if end == -1:
            end = len(value)
        parts.append(esc(value[index:end]))
        index = end
    return "".join(parts)


def strip_markers(value: str) -> str:
    return value.replace(MARK_START, "").replace(MARK_END, "")


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data[4:8] == b"ftyp":
        return "image/heic"
    return "image/jpeg"


def check_prefix(text: str) -> str | None:
    matched = re.match(r"(?is)^проверь\b[\s:,-]*(.*)$", text.strip())
    if matched is None:
        return None
    return matched.group(1).strip()
