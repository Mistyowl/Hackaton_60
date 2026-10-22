import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.db import Database
from app.services.notes import register_check, save_manual, search_notes
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

    async def test_cabinet_search_highlight_data(self) -> None:
        await self.db.insert_note(
            1,
            "Комбинаторика",
            "Математика",
            "Число перестановок букв слова равно факториалу.",
            "chat",
        )
        app = create_app(self.db, FakeTutor())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://cabinet") as client:
            health = await client.get("/health")
            self.assertEqual(health.status_code, 200)
            page = await client.get("/")
            self.assertIn("<mark>", page.text)
            found = await client.get("/api/search", params={"user_id": 1, "q": "перестановок"})
            self.assertEqual(found.status_code, 200)
            payload = found.json()
            self.assertEqual(payload[0]["title"], "Комбинаторика")
            self.assertIn("перестановок", payload[0]["snippet"])
            self.assertFalse(payload[0]["semantic"])
            hidden = await client.get("/api/notes", params={"user_id": 2})
            self.assertEqual(hidden.json()["total"], 0)


if __name__ == "__main__":
    unittest.main()
