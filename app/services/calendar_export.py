from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.reminders import MOSCOW, moscow_now
from app.services.skills import parse_time

EMPTY_CALENDAR = "Пока нечего ставить в календарь."
_MAX_ERRORS = 14
_MAX_SKILLS = 14
_EVENT_MINUTES = 20
_SLOT_HOUR = 19
_CABINET_HINT = "Открой банк ошибок в кабинете и закрепи приём."


def _clip(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _fold(line: str) -> str:
    # RFC 5545 считает октеты; для кириллицы режем по символам — календарю хватает.
    limit = 75
    if len(line) <= limit:
        return line
    parts: list[str] = []
    start = 0
    while start < len(line):
        size = limit if start == 0 else limit - 1
        end = min(start + size, len(line))
        chunk = line[start:end]
        parts.append(chunk if start == 0 else " " + chunk)
        start = end
    return "\r\n".join(parts)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def next_evening_slot(moment: datetime | None = None) -> datetime:
    current = moment or moscow_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    else:
        current = current.astimezone(MOSCOW)
    candidate = current.replace(hour=_SLOT_HOUR, minute=0, second=0, microsecond=0)
    if current >= candidate:
        candidate += timedelta(days=1)
    return candidate


def _evening_on(day: datetime) -> datetime:
    local = day.astimezone(MOSCOW) if day.tzinfo else day.replace(tzinfo=MOSCOW)
    return local.replace(hour=_SLOT_HOUR, minute=0, second=0, microsecond=0)


def _line(name: str, value: str) -> str:
    return _fold(f"{name}:{_escape(value)}")


def _event(
    uid: str,
    start: datetime,
    summary: str,
    description: str,
    now: datetime,
) -> list[str]:
    end = start + timedelta(minutes=_EVENT_MINUTES)
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(now)}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        _line("SUMMARY", summary),
        _line("DESCRIPTION", description),
        "END:VEVENT",
    ]


def _error_title(row: dict) -> str:
    skill = (row.get("skill_name") or row.get("skill") or "").strip()
    if skill:
        return "Разобрать: " + _clip(skill, 60)
    problem = (row.get("problem") or row.get("mistake") or "ошибка").strip()
    return "Разобрать: " + _clip(problem, 60)


def _error_description(row: dict) -> str:
    subject = (row.get("subject") or "").strip() or "Предмет"
    problem = _clip(row.get("problem") or row.get("mistake") or "", 120)
    parts = [subject + "."]
    if problem:
        parts.append(problem)
    parts.append(_CABINET_HINT)
    return " ".join(parts)


def build_calendar(
    errors: list[dict],
    skills: list[dict],
    now: datetime | None = None,
) -> str | None:
    moment = now or moscow_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MOSCOW)
    else:
        moment = moment.astimezone(MOSCOW)

    open_errors = [row for row in errors if not row.get("resolved_at")][:_MAX_ERRORS]
    used_skills = {
        int(row["skill_id"])
        for row in open_errors
        if row.get("skill_id") is not None
    }

    slot = next_evening_slot(moment)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Lumi//Tutor//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    event_count = 0

    for row in open_errors:
        lines.extend(
            _event(
                f"lumi-error-{int(row['id'])}@tutor",
                slot,
                _error_title(row),
                _error_description(row),
                moment,
            )
        )
        event_count += 1
        slot += timedelta(days=1)

    skill_rows = sorted(
        (
            row
            for row in skills
            if int(row["id"]) not in used_skills and (row.get("name") or "").strip()
        ),
        key=lambda row: parse_time(row.get("due_at") or ""),
    )[:_MAX_SKILLS]

    overdue_slot = slot
    for row in skill_rows:
        due = parse_time(row.get("due_at") or "")
        due_local = due.astimezone(MOSCOW)
        planned = _evening_on(due_local)
        if planned < next_evening_slot(moment):
            start = overdue_slot
            overdue_slot += timedelta(days=1)
        else:
            start = planned
        name = _clip(row["name"], 60)
        subject = (row.get("subject") or "").strip() or "Предмет"
        lines.extend(
            _event(
                f"lumi-skill-{int(row['id'])}@tutor",
                start,
                "Повторить: " + name,
                f"{subject}. {_CABINET_HINT}",
                moment,
            )
        )
        event_count += 1

    if event_count == 0:
        return None
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
