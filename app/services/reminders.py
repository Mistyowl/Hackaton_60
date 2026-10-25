from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import Database
from app.services.skills import dim_skills

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    return datetime.now(MOSCOW)


def in_quiet_hours(moment: datetime | None = None) -> bool:
    current = moment or moscow_now()
    return not 9 <= current.hour < 21


def star_word(count: int) -> str:
    number = abs(count) % 100
    tail = number % 10
    if 11 <= number <= 14:
        return "звёзд"
    if tail == 1:
        return "звезда"
    if 2 <= tail <= 4:
        return "звезды"
    return "звёзд"


def reminder_line(count: int) -> str:
    word = star_word(count)
    if count == 1:
        return "1 звезда тускнеет. 3 вопроса на 1 минуту."
    return f"{count} {word} тускнеют. 3 вопроса на 1 минуту."


async def due_reminder(db: Database, user_id: int) -> str | None:
    skills = await db.list_skills(user_id)
    faded = dim_skills(skills, limit=12)
    if not faded:
        return None
    return reminder_line(len(faded))
