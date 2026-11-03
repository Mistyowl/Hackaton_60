import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.bot.render import EMPTY_ERRORS
from app.config import ACCESS_CLOSED, user_allowed
from app.db import CABINET_SESSION_MINUTES, Database
from app.services.limits import allow
from app.services.notes import save_manual, search_notes
from app.services.reminders import moscow_now
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
    "lumi-happy.svg": "image/svg+xml",
    "lumi-think.svg": "image/svg+xml",
    "lumi-support.svg": "image/svg+xml",
    "icon-stars.png": "image/png",
    "icon-errors.png": "image/png",
    "icon-fire.png": "image/png",
    "icon-trophy.png": "image/png",
    "subject-algebra.png": "image/png",
    "subject-physics.png": "image/png",
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
        goal = user.get("exam_goal")
        if "exam_goal" in body:
            goal = body.get("exam_goal")
            if goal not in {"oge", "ege", "self"}:
                raise HTTPException(status_code=400, detail="Выберите цель.")
        elif not goal:
            goal = _default_goal(grade)
        if "reminders" in body:
            reminders = body.get("reminders")
            if not isinstance(reminders, bool):
                raise HTTPException(status_code=400, detail="Укажите, напоминать ли о звёздах.")
            await db.set_reminders(int(user["user_id"]), reminders)
            user = {**user, "reminders": 1 if reminders else 0}
        await db.set_profile(int(user["user_id"]), name, grade, exam_goal=goal)
        stats = await db.profile_stats(int(user["user_id"]))
        return _profile_payload(
            {**user, "first_name": name, "grade": grade, "explain_style": style, "exam_goal": goal},
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
        return await db.home_stats(int(user["user_id"]), moscow_now().date())

    @app.post("/api/notes")
    async def create_note(request: Request) -> dict:
        user = await student(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Напиши текст конспекта.") from None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="Напиши текст конспекта.")
        note_id = await save_manual(db, int(user["user_id"]), text)
        note = await db.get_note(int(user["user_id"]), note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return {
            "id": note["id"],
            "title": note["title"],
            "subject": note["subject"],
            "body": note["body"],
            "source": note["source"],
            "created_at": note["created_at"],
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
    async def errors(request: Request) -> dict:
        user = await student(request)
        user_id = int(user["user_id"])
        rows = await db.list_error_bank(user_id, limit=40)
        items: list[dict] = []
        counts = {"new": 0, "active": 0, "closed": 0}
        for row in rows:
            progress = 0
            skill_name = ""
            skill_id = row["skill_id"]
            if skill_id:
                skill = await db.get_skill(user_id, int(skill_id))
                if skill:
                    skill_name = skill["name"] or ""
                events = await db.list_skill_events(int(skill_id), limit=12)
                progress = _streak_progress(events)
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
                    "skill": skill_name,
                    "status": status,
                    "progress": progress,
                }
            )
        return {"counts": counts, "items": items}

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
        payload = {
            "title": view.quiz_title,
            "done": view.done,
            "total": view.total,
            "finished": finished,
            "elapsed": view.elapsed,
            "star": view.star,
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
        if finished and not view.already_closed and answered is not None:
            quiz_id = int(answered["quiz_id"])
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


def _streak_progress(events: list[dict]) -> int:
    streak = 0
    for event in reversed(events):
        if int(event.get("correct") or 0):
            streak += 1
        else:
            break
    return min(streak, 3)


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
