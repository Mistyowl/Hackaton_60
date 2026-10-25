import time

LIMIT = 30
WINDOW_SECONDS = 3600
_hits: dict[int, list[float]] = {}


def allow(user_id: int, now: float | None = None) -> bool:
    moment = time.monotonic() if now is None else now
    fresh = [stamp for stamp in _hits.get(user_id, []) if moment - stamp < WINDOW_SECONDS]
    if len(fresh) >= LIMIT:
        _hits[user_id] = fresh
        return False
    fresh.append(moment)
    _hits[user_id] = fresh
    return True


def reset() -> None:
    _hits.clear()
