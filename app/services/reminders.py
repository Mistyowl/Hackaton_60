from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    return datetime.now(MOSCOW)


def in_quiet_hours(moment: datetime | None = None) -> bool:
    current = moment or moscow_now()
    return not 9 <= current.hour < 21


def _errors_word(count: int) -> str:
    number = abs(count) % 100
    tail = number % 10
    if 11 <= number <= 14:
        return "незакреплённых ошибок"
    if tail == 1:
        return "незакреплённая ошибка"
    if 2 <= tail <= 4:
        return "незакреплённые ошибки"
    return "незакреплённых ошибок"


def reminder_text(open_errors: int) -> str | None:
    if open_errors <= 0:
        return None
    return (
        f"В банке {open_errors} {_errors_word(open_errors)}. "
        "Короткий квиз по ним займёт пару минут и поможет закрепить приём. "
        "Выключить напоминания можно в профиле кабинета."
    )
