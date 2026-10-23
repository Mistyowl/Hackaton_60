import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.bot.commands import bot_commands
from app.config import gemini_proxy_url, parse_allowed_user_ids, user_allowed
from app.services.tutor import Tutor
from maxapi.types.updates.message_callback import MessageForCallback

from app.bot.keyboards import cabinet_keyboard, grade_keyboard, menu_keyboard, style_keyboard
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
    model_html,
    parse_command,
)
from app.web import create_app
from app.webapp import MAX_INIT_AGE_SECONDS, sign_init_data, validate_init_data

BOT_TOKEN = "test-bot-token"


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
        marked = "шаг **деления** и `x+1` <b>"
        self.assertEqual(
            model_html(marked),
            "шаг <b>деления</b> и <code>x+1</code> &lt;b&gt;",
        )
        self.assertEqual(model_html("2 * 2 и **\nнет**"), "2 * 2 и **\nнет**")
        self.assertEqual(model_html("a/b = c/d"), "<b>a/b = c/d</b>")
        self.assertEqual(
            model_html("Пример: 2/3 = 4/5"),
            "<b>Пример:</b>\n<b>2/3 = 4/5</b>",
        )
        sample = "разберем **деление дробей**. Пример: `a/b : c/d = a/b * d/c`. Дальше"
        self.assertIn("<b>деление дробей</b>", model_html(sample))
        self.assertIn("<b>Пример:</b>\n<b>a/b : c/d = a/b * d/c</b>", model_html(sample))
        self.assertIn("Дальше", model_html(sample))
        self.assertEqual(fts_match('знак "переноса"'), '"знак" AND "переноса"')


class ProxyTests(unittest.TestCase):
    def test_empty_host_has_no_proxy(self) -> None:
        self.assertEqual(gemini_proxy_url("", 1080, "user", "secret"), "")
        self.assertEqual(gemini_proxy_url("   ", 1080, "user", "secret"), "")

    def test_socks5_url_quotes_credentials(self) -> None:
        url = gemini_proxy_url("proxy.example", 1080, "user", "p@ss word")
        self.assertTrue(url.startswith("socks5://"))
        self.assertIn("p%40ss%20word", url)
        self.assertNotIn("p@ss word", url)
        self.assertIn("@proxy.example:1080", url)
        self.assertEqual(gemini_proxy_url("10.0.0.1", 1080, "", ""), "socks5://10.0.0.1:1080")

    def test_host_without_port_fails(self) -> None:
        with self.assertRaises(ValueError):
            gemini_proxy_url("proxy.example", 0, "", "")

    def test_gemini_proxy_uses_httpx_not_aiohttp(self) -> None:
        tutor = Tutor("test-key", "gemini-test", "socks5://user:secret@proxy.example:1080")
        api = tutor._client._api_client
        self.assertFalse(api._use_aiohttp())
        self.assertIsNotNone(api._http_options.httpx_async_client)
        self.assertIsNotNone(api._http_options.httpx_client)


