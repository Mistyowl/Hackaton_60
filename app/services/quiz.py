import json
from dataclasses import dataclass

from app.db import Database
from app.services.skills import dim_skills, record
from app.services.tutor import Tutor, TutorError
from app.textutil import format_elapsed


@dataclass
class AnswerView:
    correct: bool
    already_closed: bool
    closed_quiz: bool
    missing: bool
    explanation: str
    finished: bool
    done: int
    total: int
    elapsed: str
    quiz_title: str
    next_question: dict | None
    star: str = ""
    skill_id: int | None = None


def question_options(question: dict) -> list[str]:
    options = json.loads(question["options_json"])
    return [str(option) for option in options]


async def start_topic_quiz(db: Database, tutor: Tutor, user_id: int, topic: str) -> int:
    title, drafts = await tutor.make_quiz(topic=topic, errors=None, fallback=topic)
    return await _save_quiz(db, user_id, title, "topic", drafts)


async def start_note_quiz(db: Database, tutor: Tutor, user_id: int, note: dict) -> int:
    body = (note.get("body") or "")[:4000]
    title = (note.get("title") or "Конспект")[:80]
    prompt = f"{title}\nКонспект:\n{body}" if body else title
    quiz_title, drafts = await tutor.make_quiz(topic=prompt, errors=None, fallback=title)
    return await _save_quiz(db, user_id, quiz_title, "note", drafts)


async def start_review_quiz(
    db: Database,
    tutor: Tutor,
    user_id: int,
    skill_id: int | None = None,
) -> tuple[int, list[int]] | None:
    skills = await db.list_skills(user_id)
    if skill_id is not None:
        chosen = [skill for skill in skills if int(skill["id"]) == skill_id]
    else:
        chosen = dim_skills(skills, limit=3)
    if not chosen:
        return None
    payload = [
        {
            "subject": skill["subject"],
            "problem": skill["name"],
            "mistake": skill["name"],
            "explanation": "Повтори этот приём на новой задаче.",
        }
        for skill in chosen
    ]
    title, drafts = await tutor.make_quiz(
        topic=None,
        errors=payload,
        fallback="Повторение",
        wanted=4,
    )
    quiz_id = await _save_quiz(db, user_id, title or "Повторение", "review", drafts)
    return quiz_id, [int(skill["id"]) for skill in chosen]


async def _save_quiz(db: Database, user_id: int, title: str, source: str, drafts) -> int:
    return await db.create_quiz(
        user_id,
        title,
        source,
        [draft.__dict__ for draft in drafts],
    )


async def start_error_quiz(db: Database, tutor: Tutor, user_id: int) -> int | None:
    errors = await db.list_errors(user_id, limit=8)
    if not errors:
        return None
    payload = [
        {
            "subject": item["subject"],
            "problem": item["problem"],
            "mistake": item["mistake"],
            "explanation": item["explanation"],
        }
        for item in errors
    ]
    title, drafts = await tutor.make_quiz(topic=None, errors=payload)
    return await db.create_quiz(
        user_id,
        title or "По моим ошибкам",
        "errors",
        [draft.__dict__ for draft in drafts],
    )


async def answer_question(
    db: Database,
    user_id: int,
    question_id: int,
    option_index: int,
    tutor: Tutor | None = None,
) -> AnswerView:
    question = await db.get_question(question_id)
    if question is None:
        return _missing()
    quiz = await db.get_quiz(question["quiz_id"])
    if quiz is None or quiz["user_id"] != user_id:
        return _missing()
    elapsed = format_elapsed(quiz["started_at"])
    if quiz["status"] != "active" or question["status"] != "pending":
        return AnswerView(
            correct=False,
            already_closed=True,
            closed_quiz=quiz["status"] != "active",
            missing=False,
            explanation="",
            finished=quiz["status"] == "done",
            done=0,
            total=0,
            elapsed=elapsed,
            quiz_title=quiz["title"],
            next_question=None,
        )
    correct = option_index == int(question["correct_index"])
    if correct:
        await db.mark_question_done(question_id)
    else:
        await db.requeue_question(question_id)
    kind = "review" if quiz.get("source") == "review" else "quiz"
    change = await record(
        db,
        user_id,
        question.get("subject") or "",
        question.get("skill") or "",
        correct,
        kind,
        tutor,
    )
    done, total = await db.quiz_counts(quiz["id"])
    nxt = await db.next_pending(quiz["id"])
    finished = nxt is None
    if finished:
        await db.finish_quiz(quiz["id"], user_id)
    return AnswerView(
        correct=correct,
        already_closed=False,
        closed_quiz=False,
        missing=False,
        explanation=question["explanation"],
        finished=finished,
        done=done,
        total=total,
        elapsed=elapsed,
        quiz_title=quiz["title"],
        next_question=nxt,
        star=change.sentence if change else "",
        skill_id=change.skill_id if change else None,
    )


def _missing() -> AnswerView:
    return AnswerView(
        correct=False,
        already_closed=False,
        closed_quiz=False,
        missing=True,
        explanation="",
        finished=False,
        done=0,
        total=0,
        elapsed="",
        quiz_title="",
        next_question=None,
    )


def ensure_topic(topic: str) -> str:
    cleaned = topic.strip()
    if len(cleaned) < 2:
        raise TutorError("Напишите тему квиза, например: квадратные уравнения.")
    return cleaned
