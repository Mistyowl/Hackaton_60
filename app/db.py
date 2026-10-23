import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from app.textutil import MARK_START, utcnow

CABINET_LINK_MINUTES = 15
CABINET_SESSION_MINUTES = 45

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    grade INTEGER,
    explain_style TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'chat',
    last_tutor_json TEXT,
    last_check_id INTEGER,
    active_quiz_id INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    problem TEXT NOT NULL,
    mistake TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    problem TEXT NOT NULL DEFAULT '',
    student_solution TEXT NOT NULL DEFAULT '',
    is_correct INTEGER NOT NULL,
    error_step TEXT NOT NULL DEFAULT '',
    hint TEXT NOT NULL DEFAULT '',
    full_explanation TEXT NOT NULL DEFAULT '',
    skill TEXT NOT NULL DEFAULT '',
    praise TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    options_json TEXT NOT NULL,
    correct_index INTEGER NOT NULL,
    explanation TEXT NOT NULL,
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
CREATE INDEX IF NOT EXISTS idx_errors_user ON errors(user_id, id);
CREATE INDEX IF NOT EXISTS idx_questions_quiz ON quiz_questions(quiz_id, status, position);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title,
    body,
    subject,
    content='notes',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, body, subject)
    VALUES (new.id, new.title, new.body, new.subject);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body, subject)
    VALUES ('delete', old.id, old.title, old.body, old.subject);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body, subject)
    VALUES ('delete', old.id, old.title, old.body, old.subject);
    INSERT INTO notes_fts(rowid, title, body, subject)
    VALUES (new.id, new.title, new.body, new.subject);
END;

