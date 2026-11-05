import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.bot.render import EMPTY_ERRORS
from app.config import ACCESS_CLOSED, user_allowed
from app.db import CABINET_SESSION_MINUTES, WEEK_GOAL, Database
from app.services.limits import LIMIT_MESSAGE, allow
from app.services.notes import SearchLimited, save_manual, search_notes
from app.services.reminders import moscow_now
from app.services.quiz import (
    answer_question,
    ensure_topic,
    question_options,
    start_error_quiz,
    start_note_quiz,
    start_topic_quiz,
)
from app.services.tutor import Tutor, TutorError
from app.textutil import normalize_subject
from app.webapp import validate_init_data

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
PAGE_PATH = ROOT / "cabinet.html"
STATIC = ROOT / "static"
LUMI_MARK = ROOT / "assets" / "lumi-mark.png"
COOKIE = "cabinet"
NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
STATIC_FILES = {
    "cabinet.css": "text/css",
    "cabinet.js": "text/javascript",
    "lumi-happy.png": "image/png",
    "lumi-think.png": "image/png",
    "icon-stars.png": "image/png",
    "icon-errors.png": "image/png",
    "icon-fire.png": "image/png",
    "icon-trophy.png": "image/png",
    "subject-algebra.png": "image/png",
    "subject-geometry.svg": "image/svg+xml",
    "subject-physics.svg": "image/svg+xml",
    "subject-russian.png": "image/png",
    "subject-chemistry.png": "image/png",
    "subject-english.png": "image/png",
}
_HASHED_ASSET = re.compile(r"^cabinet\.([0-9a-f]{10})\.(css|js)$")


def asset_hash(filename: str) -> str:
    return hashlib.sha256((STATIC / filename).read_bytes()).hexdigest()[:10]


def hashed_asset(filename: str) -> str:
    stem, ext = filename.split(".", 1)
    return f"{stem}.{asset_hash(filename)}.{ext}"


def build_version() -> str:
    parts = [
        asset_hash("cabinet.css"),
        asset_hash("cabinet.js"),
        hashlib.sha256(PAGE_PATH.read_bytes()).hexdigest()[:10],
    ]
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:12]


def cabinet_html() -> str:
    page = PAGE_PATH.read_text(encoding="utf-8")
    page = page.replace(
        "<head>",
        f'<head>\n  <meta name="lumi-build" content="{build_version()}">',
        1,
    )
    return page.replace(
        'href="/static/cabinet.css"',
        f'href="/static/{hashed_asset("cabinet.css")}"',
    ).replace(
        'src="/static/cabinet.js"',
        f'src="/static/{hashed_asset("cabinet.js")}"',
    )


def resolve_static(name: str) -> tuple[Path, str] | None:
    media = STATIC_FILES.get(name)
    if media is not None:
        return STATIC / name, media
    match = _HASHED_ASSET.fullmatch(name)
    if match is None:
        return None
    filename = f"cabinet.{match.group(2)}"
    return STATIC / filename, STATIC_FILES[filename]


