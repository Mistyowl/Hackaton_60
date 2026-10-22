from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.db import Database
from app.services.notes import search_notes
from app.services.tutor import Tutor

PAGE = (Path(__file__).resolve().parent / "cabinet.html").read_text(encoding="utf-8")


def create_app(db: Database, tutor: Tutor) -> FastAPI:
    app = FastAPI(title="Кабинет репетитора")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return PAGE

    @app.get("/api/users")
    async def users() -> list[dict]:
        return await db.list_users()

    @app.get("/api/notes")
    async def notes(user_id: int) -> dict:
        total = await db.count_notes(user_id)
        rows = await db.list_notes(user_id, 0, 100)
        return {"total": total, "notes": rows}

    @app.get("/api/notes/{note_id}")
    async def note_detail(note_id: int, user_id: int) -> dict:
        note = await db.get_note(user_id, note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Конспект не найден")
        return note

    @app.get("/api/search")
    async def search(user_id: int, q: str = "") -> list[dict]:
        hits = await search_notes(db, tutor, user_id, q)
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
    async def errors(user_id: int) -> list[dict]:
        return await db.list_errors(user_id, limit=20)

    return app
