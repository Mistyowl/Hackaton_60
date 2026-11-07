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


def _local_skill_match(key: str, known: list[dict]) -> tuple[int, str] | None:
    if not key:
        return None
    for skill in known:
        other = normalize_name(skill["name"])
        if other == key:
            return int(skill["id"]), skill["name"]
    for skill in known:
        other = normalize_name(skill["name"])
        if other and min(len(key), len(other)) >= 4 and (key in other or other in key):
            return int(skill["id"]), skill["name"]
    return None


async def _match_name(tutor: Tutor | None, raw: str, known: list[dict]) -> tuple[int | None, str]:
    cleaned = " ".join(raw.split()).strip()
    label = cleaned[:80] or "приём"
    key = normalize_name(label)
    local = _local_skill_match(key, known)
    if local is not None:
        return local
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


def _short_label(value: str, limit: int = 40) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def build_skill_graph(db: Database, user_id: int) -> dict:
    skills = await db.list_skills(user_id)
    if not skills:
        return {"nodes": [], "edges": [], "due_count": 0}
    now = datetime.now(timezone.utc)
    open_errors = await db.list_open_errors_with_skill(user_id, limit=80)
    errors_by_skill: dict[int, list[dict]] = {}
    for row in open_errors:
        skill_id = row.get("skill_id")
        if skill_id is None:
            continue
        errors_by_skill.setdefault(int(skill_id), []).append(row)

    nodes: list[dict] = []
    edges: list[dict] = []
    subjects: set[str] = set()
    due_count = 0
    for skill in skills:
        subject = " ".join((skill.get("subject") or "").split()).strip() or "Школьный предмет"
        skill_id = int(skill["id"])
        level = brightness(skill["strength"], int(skill["box"]), skill["last_seen_at"], now)
        due = is_dim(skill, now)
        if due:
            due_count += 1
        linked = errors_by_skill.get(skill_id, [])
        if subject not in subjects:
            subjects.add(subject)
            nodes.append(
                {
                    "id": f"subj:{subject}",
                    "kind": "subject",
                    "label": subject,
                }
            )
        nodes.append(
            {
                "id": f"skill:{skill_id}",
                "kind": "skill",
                "label": skill["name"],
                "skill_id": skill_id,
                "subject": subject,
                "brightness": round(level, 3),
                "due": due,
                "box": int(skill["box"]),
                "open_errors": len(linked),
            }
        )
        edges.append({"from": f"subj:{subject}", "to": f"skill:{skill_id}"})
        for row in linked[:6]:
            error_id = int(row["id"])
            nodes.append(
                {
                    "id": f"error:{error_id}",
                    "kind": "error",
                    "label": _short_label(row.get("mistake") or "ошибка"),
                    "error_id": error_id,
                    "skill_id": skill_id,
                }
            )
            edges.append({"from": f"skill:{skill_id}", "to": f"error:{error_id}"})
    return {"nodes": nodes, "edges": edges, "due_count": due_count}


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
    return SkillChange(skill_id, name, round(before, 3), round(after, 3))