CREATE TABLE IF NOT EXISTS cabinet_links (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS cabinet_sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._ensure_user_columns()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База ещё не открыта")
        return self._conn

    async def _ensure_user_columns(self) -> None:
        cursor = await self.conn.execute("PRAGMA table_info(users)")
        have = {row["name"] for row in await cursor.fetchall()}
        if "grade" not in have:
            await self.conn.execute("ALTER TABLE users ADD COLUMN grade INTEGER")
        if "explain_style" not in have:
            await self.conn.execute("ALTER TABLE users ADD COLUMN explain_style TEXT")

    async def upsert_user(self, user_id: int, first_name: str) -> None:
        now = utcnow()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, first_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = CASE
                    WHEN excluded.first_name != '' THEN excluded.first_name
                    ELSE users.first_name
                END
            """,
            (user_id, first_name or "", now),
        )
        await self.conn.execute(
            """
            INSERT INTO sessions (user_id, mode, updated_at)
            VALUES (?, 'chat', ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, now),
        )
        await self.conn.commit()

    async def get_user(self, user_id: int) -> dict:
        cursor = await self.conn.execute(
            "SELECT user_id, first_name, grade, explain_style FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            await self.upsert_user(user_id, "")
            cursor = await self.conn.execute(
                "SELECT user_id, first_name, grade, explain_style FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
        return dict(row)

    async def set_grade(self, user_id: int, grade: int) -> None:
        await self.conn.execute(
            "UPDATE users SET grade = ? WHERE user_id = ?",
            (grade, user_id),
        )
        await self.conn.commit()

    async def set_explain_style(self, user_id: int, style: str) -> None:
        await self.conn.execute(
            "UPDATE users SET explain_style = ? WHERE user_id = ?",
            (style, user_id),
        )
        await self.conn.commit()

    async def find_user(self, user_id: int) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT user_id, first_name, grade, explain_style FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_cabinet_link(self, user_id: int) -> str:
        token = secrets.token_urlsafe(9)
        await self.conn.execute(
            """
            INSERT INTO cabinet_links (token, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token, user_id, _until(CABINET_LINK_MINUTES)),
        )
        await self.conn.commit()
        return token

    async def redeem_cabinet_link(self, token: str) -> str | None:
        now = utcnow()
        cursor = await self.conn.execute(
            """
            UPDATE cabinet_links
            SET used_at = ?
            WHERE token = ? AND used_at IS NULL AND expires_at > ?
            """,
            (now, token, now),
        )
        if cursor.rowcount != 1:
            await self.conn.commit()
            return None
        found = await self.conn.execute(
            "SELECT user_id FROM cabinet_links WHERE token = ?",
            (token,),
        )
        row = await found.fetchone()
        if row is None:
            await self.conn.rollback()
            return None
        session = secrets.token_urlsafe(18)
        await self.conn.execute(
            """
            INSERT INTO cabinet_sessions (token, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (session, int(row["user_id"]), _until(CABINET_SESSION_MINUTES)),
        )
        await self.conn.commit()
        return session

    async def cabinet_user(self, token: str) -> dict | None:
        if not token:
            return None
        cursor = await self.conn.execute(
            """
            SELECT users.user_id, users.first_name, users.grade, users.explain_style
            FROM cabinet_sessions
            JOIN users ON users.user_id = cabinet_sessions.user_id
            WHERE cabinet_sessions.token = ? AND cabinet_sessions.expires_at > ?
            """,
            (token, utcnow()),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_users(self) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT user_id, first_name, created_at FROM users ORDER BY created_at, user_id"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_session(self, user_id: int) -> dict:
        cursor = await self.conn.execute(
            "SELECT * FROM sessions WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            await self.upsert_user(user_id, "")
            cursor = await self.conn.execute(
                "SELECT * FROM sessions WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
        return dict(row)

    async def set_mode(self, user_id: int, mode: str) -> None:
        await self.conn.execute(
            "UPDATE sessions SET mode = ?, updated_at = ? WHERE user_id = ?",
            (mode, utcnow(), user_id),
        )
        await self.conn.commit()

    async def set_last_tutor(self, user_id: int, payload: dict) -> None:
        await self.conn.execute(
            """
            UPDATE sessions
            SET last_tutor_json = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), utcnow(), user_id),
        )
        await self.conn.commit()

    async def get_last_tutor(self, user_id: int) -> dict | None:
        session = await self.get_session(user_id)
        raw = session.get("last_tutor_json")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def add_message(self, user_id: int, role: str, text: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO messages (user_id, role, text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, role, text[:4000], utcnow()),
        )
        await self.conn.execute(
            """
            DELETE FROM messages
            WHERE user_id = ?
              AND id NOT IN (
                SELECT id FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 30
              )
            """,
            (user_id, user_id),
        )
        await self.conn.commit()

    async def recent_messages(self, user_id: int, limit: int = 8) -> list[dict]:
        cursor = await self.conn.execute(
            """
            SELECT role, text FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        rows.reverse()
        return rows

    async def insert_note(
        self,
        user_id: int,
        title: str,
        subject: str,
        body: str,
        source: str,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO notes (user_id, title, subject, body, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                title.strip()[:120] or "Конспект",
                (subject or "").strip()[:80],
                body.strip(),
                source,
                utcnow(),
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def count_notes(self, user_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS total FROM notes WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return int(row["total"])

    async def list_notes(self, user_id: int, offset: int, limit: int) -> list[dict]:
        cursor = await self.conn.execute(
            """
            SELECT id, title, subject, body, source, created_at
            FROM notes
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_note(self, user_id: int, note_id: int) -> dict | None:
        cursor = await self.conn.execute(
            """
            SELECT id, user_id, title, subject, body, source, created_at
            FROM notes
            WHERE id = ? AND user_id = ?
            """,
            (note_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def delete_note(self, user_id: int, note_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def note_cards(self, user_id: int, limit: int = 30) -> list[dict]:
        cursor = await self.conn.execute(
            """
            SELECT id, title, subject, substr(body, 1, 180) AS preview
            FROM notes
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def search_fts(self, user_id: int, match: str, limit: int = 5) -> list[dict]:
        cursor = await self.conn.execute(
            f"""
            SELECT
                notes.id,
                notes.title,
                notes.subject,
                notes.body,
                snippet(notes_fts, 0, char(1), char(2), '…', 12) AS title_snip,
                snippet(notes_fts, 1, char(1), char(2), '…', 18) AS body_snip
            FROM notes_fts
            JOIN notes ON notes.id = notes_fts.rowid
            WHERE notes_fts MATCH ? AND notes.user_id = ?
            ORDER BY notes.id DESC
            LIMIT ?
            """,
            (match, user_id, limit),
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            title_snip = item.pop("title_snip") or ""
            body_snip = item.pop("body_snip") or ""
            if MARK_START in body_snip:
                item["raw_snippet"] = body_snip
            elif MARK_START in title_snip:
                item["raw_snippet"] = title_snip
            else:
                item["raw_snippet"] = (item["body"] or "")[:180]
            rows.append(item)
        return rows

    async def insert_error(
        self,
        user_id: int,
        subject: str,
        problem: str,
        mistake: str,
        explanation: str,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO errors (user_id, subject, problem, mistake, explanation, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                (subject or "")[:80],
                problem.strip()[:2000],
                mistake.strip()[:300] or "ошибка в решении",
                explanation.strip()[:2000],
                utcnow(),
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def list_errors(self, user_id: int, limit: int = 8) -> list[dict]:
        cursor = await self.conn.execute(
            """
            SELECT id, subject, problem, mistake, explanation, created_at
            FROM errors
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def count_errors(self, user_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS total FROM errors WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return int(row["total"])

    async def insert_check(self, user_id: int, fields: dict) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO checks (
                user_id, subject, problem, student_solution, is_correct,
                error_step, hint, full_explanation, skill, praise, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                fields.get("subject", "")[:80],
                fields.get("problem", "")[:2000],
                fields.get("student_solution", "")[:2000],
                1 if fields.get("is_correct") else 0,
                fields.get("error_step", "")[:500],
                fields.get("hint", "")[:2000],
                fields.get("full_explanation", "")[:4000],
                fields.get("skill", "")[:200],
                fields.get("praise", "")[:500],
                utcnow(),
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def get_check(self, user_id: int, check_id: int) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM checks WHERE id = ? AND user_id = ?",
            (check_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_quiz(
        self,
        user_id: int,
        title: str,
        source: str,
        questions: list[dict],
    ) -> int:
        now = utcnow()
        await self.conn.execute(
            """
            UPDATE quizzes
            SET status = 'replaced', finished_at = ?
            WHERE user_id = ? AND status = 'active'
            """,
            (now, user_id),
        )
        cursor = await self.conn.execute(
            """
            INSERT INTO quizzes (user_id, title, source, status, started_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (user_id, title[:120] or "Квиз", source, now),
        )
        quiz_id = int(cursor.lastrowid)
        for position, question in enumerate(questions):
            await self.conn.execute(
                """
                INSERT INTO quiz_questions (
                    quiz_id, prompt, options_json, correct_index,
                    explanation, status, position, attempts
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?, 0)
                """,
                (
                    quiz_id,
                    question["prompt"],
                    json.dumps(question["options"], ensure_ascii=False),
                    int(question["correct_index"]),
                    question["explanation"],
                    position,
                ),
            )
        await self.conn.execute(
            """
            UPDATE sessions
            SET active_quiz_id = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (quiz_id, now, user_id),
        )
        await self.conn.commit()
        return quiz_id

    async def get_quiz(self, quiz_id: int) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM quizzes WHERE id = ?",
            (quiz_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_question(self, question_id: int) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM quiz_questions WHERE id = ?",
            (question_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def next_pending(self, quiz_id: int) -> dict | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM quiz_questions
            WHERE quiz_id = ? AND status = 'pending'
            ORDER BY position, id
            LIMIT 1
            """,
            (quiz_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def quiz_counts(self, quiz_id: int) -> tuple[int, int]:
        cursor = await self.conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
                COUNT(*) AS total
            FROM quiz_questions
            WHERE quiz_id = ?
            """,
            (quiz_id,),
        )
        row = await cursor.fetchone()
        return int(row["done"] or 0), int(row["total"] or 0)

    async def mark_question_done(self, question_id: int) -> None:
        await self.conn.execute(
            """
            UPDATE quiz_questions
            SET status = 'done', attempts = attempts + 1
            WHERE id = ?
            """,
            (question_id,),
        )
        await self.conn.commit()

    async def requeue_question(self, question_id: int) -> None:
        question = await self.get_question(question_id)
        if question is None:
            return
        cursor = await self.conn.execute(
            "SELECT COALESCE(MAX(position), 0) AS max_pos FROM quiz_questions WHERE quiz_id = ?",
            (question["quiz_id"],),
        )
        row = await cursor.fetchone()
        await self.conn.execute(
            """
            UPDATE quiz_questions
            SET position = ?, attempts = attempts + 1, status = 'pending'
            WHERE id = ?
            """,
            (int(row["max_pos"]) + 1, question_id),
        )
        await self.conn.commit()

    async def finish_quiz(self, quiz_id: int, user_id: int) -> None:
        now = utcnow()
        await self.conn.execute(
            """
            UPDATE quizzes
            SET status = 'done', finished_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (now, quiz_id),
        )
        await self.conn.execute(
            """
            UPDATE sessions
            SET active_quiz_id = NULL, updated_at = ?
            WHERE user_id = ? AND active_quiz_id = ?
            """,
            (now, user_id, quiz_id),
        )
        await self.conn.commit()


def _until(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