def create_app(
    db: Database,
    tutor: Tutor,
    bot_token: str = "",
    allowed_user_ids: set[int] | None = None,
    week_goal: int = WEEK_GOAL,
    max_webhook=None,
    max_webhook_path: str = "/max-webhook",
) -> FastAPI:
    app = FastAPI(title="Кабинет репетитора")
    allowed = allowed_user_ids or set()
    if max_webhook is not None:
        max_webhook.setup(app, path=max_webhook_path)

    async def student(request: Request) -> dict:
        user = await db.cabinet_user(request.cookies.get(COOKIE, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="Открой кабинет из чата с ботом")
        if not user_allowed(int(user["user_id"]), allowed):
            log.info("Отказ в доступе user_id=%s", user["user_id"])
            raise HTTPException(status_code=403, detail=ACCESS_CLOSED)
        return user

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(cabinet_html(), headers=NO_STORE)

    @app.get("/api/version")
    async def version() -> JSONResponse:
        return JSONResponse({"version": build_version()}, headers=NO_STORE)

    @app.get("/assets/lumi-mark.png")
    async def lumi_mark() -> FileResponse:
        return FileResponse(LUMI_MARK, media_type="image/png", headers=NO_STORE)

    @app.get("/static/{name}")
    async def static_file(name: str) -> FileResponse:
        resolved = resolve_static(name)
        if resolved is None:
            raise HTTPException(status_code=404, detail="Файл не найден")
        path, media = resolved
        return FileResponse(path, media_type=media, headers=NO_STORE)

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
            raise HTTPException(status_code=401, detail="Открой кабинет кнопкой в чате MAX")
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
        stats = await db.profile_stats(int(user["user_id"]))
        return _profile_payload(user, stats)

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
            raise HTTPException(status_code=400, detail="Напиши имя.")
        name = " ".join(raw_name.split())
        if not name or len(name) > 128:
            raise HTTPException(status_code=400, detail="Напиши имя.")
        if isinstance(grade, bool) or not isinstance(grade, int) or not 5 <= grade <= 11:
            raise HTTPException(status_code=400, detail="Выбери класс от 5 до 11.")
        style = user["explain_style"]
        if "explain_style" in body:
            style = body.get("explain_style")
            if style not in {"short", "detail"}:
                raise HTTPException(status_code=400, detail="Выбери, как объяснять.")
            await db.set_explain_style(int(user["user_id"]), style)
        goal = user.get("exam_goal")
        if "exam_goal" in body:
            goal = body.get("exam_goal")
            if goal not in {"oge", "ege", "self"}:
                raise HTTPException(status_code=400, detail="Выбери цель.")
        elif not goal:
            goal = _default_goal(grade)
        reminders = bool(user.get("reminders", 1))
        if "reminders" in body:
            if not isinstance(body.get("reminders"), bool):
                raise HTTPException(status_code=400, detail="Укажи, присылать ли напоминания.")
            reminders = body["reminders"]
            await db.set_reminders(int(user["user_id"]), reminders)
        await db.set_profile(int(user["user_id"]), name, grade, exam_goal=goal)
        stats = await db.profile_stats(int(user["user_id"]))
        return _profile_payload(
            {
                **user,
                "first_name": name,
                "grade": grade,
                "explain_style": style,
                "exam_goal": goal,
                "reminders": reminders,
            },
            stats,
        )

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
                    "source": row["source"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        }

    @app.get("/api/home")
    async def home(request: Request) -> dict:
        user = await student(request)
        return await db.home_stats(int(user["user_id"]), moscow_now().date(), week_goal)

    @app.post("/api/notes")
    async def create_note(request: Request) -> dict:
        user = await student(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Напиши текст конспекта.") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Напиши текст конспекта.")
        text = body.get("text")
        title = body.get("title", "")
        subject = body.get("subject", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise HTTPException(status_code=400, detail="Напиши текст конспекта.")
        if not isinstance(title, str) or len(title) > 120:
            raise HTTPException(status_code=400, detail="Заголовок слишком длинный.")
        if not isinstance(subject, str) or len(subject) > 80:
            raise HTTPException(status_code=400, detail="Предмет слишком длинный.")
        note_id = await save_manual(db, int(user["user_id"]), text, title=title, subject=subject)
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return _note_payload(note)

    @app.get("/api/notes/{note_id}")
    async def note_detail(note_id: int, request: Request) -> dict:
        user = await student(request)
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return _note_payload(note)

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
            raise HTTPException(status_code=400, detail="Укажи, закреплять ли конспект.")
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
            raise HTTPException(status_code=400, detail="Напиши заголовок и текст конспекта.")
        title = " ".join(raw_title.split())
        text = raw_body.strip()
        if not title or len(title) > 120:
            raise HTTPException(status_code=400, detail="Напиши заголовок.")
        if len(raw_subject) > 80:
            raise HTTPException(status_code=400, detail="Предмет слишком длинный.")
        subject = normalize_subject(raw_subject)
        if not text or len(text) > 20000:
            raise HTTPException(status_code=400, detail="Напиши текст конспекта.")
        saved = await db.update_note(int(user["user_id"]), note_id, title, subject, text)
        if not saved:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return _note_payload(note)

    @app.get("/api/search")
    async def search(request: Request, q: str = "") -> list[dict]:
        user = await student(request)
        if not q.strip():
            return []
        user_id = int(user["user_id"])
        try:
            hits = await search_notes(db, tutor, user_id, q, spend=lambda: allow(user_id))
        except SearchLimited as error:
            raise HTTPException(status_code=429, detail=error.user_message) from None
        except TutorError as error:
            raise HTTPException(status_code=502, detail=error.user_message) from None
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
    async def errors(request: Request) -> dict:
        user = await student(request)
        user_id = int(user["user_id"])
        rows = await db.list_error_bank(user_id, limit=40)
        items: list[dict] = []
        counts = {"new": 0, "active": 0, "closed": 0}
        for row in rows:
            progress = int(row["progress"] or 0)
            if row["resolved_at"]:
                status = "closed"
                progress = 3
            elif progress <= 0:
                status = "new"
            else:
                status = "active"
            counts[status] += 1
            items.append(
                {
                    "id": row["id"],
                    "subject": row["subject"],
                    "mistake": row["mistake"],
                    "problem": _short(row["problem"]),
                    "skill": row["skill_name"],
                    "status": status,
                    "progress": progress,
                    "source": row.get("source") or "check",
                }
            )
        return {"counts": counts, "items": items}

    @app.post("/api/quizzes/topic")
    async def quiz_topic(request: Request) -> dict:
        user = await student(request)
        body = await _json_body(request)
        raw = body.get("topic")
        if not isinstance(raw, str):
            raise HTTPException(status_code=400, detail="Напиши тему квиза.")
        try:
            topic = ensure_topic(raw)
        except TutorError as error:
            raise HTTPException(status_code=400, detail=error.user_message) from None
        _spend(int(user["user_id"]))
        try:
            quiz_id = await start_topic_quiz(db, tutor, int(user["user_id"]), topic, _wanted(body))
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
        answered = await db.get_question(question_id)
        correct_index = int(answered["correct_index"]) if answered is not None else None
        quiz_id = int(answered["quiz_id"]) if answered is not None else None
        payload = {
            "title": view.quiz_title,
            "done": view.done,
            "total": view.total,
            "asked": await db.quiz_asked(quiz_id) if quiz_id is not None else 0,
            "finished": finished,
            "elapsed": view.elapsed,
            "skill_id": view.skill_id,
            "feedback": {
                "correct": view.correct,
                "explanation": view.explanation,
                "correct_index": correct_index,
            },
            "question": _public_question(view.next_question),
            "misses": [],
            "summary": None,
        }
        if finished and not view.already_closed and quiz_id is not None:
            payload["misses"] = await _misses(db, quiz_id)
            payload["summary"] = await _quiz_summary(db, quiz_id)
        return payload

    @app.post("/api/quizzes/note/{note_id}")
    async def quiz_note(note_id: int, request: Request) -> dict:
        user = await student(request)
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _spend(int(user["user_id"]))
        try:
            quiz_id = await start_note_quiz(db, tutor, int(user["user_id"]), note, _wanted(payload))
        except TutorError as error:
            raise HTTPException(status_code=502, detail=error.user_message) from None
        return await _quiz_payload(db, quiz_id)

    return app


def _spend(user_id: int) -> None:
    if not allow(user_id):
        raise HTTPException(status_code=429, detail=LIMIT_MESSAGE)


async def _misses(db: Database, quiz_id: int) -> list[dict]:
    rows = await db.quiz_questions(quiz_id)
    return [
        {"prompt": row["prompt"], "explanation": row["explanation"]}
        for row in rows
        if int(row["attempts"] or 0) > 1
    ]


def _topic_label(row: dict) -> str:
    label = (row.get("skill") or row.get("subject") or "").strip()
    if label:
        return label
    return _short(row.get("prompt") or "", 40)


async def _quiz_summary(db: Database, quiz_id: int) -> dict:
    rows = await db.quiz_questions(quiz_id)
    total = len(rows)
    correct = 0
    strong: list[str] = []
    review: list[str] = []
    seen_strong: set[str] = set()
    seen_review: set[str] = set()
    for row in rows:
        attempts = int(row["attempts"] or 0)
        label = _topic_label(row)
        if attempts == 1:
            correct += 1
            if label and label not in seen_strong:
                seen_strong.add(label)
                strong.append(label)
        elif attempts > 1:
            if label and label not in seen_review:
                seen_review.add(label)
                review.append(label)
    if review:
        advice = (
            f"Рекомендую перечитать тему «{review[0]}» "
            "и пройти квиз по ошибкам через день."
        )
    else:
        advice = "Отлично! Все темы закрыты с первой попытки. Можно взять новую тему."
    return {
        "correct": correct,
        "total": total,
        "strong": strong,
        "review": review,
        "advice": advice,
    }


def _default_goal(grade: int) -> str:
    if grade == 9:
        return "oge"
    if grade in (10, 11):
        return "ege"
    return "self"


def _profile_payload(user: dict, stats: dict | None = None) -> dict:
    payload = {
        "first_name": user["first_name"],
        "grade": user["grade"],
        "explain_style": user["explain_style"],
        "exam_goal": user.get("exam_goal") or None,
        "reminders": bool(user.get("reminders", 1)),
        "quizzes_done": 0,
        "closed_errors": 0,
        "accuracy": 0,
        "streak_days": 0,
    }
    if stats:
        payload["quizzes_done"] = int(stats.get("quizzes_done") or 0)
        payload["closed_errors"] = int(stats.get("closed_errors") or 0)
        payload["accuracy"] = int(stats.get("accuracy") or 0)
        payload["streak_days"] = int(stats.get("streak_days") or 0)
    return payload


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _wanted(body: dict) -> int:
    raw = body.get("count")
    if raw in (5, 10):
        return int(raw)
    return 6


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
        "retry": int(question.get("attempts") or 0) > 0,
    }


async def _quiz_payload(db: Database, quiz_id: int) -> dict:
    quiz = await db.get_quiz(quiz_id)
    question = await db.next_pending(quiz_id)
    done, total = await db.quiz_counts(quiz_id)
    return {
        "title": quiz["title"] if quiz else "",
        "done": done,
        "total": total,
        "asked": await db.quiz_asked(quiz_id),
        "finished": question is None,
        "feedback": None,
        "question": _public_question(question),
    }


def _note_payload(note: dict) -> dict:
    return {
        "id": note["id"],
        "title": note["title"],
        "subject": note["subject"],
        "body": note["body"],
        "source": note["source"],
        "pinned": bool(note.get("pinned")),
        "created_at": note["created_at"],
    }


def _short(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
