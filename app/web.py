import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.bot.render import EMPTY_ERRORS
from app.config import ACCESS_CLOSED, user_allowed
from app.db import CABINET_SESSION_MINUTES, Database
from app.services.limits import allow
from app.services.notes import search_notes
from app.services.quiz import (
    answer_question,
    ensure_topic,
    question_options,
    start_error_quiz,
    start_note_quiz,
    start_review_quiz,
    start_topic_quiz,
)
from app.services.skills import sky_payload
from app.services.tutor import Tutor, TutorError
from app.webapp import validate_init_data

log = logging.getLogger(__name__)
PAGE = (Path(__file__).resolve().parent / "cabinet.html").read_text(encoding="utf-8")
STATIC = Path(__file__).resolve().parent / "static"
LUMI_MARK = Path(__file__).resolve().parent / "assets" / "lumi-mark.png"
COOKIE = "cabinet"


def create_app(
    db: Database,
    tutor: Tutor,
    bot_token: str = "",
    allowed_user_ids: set[int] | None = None,
) -> FastAPI:
    app = FastAPI(title="Кабинет репетитора")
    allowed = allowed_user_ids or set()

    async def student(request: Request) -> dict:
        user = await db.cabinet_user(request.cookies.get(COOKIE, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="Откройте кабинет из чата с ботом")
        if not user_allowed(int(user["user_id"]), allowed):
            log.info("Отказ в доступе user_id=%s", user["user_id"])
            raise HTTPException(status_code=403, detail=ACCESS_CLOSED)
        return user

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return PAGE

    @app.get("/assets/lumi-mark.png")
    async def lumi_mark() -> FileResponse:
        return FileResponse(LUMI_MARK, media_type="image/png")

    @app.get("/static/{name}")
    async def static_file(name: str) -> FileResponse:
        allowed_files = {
            "cabinet.css": "text/css",
            "cabinet.js": "text/javascript",
            "lumi-happy.svg": "image/svg+xml",
            "lumi-think.svg": "image/svg+xml",
            "lumi-support.svg": "image/svg+xml",
        }
        media = allowed_files.get(name)
        if media is None:
            raise HTTPException(status_code=404, detail="Файл не найден")
        return FileResponse(STATIC / name, media_type=media)

    @app.post("/api/session")
    async def open_session(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Нужны данные мини-приложения") from None
        init_data = body.get("init_data") if isinstance(body, dict) else None
        if not isinstance(init_data, str):
            raise HTTPException(status_code=400, detail="Нужны данные мини-приложения")
        user = validate_init_data(init_data, bot_token, _now())
        if user is None:
            raise HTTPException(status_code=401, detail="Откройте кабинет кнопкой в чате MAX")
        if not user_allowed(user.user_id, allowed):
            log.info("Отказ в доступе user_id=%s", user.user_id)
            raise HTTPException(status_code=403, detail=ACCESS_CLOSED)
        await db.upsert_user(user.user_id, user.first_name)
        session = await db.create_cabinet_session(user.user_id)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE,
            session,
            max_age=CABINET_SESSION_MINUTES * 60,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/profile")
    async def profile(request: Request) -> dict:
        user = await student(request)
        return _profile_payload(user)

    @app.patch("/api/profile")
    async def update_profile(request: Request) -> dict:
        user = await student(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Нужны данные профиля.") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Нужны данные профиля.")
        raw_name = body.get("first_name")
        grade = body.get("grade")
        if not isinstance(raw_name, str):
            raise HTTPException(status_code=400, detail="Напишите имя.")
        name = " ".join(raw_name.split())
        if not name or len(name) > 128:
            raise HTTPException(status_code=400, detail="Напишите имя.")
        if isinstance(grade, bool) or not isinstance(grade, int) or not 5 <= grade <= 11:
            raise HTTPException(status_code=400, detail="Выберите класс от 5 до 11.")
        style = user["explain_style"]
        if "explain_style" in body:
            style = body.get("explain_style")
            if style not in {"short", "detail"}:
                raise HTTPException(status_code=400, detail="Выберите, как объяснять.")
            await db.set_explain_style(int(user["user_id"]), style)
        if "reminders" in body:
            reminders = body.get("reminders")
            if not isinstance(reminders, bool):
                raise HTTPException(status_code=400, detail="Укажите, напоминать ли о звёздах.")
            await db.set_reminders(int(user["user_id"]), reminders)
            user = {**user, "reminders": 1 if reminders else 0}
        await db.set_profile(int(user["user_id"]), name, grade)
        return _profile_payload({**user, "first_name": name, "grade": grade, "explain_style": style})

    @app.get("/api/notes")
    async def notes(request: Request) -> dict:
        user = await student(request)
        user_id = int(user["user_id"])
        total = await db.count_notes(user_id)
        rows = await db.list_notes(user_id, 0, 100)
        return {
            "total": total,
            "notes": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "subject": row["subject"],
                    "preview": (row["body"] or "")[:280],
                    "pinned": bool(row["pinned"]),
                }
                for row in rows
            ],
        }

    @app.get("/api/notes/{note_id}")
    async def note_detail(note_id: int, request: Request) -> dict:
        user = await student(request)
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return {
            "id": note["id"],
            "title": note["title"],
            "subject": note["subject"],
            "body": note["body"],
        }

    @app.delete("/api/notes/{note_id}")
    async def delete_note(note_id: int, request: Request) -> dict:
        user = await student(request)
        deleted = await db.delete_note(int(user["user_id"]), note_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return {"deleted": True}

    @app.patch("/api/notes/{note_id}")
    async def pin_note(note_id: int, request: Request) -> dict:
        user = await student(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Нужны данные конспекта.") from None
        if not isinstance(body, dict) or not isinstance(body.get("pinned"), bool):
            raise HTTPException(status_code=400, detail="Укажите, закреплять ли конспект.")
        pinned = body["pinned"]
        updated = await db.set_note_pinned(int(user["user_id"]), note_id, pinned)
        if not updated:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return {"id": note_id, "pinned": pinned}

    @app.put("/api/notes/{note_id}")
    async def update_note(note_id: int, request: Request) -> dict:
        user = await student(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Нужны данные конспекта.") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Нужны данные конспекта.")
        raw_title = payload.get("title")
        raw_subject = payload.get("subject")
        raw_body = payload.get("body")
        if (
            not isinstance(raw_title, str)
            or not isinstance(raw_subject, str)
            or not isinstance(raw_body, str)
        ):
            raise HTTPException(status_code=400, detail="Напишите заголовок и текст конспекта.")
        title = " ".join(raw_title.split())
        subject = " ".join(raw_subject.split())
        text = raw_body.strip()
        if not title or len(title) > 120:
            raise HTTPException(status_code=400, detail="Напишите заголовок.")
        if len(subject) > 80:
            raise HTTPException(status_code=400, detail="Предмет слишком длинный.")
        if not text or len(text) > 20000:
            raise HTTPException(status_code=400, detail="Напишите текст конспекта.")
        saved = await db.update_note(int(user["user_id"]), note_id, title, subject, text)
        if not saved:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return {
            "id": note["id"],
            "title": note["title"],
            "subject": note["subject"],
            "body": note["body"],
        }

    @app.get("/api/search")
    async def search(request: Request, q: str = "") -> list[dict]:
        user = await student(request)
        if not q.strip():
            return []
        hits = await search_notes(db, tutor, int(user["user_id"]), q)
        return [
            {
                "id": hit.id,
                "title": hit.title,
                "subject": hit.subject,
                "snippet": hit.snippet,
                "semantic": hit.semantic,
            }
            for hit in hits
        ]

    @app.get("/api/errors")
    async def errors(request: Request) -> list[dict]:
        user = await student(request)
        rows = await db.list_errors(int(user["user_id"]), limit=20)
        return [
            {
                "subject": row["subject"],
                "mistake": row["mistake"],
                "problem": _short(row["problem"]),
            }
            for row in rows
        ]

    @app.post("/api/quizzes/topic")
    async def quiz_topic(request: Request) -> dict:
        user = await student(request)
        body = await _json_body(request)
        raw = body.get("topic")
        if not isinstance(raw, str):
            raise HTTPException(status_code=400, detail="Напишите тему квиза.")
        try:
            topic = ensure_topic(raw)
        except TutorError as error:
            raise HTTPException(status_code=400, detail=error.user_message) from None
        _spend(int(user["user_id"]))
        try:
            quiz_id = await start_topic_quiz(db, tutor, int(user["user_id"]), topic)
        except TutorError as error:
            raise HTTPException(status_code=502, detail=error.user_message) from None
        return await _quiz_payload(db, quiz_id)

    @app.post("/api/quizzes/errors")
    async def quiz_errors(request: Request) -> dict:
        user = await student(request)
        _spend(int(user["user_id"]))
        try:
            quiz_id = await start_error_quiz(db, tutor, int(user["user_id"]))
        except TutorError as error:
            raise HTTPException(status_code=502, detail=error.user_message) from None
        if quiz_id is None:
            raise HTTPException(status_code=409, detail=EMPTY_ERRORS)
        return await _quiz_payload(db, quiz_id)

    @app.post("/api/quizzes/answer")
    async def quiz_answer(request: Request) -> dict:
        user = await student(request)
        body = await _json_body(request)
        question_id = body.get("question_id")
        option = body.get("option")
        if isinstance(question_id, bool) or isinstance(option, bool):
            raise HTTPException(status_code=400, detail="Нужен номер вопроса и вариант.")
        if not isinstance(question_id, int) or not isinstance(option, int):
            raise HTTPException(status_code=400, detail="Нужен номер вопроса и вариант.")
        view = await answer_question(db, int(user["user_id"]), question_id, option, tutor)
        if view.missing:
            raise HTTPException(status_code=404, detail="Этот вопрос уже недоступен.")
        if view.already_closed:
            detail = "Этот квиз уже закрыт." if view.closed_quiz else "Эта задача уже закрыта."
            raise HTTPException(status_code=409, detail=detail)
        finished = view.finished or view.next_question is None
        payload = {
            "title": view.quiz_title,
            "done": view.done,
            "total": view.total,
            "finished": finished,
            "elapsed": view.elapsed,
            "star": view.star,
            "skill_id": view.skill_id,
            "feedback": {"correct": view.correct, "explanation": view.explanation},
            "question": _public_question(view.next_question),
            "misses": [],
        }
        if finished and not view.already_closed:
            question = await db.get_question(question_id)
            if question is not None:
                payload["misses"] = await _misses(db, int(question["quiz_id"]))
        return payload

    @app.post("/api/quizzes/note/{note_id}")
    async def quiz_note(note_id: int, request: Request) -> dict:
        user = await student(request)
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        _spend(int(user["user_id"]))
        try:
            quiz_id = await start_note_quiz(db, tutor, int(user["user_id"]), note)
        except TutorError as error:
            raise HTTPException(status_code=502, detail=error.user_message) from None
        return await _quiz_payload(db, quiz_id)

    @app.post("/api/quizzes/review")
    async def quiz_review(request: Request) -> dict:
        user = await student(request)
        body = await _json_body(request)
        raw_skill = body.get("skill_id")
        skill_id = None
        if raw_skill is not None:
            if isinstance(raw_skill, bool) or not isinstance(raw_skill, int):
                raise HTTPException(status_code=400, detail="Нужен номер звезды.")
            skill_id = raw_skill
        _spend(int(user["user_id"]))
        try:
            started = await start_review_quiz(db, tutor, int(user["user_id"]), skill_id)
        except TutorError as error:
            raise HTTPException(status_code=502, detail=error.user_message) from None
        if started is None:
            raise HTTPException(status_code=409, detail="Тусклых звёзд пока нет.")
        quiz_id, skill_ids = started
        payload = await _quiz_payload(db, quiz_id)
        payload["skill_ids"] = skill_ids
        return payload

    @app.get("/api/sky")
    async def sky(request: Request) -> dict:
        user = await student(request)
        skills = await db.list_skills(int(user["user_id"]))
        return sky_payload(skills)

    @app.get("/api/skills/{skill_id}")
    async def skill_detail(skill_id: int, request: Request) -> dict:
        user = await student(request)
        user_id = int(user["user_id"])
        skill = await db.get_skill(user_id, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Звезда не найдена")
        payload = sky_payload([skill])
        star = payload["constellations"][0]["stars"][0]
        events = await db.list_skill_events(skill_id)
        notes = await db.notes_for_skill(user_id, skill_id)
        mistakes = await db.errors_for_skill(user_id, skill_id)
        return {
            **star,
            "subject": skill["subject"],
            "events": [
                {"kind": item["kind"], "correct": bool(item["correct"]), "created_at": item["created_at"]}
                for item in events
            ],
            "notes": [{"id": item["id"], "title": item["title"]} for item in notes],
            "errors": [
                {"mistake": item["mistake"], "problem": _short(item["problem"]), "open": item["resolved_at"] is None}
                for item in mistakes
            ],
        }

    return app


def _spend(user_id: int) -> None:
    if not allow(user_id):
        raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте через час.")


async def _misses(db: Database, quiz_id: int) -> list[dict]:
    rows = await db.quiz_questions(quiz_id)
    return [
        {"prompt": row["prompt"], "explanation": row["explanation"]}
        for row in rows
        if int(row["attempts"] or 0) > 1
    ]


def _profile_payload(user: dict) -> dict:
    return {
        "first_name": user["first_name"],
        "grade": user["grade"],
        "explain_style": user["explain_style"],
        "reminders": bool(user.get("reminders", 1)),
    }


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Нужны данные квиза.") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Нужны данные квиза.")
    return body


def _public_question(question: dict | None) -> dict | None:
    if question is None:
        return None
    return {
        "id": question["id"],
        "prompt": question["prompt"],
        "options": question_options(question),
    }


async def _quiz_payload(db: Database, quiz_id: int) -> dict:
    quiz = await db.get_quiz(quiz_id)
    question = await db.next_pending(quiz_id)
    done, total = await db.quiz_counts(quiz_id)
    return {
        "title": quiz["title"] if quiz else "",
        "done": done,
        "total": total,
        "finished": question is None,
        "feedback": None,
        "question": _public_question(question),
    }


def _short(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
