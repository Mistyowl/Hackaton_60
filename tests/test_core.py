import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.bot.commands import bot_commands
from app.bot.keyboards import cabinet_link_keyboard, grade_keyboard, menu_keyboard, style_keyboard
from app.db import Database
from app.services.notes import onboard_step, register_check, save_manual, search_notes, student_dossier
from app.services.quiz import answer_question
from app.services.tutor import (
    CheckResult,
    QuestionDraft,
    TutorError,
    _QuestionSchema,
    _QuizSchema,
    normalize_quiz,
)
from app.textutil import (
    MARK_END,
    MARK_START,
    check_prefix,
    format_elapsed,
    fts_match,
    markers_to_html,
    parse_command,
)
from app.web import create_app


class TextTests(unittest.TestCase):
    def test_command_and_check_prefix(self) -> None:
        self.assertEqual(parse_command("/Quiz квадратные"), ("/quiz", "квадратные"))
        self.assertIsNone(parse_command("просто текст"))
        self.assertEqual(check_prefix("Проверь: 2+2=5"), "2+2=5")
        self.assertEqual(check_prefix("проверь"), "")
        self.assertIsNone(check_prefix("проверьте тетрадь"))

    def test_elapsed_and_markers(self) -> None:
        start = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
        now = datetime(2026, 10, 22, 12, 1, 12, tzinfo=timezone.utc)
        self.assertEqual(format_elapsed(start.isoformat(), now), "1 мин 12 сек")
        raw = f"катет {MARK_START}Пифагор{MARK_END} <угол>"
        self.assertEqual(markers_to_html(raw), "катет <b>Пифагор</b> &lt;угол&gt;")
        self.assertEqual(fts_match('знак "переноса"'), '"знак" AND "переноса"')


class NormalizeTests(unittest.TestCase):
    def test_keeps_four_valid_questions(self) -> None:
        questions = [
            _QuestionSchema(
                prompt=f"Вопрос {index}",
                options=["1", "2", "3", "4"],
                correct_index=1,
                explanation="потому что",
            )
            for index in range(5)
        ]
        questions.append(
            _QuestionSchema(
                prompt="битый",
                options=["только один"],
                correct_index=0,
                explanation="",
            )
        )
        title, drafts = normalize_quiz(_QuizSchema(title="", questions=questions), "Тема")
        self.assertEqual(title, "Тема")
        self.assertEqual(len(drafts), 5)
        self.assertIsInstance(drafts[0], QuestionDraft)

    def test_rejects_short_pack(self) -> None:
        pack = _QuizSchema(
            title="Мало",
            questions=[
                _QuestionSchema(
                    prompt="один",
                    options=["а", "б", "в", "г"],
                    correct_index=0,
                    explanation="да",
                )
            ],
        )
        with self.assertRaises(TutorError):
            normalize_quiz(pack, "Тема")