class AllowTests(unittest.TestCase):
    def test_empty_list_allows_anyone(self) -> None:
        self.assertEqual(parse_allowed_user_ids(""), set())
        self.assertEqual(parse_allowed_user_ids(" , "), set())
        self.assertTrue(user_allowed(99, set()))

    def test_listed_ids_only(self) -> None:
        allowed = parse_allowed_user_ids("11, 22,33")
        self.assertEqual(allowed, {11, 22, 33})
        self.assertTrue(user_allowed(11, allowed))
        self.assertTrue(user_allowed(22, allowed))
        self.assertTrue(user_allowed(33, allowed))
        self.assertFalse(user_allowed(1, allowed))
        with self.assertRaises(ValueError):
            parse_allowed_user_ids("11,abc")


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

    async def test_cabinet_opens_for_the_signed_student(self) -> None:
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
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        anya = _init_data(1, "Аня")
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            health = await client.get("/health")
            self.assertEqual(health.status_code, 200)
            page = await client.get("/")
            self.assertIn("<mark>", page.text)
            self.assertIn("Откройте кабинет кнопкой в чате MAX", page.text)
            self.assertIn("https://st.max.ru/js/max-web-app.js", page.text)
            self.assertNotIn("Боря", page.text)
            self.assertNotIn("Секрет Бори", page.text)
            self.assertNotIn("/k/", page.text)
            self.assertEqual((await client.get("/api/profile")).status_code, 401)
            self.assertEqual((await client.get("/api/users")).status_code, 404)
            self.assertEqual(
                (await client.get("/api/search", params={"user_id": 2, "q": "перестановок"})).status_code,
                401,
            )
            self.assertEqual((await client.get("/api/notes")).status_code, 401)
            self.assertEqual((await client.get("/api/errors")).status_code, 401)

            forged = await client.post("/api/session", json={"init_data": anya[:-4] + "abcd"})
            self.assertEqual(forged.status_code, 401)
            self.assertEqual((await client.get(f"/k/{anya}")).status_code, 404)
            first = await client.post("/api/session", json={"init_data": anya, "user_id": 2})
            self.assertEqual(first.status_code, 200)
            cookie = first.headers["set-cookie"]
            self.assertIn("cabinet=", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)
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

        async with AsyncClient(transport=transport, base_url="https://cabinet") as replay:
            stale = _init_data(1, "Аня", auth_date=int(datetime.now(timezone.utc).timestamp()) - MAX_INIT_AGE_SECONDS - 5)
            opened = await replay.post("/api/session", json={"init_data": stale})
            self.assertEqual(opened.status_code, 401)
            self.assertNotIn("set-cookie", {key.lower() for key in opened.headers})
            self.assertEqual((await replay.get("/api/profile")).status_code, 401)
            self.assertNotIn("Боря", opened.text)
            self.assertNotIn("Аня", opened.text)

        async with AsyncClient(transport=transport, base_url="https://cabinet") as fresh:
            again = await fresh.post("/api/session", json={"init_data": anya})
            self.assertEqual(again.status_code, 200)
            profile = await fresh.get("/api/profile")
            self.assertEqual(profile.json()["first_name"], "Аня")

        async with AsyncClient(transport=transport, base_url="https://cabinet") as ghost:
            opened = await ghost.post("/api/session", json={"init_data": _init_data(99, "Гость")})
            self.assertEqual(opened.status_code, 200)
            profile = await ghost.get("/api/profile")
            self.assertEqual(profile.json()["first_name"], "Гость")
            self.assertEqual((await ghost.get("/api/notes")).json()["total"], 0)
            self.assertNotIn("Аня", profile.text)
        self.assertIsNotNone(await self.db.find_user(99))

    async def test_cabinet_delete_belongs_to_the_session(self) -> None:
        own = await self.db.insert_note(1, "Свой конспект", "Математика", "только Аня", "manual")
        kept = await self.db.insert_note(1, "Второй конспект", "Математика", "тоже Аня", "manual")
        foreign = await self.db.insert_note(2, "Чужой конспект", "Физика", "только Боря", "manual")
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            page = await anon.get("/")
            self.assertIn("Удалить", page.text)
            self.assertEqual((await anon.delete(f"/api/notes/{own}")).status_code, 401)
            self.assertIsNotNone(await self.db.get_note(1, own))
        async with AsyncClient(transport=transport, base_url="https://cabinet") as owner:
            opened = await owner.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            self.assertEqual(opened.status_code, 200)
            missed = await owner.delete(f"/api/notes/{foreign}")
            self.assertEqual(missed.status_code, 404)
            self.assertIsNotNone(await self.db.get_note(2, foreign))
            removed = await owner.delete(f"/api/notes/{own}")
            self.assertEqual(removed.status_code, 200)
            self.assertTrue(removed.json()["deleted"])
            self.assertIsNone(await self.db.get_note(1, own))
        async with AsyncClient(transport=transport, base_url="https://cabinet") as stranger:
            await stranger.post("/api/session", json={"init_data": _init_data(2, "Боря")})
            blocked = await stranger.delete(f"/api/notes/{kept}")
            self.assertEqual(blocked.status_code, 404)
            self.assertIsNotNone(await self.db.get_note(1, kept))

    async def test_cabinet_rejects_user_outside_allowlist(self) -> None:
        allowed = {11, 22, 33}
        app = create_app(self.db, FakeTutor(), BOT_TOKEN, allowed)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            blocked = await client.post("/api/session", json={"init_data": _init_data(99, "Гость")})
            self.assertEqual(blocked.status_code, 403)
            self.assertIn("участников теста", blocked.text)
            self.assertNotIn("set-cookie", {key.lower() for key in blocked.headers})
            self.assertIsNone(await self.db.find_user(99))
            opened = await client.post(
                "/api/session",
                json={"init_data": _init_data(11, "Лена")},
            )
            self.assertEqual(opened.status_code, 200)
            self.assertEqual((await client.get("/api/profile")).json()["first_name"], "Лена")
        token = await self.db.create_cabinet_session(2)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as stranger:
            stranger.cookies.set("cabinet", token)
            profile = await stranger.get("/api/profile")
            self.assertEqual(profile.status_code, 403)
            self.assertIn("участников теста", profile.text)

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
        opened = menu_keyboard(True, "ideya_fiks_60_bot")
        with_cabinet = [
            button.payload for row in opened.payload.buttons for button in row if button.payload
        ]
        self.assertNotIn("menu:cabinet", with_cabinet)
        self.assertIn("menu:ask", with_cabinet)
        self.assertIn("menu:own", with_cabinet)
        self.assertNotIn("menu:search", with_cabinet)
        self.assertNotIn("menu:notes", with_cabinet)
        cabinet_button = opened.model_dump()["payload"]["buttons"][-1][0]
        self.assertEqual(cabinet_button["type"], "open_app")
        self.assertEqual(cabinet_button["text"], "Кабинет")
        self.assertEqual(cabinet_button["web_app"], "ideya_fiks_60_bot")
        self.assertNotIn("contact_id", cabinet_button)
        self.assertNotIn("payload", cabinet_button)
        published = [command.name for command in bot_commands()]
        self.assertIn("notes", published)
        self.assertIn("search", published)
        hidden = [command.name for command in bot_commands(True)]
        self.assertNotIn("notes", hidden)
        self.assertNotIn("search", hidden)
        self.assertIn("quiz", hidden)
        self.assertIn("check", hidden)
        link = cabinet_keyboard("ideya_fiks_60_bot")
        opener = link.model_dump()["payload"]["buttons"][0][0]
        self.assertEqual(opener["text"], "Кабинет")
        self.assertEqual(opener["type"], "open_app")
        self.assertEqual(opener["web_app"], "ideya_fiks_60_bot")
        edited = MessageForCallback(text="Меню", attachments=[opened, link])
        for stored in edited.attachments:
            open_app = [
                button
                for row in stored.model_dump()["payload"]["buttons"]
                for button in row
                if button["type"] == "open_app"
            ]
            self.assertEqual(len(open_app), 1)
            self.assertNotIn("contact_id", open_app[0])
            self.assertNotIn("payload", open_app[0])
        await self.db.set_explain_style(1, "short")
        self.assertIsNone(onboard_step(await self.db.get_user(1)))


