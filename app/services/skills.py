import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import Database
from app.services.tutor import Tutor
from app.textutil import utcnow

BOX_DAYS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
STABILITY_DAYS = {1: 2, 2: 4, 3: 8, 4: 16, 5: 32}
DIM_BRIGHTNESS = 0.45
_SPACE = re.compile(r"\s+")
_NOISE = re.compile(r"[^\w\sа-яё-]", re.IGNORECASE)


@dataclass
class SkillChange:
    skill_id: int
    name: str
    before: float
    after: float
    sentence: str

    @property
    def brighter(self) -> bool:
        return self.after > self.before + 0.01


def normalize_name(value: str) -> str:
    cleaned = value.replace("ё", "е").replace("Ё", "Е")
    cleaned = _NOISE.sub(" ", cleaned)
    cleaned = _SPACE.sub(" ", cleaned).strip().casefold()
    return cleaned[:80]


def next_box(box: int, correct: bool) -> int:
    current = box if 1 <= box <= 5 else 1
    if not correct:
        return 1
    return min(5, current + 1)


def strength_for(box: int, correct: bool) -> float:
    if not correct:
        return 0.2
    return next_box(box, True) / 5


def due_after(box: int, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    days = BOX_DAYS.get(box, 1)
    return (moment + timedelta(days=days)).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    text = (value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def brightness(strength: float, box: int, last_seen_at: str, now: datetime | None = None) -> float:
    moment = now or datetime.now(timezone.utc)
    seen = parse_time(last_seen_at)
    days = max(0.0, (moment - seen).total_seconds() / 86400)
    stability = STABILITY_DAYS.get(box if 1 <= box <= 5 else 1, 2)
    value = float(strength) * math.exp(-days / stability)
    return max(0.0, min(1.0, value))


def is_dim(skill: dict, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    level = brightness(skill["strength"], int(skill["box"]), skill["last_seen_at"], moment)
    if level < DIM_BRIGHTNESS:
        return True
    return parse_time(skill["due_at"]) <= moment


def star_position(skill_id: int) -> tuple[float, float]:
    digest = hashlib.sha256(str(skill_id).encode()).digest()
    x = 14 + digest[0] / 255 * 72
    y = 16 + digest[1] / 255 * 62
    return round(x, 1), round(y, 1)


def _cell_position(skill_id: int, column: int, row: int, columns: int, rows: int) -> tuple[float, float]:
    digest = hashlib.sha256(str(skill_id).encode()).digest()
    width = 84 / columns
    height = 68 / rows
    x = 8 + column * width + 4 + digest[0] / 255 * (width - 10)
    y = 8 + row * height + 4 + digest[1] / 255 * (height - 10)
    return round(x, 1), round(y, 1)


def sentence_for(name: str, before: float, after: float) -> str:
    if after > before + 0.01:
        return f"Звезда «{name}» стала ярче."
    if after + 0.01 < before:
        return f"Звезда «{name}» потускнела. Зажжём её?"
    return f"Звезда «{name}» на месте."


def sky_payload(skills: list[dict], now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    groups: dict[str, list[dict]] = {}
    bright = 0
    dim = 0
    for skill in skills:
        level = brightness(skill["strength"], int(skill["box"]), skill["last_seen_at"], moment)
        faded = is_dim(skill, moment)
        if faded:
            dim += 1
        else:
            bright += 1
        star = {
            "id": skill["id"],
            "name": skill["name"],
            "brightness": round(level, 3),
            "due": parse_time(skill["due_at"]) <= moment,
            "dim": faded,
            "box": int(skill["box"]),
            "last_seen_at": skill["last_seen_at"],
            "x": 0.0,
            "y": 0.0,
        }
        groups.setdefault(skill["subject"] or "Школьный предмет", []).append(star)
    subjects = list(groups)
    columns = 1 if len(subjects) <= 1 else 2
    rows = max(1, (len(subjects) + columns - 1) // columns)
    constellations = []
    for index, subject in enumerate(subjects):
        stars = groups[subject]
        for star in stars:
            star["x"], star["y"] = _cell_position(
                int(star["id"]),
                index % columns,
                index // columns,
                columns,
                rows,
            )
        constellations.append({"subject": subject, "stars": stars})
    return {
        "summary": {"total": len(skills), "bright": bright, "dim": dim},
        "constellations": constellations,
    }


async def _match_name(tutor: Tutor | None, raw: str, known: list[dict]) -> tuple[int | None, str]:
    cleaned = " ".join(raw.split()).strip()
    label = cleaned[:80] or "приём"
    key = normalize_name(label)
    for skill in known:
        if normalize_name(skill["name"]) == key and key:
            return int(skill["id"]), skill["name"]
    if tutor is None or not tutor.enabled or not known or not key:
        return None, label[:60]
    try:
        picked = await tutor.match_skill(label, known)
    except Exception:
        return None, label[:60]
    if picked is None:
        return None, label[:60]
    skill_id, name = picked
    if skill_id:
        for skill in known:
            if int(skill["id"]) == skill_id:
                return skill_id, skill["name"]
    return None, (name or label)[:60]


async def record(
    db: Database,
    user_id: int,
    subject: str,
    raw_skill: str,
    correct: bool,
    kind: str,
    tutor: Tutor | None = None,
) -> SkillChange | None:
    label = " ".join((raw_skill or "").split()).strip()
    if not label:
        return None
    subject_name = " ".join((subject or "").split()).strip()[:80] or "Школьный предмет"
    known = await db.list_skills(user_id)
    same_subject = [item for item in known if (item["subject"] or "") == subject_name] or known
    skill_id, name = await _match_name(tutor, label, same_subject)
    now = datetime.now(timezone.utc)
    stamp = utcnow()
    current = await db.get_skill(user_id, skill_id) if skill_id else None
    if current is None:
        box = 2 if correct else 1
        strength = box / 5
        before = 0.0
        skill_id = await db.insert_skill(
            user_id,
            subject_name,
            name[:80],
            strength,
            box,
            stamp,
            due_after(box, now),
        )
    else:
        before = brightness(current["strength"], int(current["box"]), current["last_seen_at"], now)
        box = next_box(int(current["box"]), correct)
        strength = 0.2 if not correct else box / 5
        await db.update_skill(int(current["id"]), strength, box, stamp, due_after(box, now))
        skill_id = int(current["id"])
        name = current["name"]
    await db.insert_skill_event(skill_id, kind, correct)
    if box >= 3:
        await db.resolve_errors(user_id, skill_id)
    saved = await db.get_skill(user_id, skill_id)
    after = brightness(saved["strength"], int(saved["box"]), saved["last_seen_at"], now) if saved else strength
    text = sentence_for(name, before, after)
    return SkillChange(skill_id, name, round(before, 3), round(after, 3), text)


def dim_skills(skills: list[dict], limit: int = 3, now: datetime | None = None) -> list[dict]:
    moment = now or datetime.now(timezone.utc)
    faded = [skill for skill in skills if is_dim(skill, moment)]
    faded.sort(key=lambda skill: brightness(skill["strength"], int(skill["box"]), skill["last_seen_at"], moment))
    return faded[:limit]
