import logging
from dataclasses import dataclass

from app.db import Database
from app.services.tutor import CheckResult, Tutor, TutorReply
from app.textutil import fts_match, markers_to_html, strip_markers

log = logging.getLogger(__name__)
PAGE_SIZE = 5


@dataclass
class NoteHit:
    id: int
    title: str
    subject: str
    snippet: str
    snippet_html: str
    semantic: bool


def note_body_from_reply(reply: TutorReply, include_solution: bool = True) -> str:
    parts = [reply.reply.strip()]
    if include_solution and reply.full_solution.strip():
        parts.append("Полный разбор:\n" + reply.full_solution.strip())
    if reply.skill:
        parts.append("Приём: " + reply.skill)
    return "\n\n".join(part for part in parts if part)


def note_body_from_check(check: dict) -> str:
    parts = []
    if check.get("problem"):
        parts.append("Условие:\n" + check["problem"])
    if check.get("student_solution"):
        parts.append("Решение ученика:\n" + check["student_solution"])
    if check.get("hint"):
        parts.append("Разбор:\n" + check["hint"])
    if check.get("full_explanation"):
        parts.append("Полный разбор:\n" + check["full_explanation"])
    if check.get("skill"):
        parts.append("Приём: " + check["skill"])
    return "\n\n".join(parts) or "Разбор решения"


async def save_reply(db: Database, user_id: int, reply: TutorReply, source: str) -> int:
    return await db.insert_note(
        user_id=user_id,
        title=reply.title,
        subject=reply.subject,
        body=note_body_from_reply(reply),
        source=source,
    )


async def save_check(db: Database, user_id: int, check: dict) -> int:
    title = (check.get("skill") or check.get("problem") or "Разбор решения").strip()
    return await db.insert_note(
        user_id=user_id,
        title=title[:80],
        subject=check.get("subject") or "Школьный предмет",
        body=note_body_from_check(check),
        source="photo" if check.get("student_solution") else "chat",
    )


async def save_manual(db: Database, user_id: int, text: str) -> int:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0][:80] if lines else "Свой конспект"
    return await db.insert_note(
        user_id=user_id,
        title=title,
        subject="Свой конспект",
        body=text.strip(),
        source="manual",
    )


async def register_check(db: Database, user_id: int, result: CheckResult) -> int | None:
    if not result.is_schoolwork:
        return None
    check_id = await db.insert_check(
        user_id,
        {
            "subject": result.subject,
            "problem": result.problem_text,
            "student_solution": result.student_solution_text,
            "is_correct": result.is_correct,
            "error_step": result.error_step,
            "hint": result.hint,
            "full_explanation": result.full_explanation,
            "skill": result.skill,
            "praise": result.praise,
        },
    )
    if not result.is_correct:
        await db.insert_error(
            user_id=user_id,
            subject=result.subject,
            problem=result.problem_text or result.student_solution_text or "Учебное решение",
            mistake=result.skill or result.error_step or "ошибка в решении",
            explanation=result.hint or result.error_step or "Нужно разобрать этот шаг ещё раз.",
        )
    return check_id


def _hit_from_fts(row: dict) -> NoteHit:
    raw = row["raw_snippet"]
    return NoteHit(
        id=row["id"],
        title=row["title"],
        subject=row["subject"],
        snippet=strip_markers(raw),
        snippet_html=markers_to_html(raw),
        semantic=False,
    )


async def search_notes(db: Database, tutor: Tutor, user_id: int, query: str) -> list[NoteHit]:
    cleaned = query.strip()
    if not cleaned:
        return []
    match = fts_match(cleaned)
    if match:
        try:
            rows = await db.search_fts(user_id, match)
        except Exception:
            log.exception("full-text search failed")
            rows = []
        if rows:
            return [_hit_from_fts(row) for row in rows]
    if not tutor.enabled:
        return []
    cards = await db.note_cards(user_id)
    if not cards:
        return []
    ids = await tutor.pick_notes(cleaned, cards)
    hits: list[NoteHit] = []
    for note_id in ids:
        note = await db.get_note(user_id, note_id)
        if note is None:
            continue
        preview = note["body"][:240]
        hits.append(
            NoteHit(
                id=note["id"],
                title=note["title"],
                subject=note["subject"],
                snippet=preview,
                snippet_html=markers_to_html(preview),
                semantic=True,
            )
        )
    return hits
