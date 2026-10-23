from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import CABINET_SESSION_MINUTES, Database
from app.services.notes import search_notes
from app.services.tutor import Tutor

PAGE = (Path(__file__).resolve().parent / "cabinet.html").read_text(encoding="utf-8")
COOKIE = "cabinet"


def create_app(db: Database, tutor: Tutor) -> FastAPI:
    app = FastAPI(title="Кабинет репетитора")

    async def student(request: Request) -> dict:
        user = await db.cabinet_user(request.cookies.get(COOKIE, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="Откройте кабинет из чата с ботом")
        return user

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return PAGE

    @app.get("/k/{token}")
    async def open_link(token: str):
        session = await db.redeem_cabinet_link(token)
        if session is None:
            return HTMLResponse(PAGE)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            COOKIE,
            session,
            max_age=CABINET_SESSION_MINUTES * 60,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/profile")
    async def profile(request: Request) -> dict:
        user = await student(request)
        return {
            "first_name": user["first_name"],
            "grade": user["grade"],
            "explain_style": user["explain_style"],
        }

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

    return app


def _short(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
