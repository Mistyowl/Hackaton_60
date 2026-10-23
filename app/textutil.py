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


_MARK = re.compile(r"\*\*([^*\n]+)\*\*|`([^`\n]+)`")
_LABEL = re.compile(r"^(Пример|Итог|Ответ|Шаг\s+\d+)\s*:\s*(.*)$", re.IGNORECASE)
_TRAILING_LABEL = re.compile(r"(Пример|Итог|Ответ):\s*$", re.IGNORECASE)
_CYRILLIC_WORD = re.compile(r"[А-Яа-яЁё]{3,}")
_INLINE_FORMULA = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё0-9_])"
    r"([A-Za-z0-9()]+\s*/\s*[A-Za-z0-9()]+"
    r"(?:\s*[:*+\-×÷]\s*(?:[A-Za-z0-9()]+\s*/\s*[A-Za-z0-9()]+|\([^)]*\)|[A-Za-z0-9()]+))+"
    r"\s*=\s*[A-Za-z0-9()*/:+\-×÷ ]*[A-Za-z0-9)])"
)


def model_html(value: str) -> str:
    rendered = "\n".join(_format_line(line) for line in (value or "").split("\n"))
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def _format_line(line: str) -> str:
    stripped = line.strip()
    label = _LABEL.match(stripped)
    if label and _is_formula(label.group(2)):
        return f"<b>{esc(label.group(1))}:</b>\n<b>{esc(label.group(2).strip())}</b>"
    if _is_formula(stripped):
        return f"<b>{esc(stripped)}</b>"
    return _markup_inline(line)


def _markup_inline(text: str) -> str:
    chunks: list[str] = []
    position = 0
    for match in _MARK.finditer(text):
        chunks.append(_plain(text[position : match.start()]))
        if match.group(1) is not None:
            chunks.append("<b>" + esc(match.group(1)) + "</b>")
            position = match.end()
            continue
        formula = match.group(2).strip()
        label = ""
        if "=" in formula and chunks:
            chunks[-1], label = _detach_label(chunks[-1])
            if label:
                chunks.append(label)
        piece = ("<b>" if "=" in formula else "<code>") + esc(formula) + ("</b>" if "=" in formula else "</code>")
        if "=" in formula and not label:
            piece = "\n" + piece
        chunks.append(piece + ("\n" if "=" in formula else ""))
        position = match.end()
        if "=" in formula and position < len(text) and text[position] == ".":
            position += 1
            if position < len(text) and text[position] == " ":
                position += 1
    chunks.append(_plain(text[position:]))
    return "".join(chunks)


def _plain(text: str) -> str:
    if not text:
        return ""
    chunks: list[str] = []
    position = 0
    for match in _INLINE_FORMULA.finditer(text):
        before, label = _detach_label(esc(text[position : match.start()]))
        chunks.append(before)
        if label:
            chunks.append(label)
        chunks.append("\n<b>" + esc(match.group(1).strip()) + "</b>\n")
        position = match.end()
        if position < len(text) and text[position] == ".":
            position += 1
            if position < len(text) and text[position] == " ":
                position += 1
    chunks.append(esc(text[position:]))
    return "".join(chunks)


def _detach_label(html: str) -> tuple[str, str]:
    match = _TRAILING_LABEL.search(html)
    if match is None:
        return html, ""
    return html[: match.start()].rstrip(), f"\n<b>{match.group(1)}:</b>\n"


def _is_formula(line: str) -> bool:
    stripped = line.strip()
    if not stripped or "=" not in stripped or len(stripped) > 180:
        return False
    return len(_CYRILLIC_WORD.findall(stripped)) <= 1


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