class FakeTutor:
    enabled = True

    def __init__(self, ids: list[int] | None = None) -> None:
        self.ids = ids or []
        self.calls = 0

    async def pick_notes(self, query: str, cards: list[dict]) -> list[int]:
        self.calls += 1
        return [note_id for note_id in self.ids if any(card["id"] == note_id for card in cards)]


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "tutor.db"))
        await self.db.connect()
        await self.db.upsert_user(1, "Аня")
        await self.db.upsert_user(2, "Боря")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_notes_are_private_and_searchable(self) -> None:
        await self.db.insert_note(
            1,
            "Теорема Пифагора",
            "Геометрия",
            "Квадрат гипотенузы равен сумме квадратов катетов.",
            "chat",
        )
        await self.db.insert_note(
            2,
            "Теорема Пифагора",
            "Геометрия",
            "Чужой конспект про гипотенузу.",
            "chat",
        )
        hits = await search_notes(self.db, FakeTutor(), 1, "гипотенузы")
        self.assertEqual(len(hits), 1)
        self.assertIn("гипотенуз", hits[0].snippet.lower())
        self.assertIn("<b>", hits[0].snippet_html)
        self.assertFalse(hits[0].semantic)
        self.assertEqual(await search_notes(self.db, FakeTutor(), 2, "катетов"), [])

    async def test_delete_drops_search_hit(self) -> None:
        note_id = await self.db.insert_note(1, "Дроби", "Математика", "Общий знаменатель.", "manual")
        self.assertEqual(len(await search_notes(self.db, FakeTutor(), 1, "знаменатель")), 1)
        self.assertTrue(await self.db.delete_note(1, note_id))
        self.assertEqual(await search_notes(self.db, FakeTutor(), 1, "знаменатель"), [])
        self.assertFalse(await self.db.delete_note(2, note_id))

    async def test_semantic_fallback_uses_only_own_notes(self) -> None:
        note_id = await self.db.insert_note(
            1,
            "Прямоугольный треугольник",
            "Геометрия",
            "Катеты образуют прямой угол.",
            "chat",
        )
        tutor = FakeTutor([note_id, 999])
        hits = await search_notes(self.db, tutor, 1, "пифагор")
        self.assertEqual(tutor.calls, 1)
        self.assertEqual([hit.id for hit in hits], [note_id])
        self.assertTrue(hits[0].semantic)

    async def test_manual_note_title_is_first_line(self) -> None:
        note_id = await save_manual(self.db, 1, "Закон Ома\nСила тока равна напряжению, делённому на сопротивление.")
        note = await self.db.get_note(1, note_id)
        self.assertIsNotNone(note)
        assert note is not None
        self.assertEqual(note["title"], "Закон Ома")
        self.assertEqual(note["source"], "manual")

    async def test_wrong_check_fills_error_bank(self) -> None:
        wrong = CheckResult(
            is_schoolwork=True,
            subject="Алгебра",
            problem_text="2x = 4",
            student_solution_text="x = 4",
            is_correct=False,
            error_step="не разделил на 2",
            hint="Раздели обе части на коэффициент.",
            full_explanation="x = 2",
            skill="деление обеих частей",
            praise="",
        )
        right = CheckResult(
            is_schoolwork=True,
            subject="Алгебра",
            problem_text="2x = 4",
            student_solution_text="x = 2",
            is_correct=True,
            error_step="",
            hint="",
            full_explanation="x = 2",
            skill="деление обеих частей",
            praise="Верно разделил обе части.",
        )
        noise = CheckResult(
            is_schoolwork=False,
            subject="",
            problem_text="",
            student_solution_text="",
            is_correct=False,
            error_step="",
            hint="",
            full_explanation="",
            skill="",
            praise="",
        )
        self.assertIsNotNone(await register_check(self.db, 1, wrong))
        self.assertIsNotNone(await register_check(self.db, 1, right))
        self.assertIsNone(await register_check(self.db, 1, noise))
        self.assertEqual(await self.db.count_errors(1), 1)
        errors = await self.db.list_errors(1)
        self.assertEqual(errors[0]["mistake"], "деление обеих частей")

    async def test_quiz_returns_wrong_answer_to_the_end(self) -> None:
        quiz_id = await self.db.create_quiz(
            1,
            "Сложение",
            "topic",
            [
                {
                    "prompt": f"Сколько будет {index}?",
                    "options": ["0", "1", "2", "3"],
                    "correct_index": 1,
                    "explanation": "Сложи ещё раз.",
                }
                for index in range(3)
            ],
        )
        first = await self.db.next_pending(quiz_id)
        assert first is not None
        wrong = await answer_question(self.db, 1, first["id"], 0)
        self.assertFalse(wrong.correct)
        self.assertFalse(wrong.finished)
        self.assertEqual(wrong.done, 0)
        assert wrong.next_question is not None
        self.assertNotEqual(wrong.next_question["id"], first["id"])

        second = wrong.next_question
        closed = await answer_question(self.db, 1, second["id"], 1)
        self.assertTrue(closed.correct)
        self.assertEqual(closed.done, 1)

        third = closed.next_question
        assert third is not None
        await answer_question(self.db, 1, third["id"], 1)
        returned = await self.db.next_pending(quiz_id)
        assert returned is not None
        self.assertEqual(returned["id"], first["id"])
        done = await answer_question(self.db, 1, returned["id"], 1)
        self.assertTrue(done.finished)
        self.assertEqual(done.done, 3)
        self.assertEqual(done.total, 3)
        quiz = await self.db.get_quiz(quiz_id)
        assert quiz is not None
        self.assertEqual(quiz["status"], "done")

        again = await answer_question(self.db, 1, returned["id"], 1)
        self.assertTrue(again.already_closed)
        stranger = await answer_question(self.db, 2, returned["id"], 1)
        self.assertTrue(stranger.missing)

    async def test_cabinet_link_opens_once_for_one_student(self) -> None:
        await self.db.set_grade(1, 7)
        await self.db.set_explain_style(1, "detail")
        await self.db.insert_note(
            1,
            "Комбинаторика",
            "Математика",
            "Число перестановок букв слова равно факториалу.",
            "chat",
        )
        await self.db.insert_note(
            2,
            "Секрет Бори",
            "Физика",
            "Чужое слово перестановок не должно всплыть.",
            "chat",
        )
        await self.db.insert_error(1, "Алгебра", "Я" * 400, "приём-деления", "СКРЫТЫЙ РАЗБОР")
        await self.db.insert_error(2, "Физика", "чужая задача", "секрет Бори", "чужой разбор")
        token = await self.db.create_cabinet_link(1)
        self.assertGreaterEqual(len(token), 8)
        self.assertLessEqual(len(token), 16)
        app = create_app(self.db, FakeTutor())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://cabinet") as client:
            health = await client.get("/health")
            self.assertEqual(health.status_code, 200)
            page = await client.get("/")
            self.assertIn("<mark>", page.text)
            self.assertIn("Откройте кабинет из чата с ботом", page.text)
            self.assertNotIn("Боря", page.text)
            self.assertNotIn("Секрет Бори", page.text)
            self.assertNotIn("мини-приложен", page.text.lower())
            self.assertEqual((await client.get("/api/profile")).status_code, 401)
            self.assertEqual((await client.get("/api/users")).status_code, 404)
            self.assertEqual(
                (await client.get("/api/search", params={"user_id": 2, "q": "перестановок"})).status_code,
                401,
            )
            self.assertEqual((await client.get("/api/notes")).status_code, 401)
            self.assertEqual((await client.get("/api/errors")).status_code, 401)

            first = await client.get(f"/k/{token}", follow_redirects=False)
            self.assertEqual(first.status_code, 302)
            self.assertEqual(first.headers["location"], "/")
            cookie = first.headers["set-cookie"]
            self.assertIn("cabinet=", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("samesite=lax", cookie.lower())
            self.assertIn("Path=/", cookie)
            self.assertIn("Max-Age=2700", cookie)

            profile = await client.get("/api/profile", params={"user_id": 2})
            self.assertEqual(profile.status_code, 200)
            body = profile.json()
            self.assertEqual(body["first_name"], "Аня")
            self.assertEqual(body["grade"], 7)
            self.assertEqual(body["explain_style"], "detail")
            self.assertNotIn("user_id", body)
            self.assertNotIn("Боря", profile.text)

            found = await client.get("/api/search", params={"q": "перестановок", "user_id": 2})
            self.assertEqual(found.status_code, 200)
            payload = found.json()
            self.assertEqual([item["title"] for item in payload], ["Комбинаторика"])
            self.assertIn("перестановок", payload[0]["snippet"])
            self.assertFalse(payload[0]["semantic"])
            self.assertNotIn("Секрет", found.text)
            self.assertEqual((await client.get("/api/search", params={"q": "  "})).json(), [])

            notes = await client.get("/api/notes", params={"user_id": 2})
            self.assertEqual(notes.json()["total"], 1)
            self.assertNotIn("Секрет", notes.text)
            self.assertNotIn("body", notes.json()["notes"][0])
            detail = await client.get(f"/api/notes/{notes.json()['notes'][0]['id']}")
            self.assertIn("факториалу", detail.json()["body"])
            foreign = await self.db.insert_note(2, "Ещё секрет", "Физика", "скрыто", "chat")
            self.assertEqual((await client.get(f"/api/notes/{foreign}")).status_code, 404)

            mistakes = await client.get("/api/errors", params={"user_id": 2})
            self.assertEqual(mistakes.status_code, 200)
            error_rows = mistakes.json()
            self.assertEqual([item["mistake"] for item in error_rows], ["приём-деления"])
            self.assertNotIn("explanation", error_rows[0])
            self.assertNotIn("СКРЫТЫЙ РАЗБОР", mistakes.text)
            self.assertNotIn("секрет Бори", mistakes.text)
            self.assertLessEqual(len(error_rows[0]["problem"]), 180)

        async with AsyncClient(transport=transport, base_url="http://cabinet") as replay:
            second = await replay.get(f"/k/{token}", follow_redirects=False)
            self.assertEqual(second.status_code, 200)
            self.assertIn("Ссылка уже не действует", second.text)
            self.assertNotIn("set-cookie", {key.lower() for key in second.headers})
            self.assertEqual((await replay.get("/api/profile")).status_code, 401)
            self.assertNotIn("Боря", second.text)
            self.assertNotIn("Аня", second.text)

        expired = await self.db.create_cabinet_link(1)
        await self.db.conn.execute(
            "UPDATE cabinet_links SET expires_at = ? WHERE token = ?",
            ("2000-01-01T00:00:00+00:00", expired),
        )
        await self.db.conn.commit()
        async with AsyncClient(transport=transport, base_url="http://cabinet") as stale:
            opened = await stale.get(f"/k/{expired}", follow_redirects=False)
            self.assertEqual(opened.status_code, 200)
            self.assertIn("Ссылка уже не действует", opened.text)
            self.assertNotIn("set-cookie", {key.lower() for key in opened.headers})
            self.assertEqual((await stale.get("/api/profile")).status_code, 401)

        await self.db.conn.execute(
            """
            INSERT INTO cabinet_links (token, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            ("ghost-link-token", 99, "2999-01-01T00:00:00+00:00"),
        )
        await self.db.conn.commit()
        async with AsyncClient(transport=transport, base_url="http://cabinet") as ghost:
            opened = await ghost.get("/k/ghost-link-token", follow_redirects=False)
            self.assertEqual(opened.status_code, 302)
            self.assertEqual((await ghost.get("/api/profile")).status_code, 401)
        self.assertIsNone(await self.db.find_user(99))

    async def test_cabinet_delete_belongs_to_the_session(self) -> None:
        own = await self.db.insert_note(1, "Свой конспект", "Математика", "только Аня", "manual")
        kept = await self.db.insert_note(1, "Второй конспект", "Математика", "тоже Аня", "manual")
        foreign = await self.db.insert_note(2, "Чужой конспект", "Физика", "только Боря", "manual")
        app = create_app(self.db, FakeTutor())
        transport = ASGITransport(app=app)
        anya = await self.db.create_cabinet_link(1)
        borya = await self.db.create_cabinet_link(2)
        async with AsyncClient(transport=transport, base_url="http://cabinet") as anon:
            page = await anon.get("/")
            self.assertIn("Удалить", page.text)
            self.assertEqual((await anon.delete(f"/api/notes/{own}")).status_code, 401)
            self.assertIsNotNone(await self.db.get_note(1, own))
        async with AsyncClient(transport=transport, base_url="http://cabinet") as owner:
            opened = await owner.get(f"/k/{anya}", follow_redirects=False)
            self.assertEqual(opened.status_code, 302)
            missed = await owner.delete(f"/api/notes/{foreign}")
            self.assertEqual(missed.status_code, 404)
            self.assertIsNotNone(await self.db.get_note(2, foreign))
            removed = await owner.delete(f"/api/notes/{own}")
            self.assertEqual(removed.status_code, 200)
            self.assertTrue(removed.json()["deleted"])
            self.assertIsNone(await self.db.get_note(1, own))
        async with AsyncClient(transport=transport, base_url="http://cabinet") as stranger:
            await stranger.get(f"/k/{borya}", follow_redirects=False)
            blocked = await stranger.delete(f"/api/notes/{kept}")
            self.assertEqual(blocked.status_code, 404)
            self.assertIsNotNone(await self.db.get_note(1, kept))

    async def test_dossier_uses_only_this_student(self) -> None:
        await self.db.upsert_user(3, "")
        self.assertEqual(await student_dossier(self.db, 3), "")
        long_problem = "Я" * 400
        await self.db.set_grade(1, 7)
        await self.db.set_explain_style(1, "detail")
        await self.db.insert_error(1, "Алгебра", long_problem, "приём-деления", "короткая подсказка")
        await self.db.insert_error(2, "Физика", "чужая задача", "секрет Бори", "чужая подсказка")
        await self.db.insert_note(1, "Свой конспект Ани", "Геометрия", "Начало конспекта.", "manual")
        await register_check(
            self.db,
            1,
            CheckResult(
                is_schoolwork=True,
                subject="Алгебра",
                problem_text="2x = 4",
                student_solution_text="x = 4",
                is_correct=False,
                error_step="не разделил",
                hint="Раздели на коэффициент.",
                full_explanation="СЕКРЕТНЫЙ ПОЛНЫЙ РАЗБОР",
                skill="линейное уравнение",
                praise="",
            ),
        )
        voice = await student_dossier(self.db, 1, records=False)
        self.assertIn("Аня", voice)
        self.assertIn("Класс: 7", voice)
        self.assertNotIn("приём-деления", voice)
        self.assertNotIn("Свой конспект Ани", voice)
        text = await student_dossier(self.db, 1)
        self.assertIn("Аня", text)
        self.assertIn("Класс: 7", text)
        self.assertIn("подробнее", text)
        self.assertIn("приём-деления", text)
        self.assertIn("Свой конспект Ани", text)
        self.assertIn("Раздели на коэффициент.", text)
        self.assertNotIn("секрет Бори", text)
        self.assertNotIn("СЕКРЕТНЫЙ ПОЛНЫЙ РАЗБОР", text)
        self.assertNotIn(long_problem, text)
        self.assertLessEqual(len(text), 1200)
        other = await student_dossier(self.db, 2)
        self.assertIn("секрет Бори", other)
        self.assertNotIn("приём-деления", other)

    async def test_menu_waits_until_style_is_chosen(self) -> None:
        user = await self.db.get_user(1)
        self.assertEqual(onboard_step(user), "grade")
        await self.db.set_mode(1, "onboard_grade")
        await self.db.set_grade(1, 6)
        self.assertEqual((await self.db.get_session(1))["mode"], "onboard_grade")
        self.assertEqual(onboard_step(await self.db.get_user(1)), "style")
        grade_payloads = [
            button.payload
            for row in grade_keyboard().payload.buttons
            for button in row
        ]
        style_payloads = [
            button.payload
            for row in style_keyboard().payload.buttons
            for button in row
        ]
        self.assertEqual(grade_payloads, [f"grade:{number}" for number in range(1, 12)])
        self.assertEqual(style_payloads, ["style:short", "style:detail"])
        self.assertNotIn("menu:home", grade_payloads + style_payloads)
        menu_payloads = [
            button.payload for row in menu_keyboard().payload.buttons for button in row
        ]
        self.assertIn("menu:ask", menu_payloads)
        self.assertIn("menu:search", menu_payloads)
        self.assertIn("menu:notes", menu_payloads)
        self.assertNotIn("menu:cabinet", menu_payloads)
        with_cabinet = [
            button.payload for row in menu_keyboard(True).payload.buttons for button in row
        ]
        self.assertIn("menu:cabinet", with_cabinet)
        self.assertIn("menu:ask", with_cabinet)
        self.assertIn("menu:own", with_cabinet)
        self.assertNotIn("menu:search", with_cabinet)
        self.assertNotIn("menu:notes", with_cabinet)
        published = [command.name for command in bot_commands()]
        self.assertIn("notes", published)
        self.assertIn("search", published)
        hidden = [command.name for command in bot_commands(True)]
        self.assertNotIn("notes", hidden)
        self.assertNotIn("search", hidden)
        self.assertIn("quiz", hidden)
        self.assertIn("check", hidden)
        link = cabinet_link_keyboard("http://127.0.0.1:8000/k/abc")
        opener = link.payload.buttons[0][0]
        self.assertEqual(opener.text, "Открыть")
        self.assertEqual(opener.url, "http://127.0.0.1:8000/k/abc")
        await self.db.set_explain_style(1, "short")
        self.assertIsNone(onboard_step(await self.db.get_user(1)))


if __name__ == "__main__":
    unittest.main()