class WebAppTests(unittest.TestCase):
    def test_signed_launch_names_the_student(self) -> None:
        now = 1_700_000_000
        raw = _init_data(7, "Аня Петрова", auth_date=now)
        user = validate_init_data(raw, BOT_TOKEN, now + 10)
        assert user is not None
        self.assertEqual(user.user_id, 7)
        self.assertEqual(user.first_name, "Аня Петрова")
        self.assertIsNone(validate_init_data(raw, "other-token", now + 10))
        self.assertIsNone(validate_init_data(raw + "&hash=second", BOT_TOKEN, now + 10))
        self.assertIsNone(validate_init_data(raw, BOT_TOKEN, now + MAX_INIT_AGE_SECONDS + 5))
        self.assertIsNone(validate_init_data(raw.replace("1700000000", "1700000001", 1), BOT_TOKEN, now + 10))


def _init_data(user_id: int, first_name: str, *, auth_date: int | None = None, token: str = BOT_TOKEN) -> str:
    if auth_date is None:
        auth_date = int(datetime.now(timezone.utc).timestamp())
    user = json.dumps(
        {"id": user_id, "first_name": first_name},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sign_init_data(
        {"auth_date": str(auth_date), "query_id": "cabinet-test", "user": user},
        token,
    )


if __name__ == "__main__":
    unittest.main()
