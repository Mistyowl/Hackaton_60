import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.bot.commands import bot_commands
from app.bot.handlers import RecentIds, voice_from_attachments
from app.config import (
    gemini_proxy_url,
    parse_allowed_user_ids,
    user_allowed,
    validate_webhook,
    webhook_path,
)
from app.services.tutor import Tutor
from maxapi.enums.attachment import AttachmentType
from maxapi.types.attachments.attachment import OtherAttachmentPayload
from maxapi.types.attachments.audio import Audio
from maxapi.types.attachments.file import File
from maxapi.types.updates.message_callback import MessageForCallback

from app.bot.keyboards import cabinet_keyboard, grade_keyboard, menu_keyboard, style_keyboard
from app.db import Database
from app.services.limits import allow, reset
from app.services.notes import (
    manual_title,
    onboard_step,
    register_check,
    save_manual,
    search_notes,
    student_dossier,
)
from app.bot.render import render_check, render_feedback
from app.services.calendar_export import EMPTY_CALENDAR, build_calendar, next_evening_slot
from app.services.demo import DEMO_NOTE, DEMO_USER_ID, ensure_demo
from app.services.reminders import MOSCOW, in_quiet_hours, reminder_text
from app.services.skills import (
    _local_skill_match,
    brightness,
    build_skill_graph,
    next_box,
    normalize_name,
    record,
)
from app.services.quiz import (
    AnswerView,
    answer_question,
    start_due_quiz,
    start_error_quiz,
    start_skill_quiz,
    start_topic_quiz,
)
from app.services.tutor import (
    QUIZ_OUTPUT_TOKENS,
    QUOTA_MESSAGE,
    RETRY_CAP_SECONDS,
    CheckResult,
    QuestionDraft,
    TutorError,
    _QuestionSchema,
    _QuizSchema,
    _TutorSchema,
    _coerce_quiz_payload,
    _error_kind,
    _fallback_models,
    _retry_seconds,
    _schema_in_api,
    normalize_quiz,
)
from app.textutil import (
    MARK_END,
    MARK_START,
    audio_filename_ok,
    audio_mime,
    check_prefix,
    format_elapsed,
    fts_match,
    markers_to_html,
    model_html,
    normalize_subject,
    parse_command,
    utcnow,
)
from app.web import _quiz_summary, create_app
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

    def test_audio_mime_and_filename(self) -> None:
        self.assertEqual(audio_mime(b"OggS...." ), "audio/ogg")
        self.assertEqual(audio_mime(b"ID3...." ), "audio/mpeg")
        self.assertEqual(audio_mime(b"RIFF....WAVE....", "x.wav"), "audio/wav")
        self.assertEqual(audio_mime(b"????", "lesson.m4a"), "audio/mp4")
        self.assertTrue(audio_filename_ok("voice.ogg"))
        self.assertTrue(audio_filename_ok("HW.M4A"))
        self.assertFalse(audio_filename_ok("notes.pdf"))


class RecentIdsTests(unittest.TestCase):
    def test_claims_once_until_ttl(self) -> None:
        seen = RecentIds(ttl_seconds=60, max_size=8)
        self.assertTrue(seen.claim("msg:1"))
        self.assertFalse(seen.claim("msg:1"))
        self.assertTrue(seen.claim("msg:2"))
        self.assertTrue(seen.claim(None))
        self.assertTrue(seen.claim(""))

    def test_expires_and_respects_max_size(self) -> None:
        seen = RecentIds(ttl_seconds=30, max_size=2)
        with patch("app.bot.handlers.time.monotonic", side_effect=[1.0, 1.0, 1.0, 40.0, 40.0]):
            self.assertTrue(seen.claim("a"))
            self.assertTrue(seen.claim("b"))
            self.assertTrue(seen.claim("c"))  # вытесняет самый старый
            self.assertTrue(seen.claim("a"))  # ttl истёк — снова можно
            self.assertFalse(seen.claim("a"))


class VoiceAttachmentTests(unittest.TestCase):
    def test_prefers_audio_transcription(self) -> None:
        audio = Audio(
            type=AttachmentType.AUDIO,
            payload=OtherAttachmentPayload(url="https://cdn.example/a.ogg", token="t"),
            transcription="  Как решить уравнение?  ",
        )
        found = voice_from_attachments([audio])
        self.assertEqual(found, ("https://cdn.example/a.ogg", "Как решить уравнение?", ""))

    def test_accepts_audio_file_by_name(self) -> None:
        file = File(
            type=AttachmentType.FILE,
            payload=OtherAttachmentPayload(url="https://cdn.example/v.aac", token="t"),
            filename="voice.aac",
            size=1200,
        )
        found = voice_from_attachments([file])
        self.assertEqual(found, ("https://cdn.example/v.aac", None, "voice.aac"))

    def test_skips_non_audio_file(self) -> None:
        file = File(
            type=AttachmentType.FILE,
            payload=OtherAttachmentPayload(url="https://cdn.example/x.pdf", token="t"),
            filename="x.pdf",
            size=10,
        )
        self.assertIsNone(voice_from_attachments([file]))


class WebhookConfigTests(unittest.TestCase):
    def test_webhook_path_and_validation(self) -> None:
        self.assertEqual(webhook_path("https://bot.example/max-webhook"), "/max-webhook")
        self.assertEqual(webhook_path("https://bot.example/"), "/max-webhook")
        url, secret = validate_webhook(
            "https://bot.example/max-webhook", "secret-1"
        )
        self.assertEqual(url, "https://bot.example/max-webhook")
        self.assertEqual(secret, "secret-1")
        self.assertEqual(validate_webhook("", "secret-1"), ("", ""))
        with self.assertRaises(ValueError):
            validate_webhook("http://bot.example/max-webhook", "secret-1")
        with self.assertRaises(ValueError):
            validate_webhook("https://bot.example/max-webhook", "ab")


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


class _FakeModels:
    def __init__(self, behavior) -> None:
        self.calls: list = []
        self.models: list[str] = []
        self.behavior = behavior

    async def generate_content(self, model, contents, config):
        self.calls.append(config)
        self.models.append(model)
        return self.behavior(config, model)


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = type("Aio", (), {"models": models})()


class _FakeResponse:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.text = ""


class TutorGeminiTests(unittest.IsolatedAsyncioTestCase):
    def _tutor(self, behavior) -> tuple[Tutor, _FakeModels]:
        tutor = Tutor("test-key", "gemini-test")
        models = _FakeModels(behavior)
        tutor._client = _FakeClient(models)
        return tutor, models

    def test_retry_seconds_and_error_kind(self) -> None:
        quota = Exception(
            "429 RESOURCE_EXHAUSTED. Please retry in 46.10723102s. quota exceeded"
        )
        self.assertEqual(_error_kind(quota), "quota")
        self.assertAlmostEqual(_retry_seconds(quota), 46.10723102)
        self.assertEqual(_error_kind(Exception("503 UNAVAILABLE high demand")), "unavailable")
        self.assertEqual(_error_kind(Exception("500 INTERNAL error encountered")), "unavailable")
        self.assertEqual(_error_kind(Exception("boom")), "other")
        self.assertFalse(_schema_in_api("gemma-4-31b-it"))
        self.assertTrue(_schema_in_api("gemini-3.8-flash"))
        self.assertEqual(
            _fallback_models("gemini-3.8-flash, gemma-4-31b-it, gemini-3.8-flash", "gemini-3.5-flash-lite"),
            ["gemini-3.8-flash", "gemma-4-31b-it"],
        )

    def test_coerce_quiz_payload_accepts_question_alias(self) -> None:
        raw = json.dumps(
            [
                {
                    "question": "2+2?",
                    "options": ["1", "2", "3", "4"],
                    "correct_index": 3,
                    "explanation": "четыре",
                }
            ],
            ensure_ascii=False,
        )
        pack = _QuizSchema.model_validate_json(_coerce_quiz_payload(raw))
        self.assertEqual(pack.questions[0].prompt, "2+2?")
        self.assertEqual(pack.questions[0].correct_index, 3)

    async def test_thinking_unsupported_cached(self) -> None:
        def behavior(config, model):
            if getattr(config, "thinking_config", None) is not None:
                raise Exception("INVALID_ARGUMENT: thinking is not supported")
            return _FakeResponse(
                _TutorSchema(reply="ок", subject="Алгебра", title="т", skill="с")
            )

        tutor, models = self._tutor(behavior)
        first = await tutor._generate("sys", [], _TutorSchema, 0.4)
        self.assertEqual(first.parsed.reply, "ок")
        self.assertIs(tutor._thinking_ok["gemini-test"], False)
        self.assertEqual(len(models.calls), 2)

        await tutor._generate("sys", [], _TutorSchema, 0.4)
        self.assertEqual(len(models.calls), 3)
        self.assertIsNone(getattr(models.calls[-1], "thinking_config", None))

    async def test_quota_retry_waits_before_second_call(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        outcomes = iter(
            [
                Exception(
                    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
                    "'message': 'Please retry in 46.1s', 'status': 'RESOURCE_EXHAUSTED'}}"
                ),
                _FakeResponse(
                    _TutorSchema(reply="готово", subject="Алгебра", title="т", skill="с")
                ),
            ]
        )

        def behavior(config, model):
            item = next(outcomes)
            if isinstance(item, Exception):
                raise item
            return item

        tutor, models = self._tutor(behavior)
        tutor._thinking_ok["gemini-test"] = False
        with patch("app.services.tutor.asyncio.sleep", fake_sleep):
            data = await tutor._ask("sys", [], _TutorSchema, 0.4)
        self.assertEqual(data.reply, "готово")
        self.assertEqual(sleeps, [RETRY_CAP_SECONDS])
        self.assertEqual(len(models.calls), 2)

    async def test_quota_exhausted_returns_user_message(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        def behavior(config, model):
            raise Exception(
                "429 RESOURCE_EXHAUSTED. Please retry in 12s. quota exceeded"
            )

        tutor, _models = self._tutor(behavior)
        tutor._thinking_ok["gemini-test"] = False
        with patch("app.services.tutor.asyncio.sleep", fake_sleep):
            with self.assertRaises(TutorError) as raised:
                await tutor._ask("sys", [], _TutorSchema, 0.4)
        self.assertEqual(raised.exception.user_message, QUOTA_MESSAGE)
        self.assertEqual(sleeps, [12.0, 12.0])

    async def test_fallback_model_used_when_primary_quota(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        def behavior(config, model):
            if model == "gemini-primary":
                raise Exception(
                    "429 RESOURCE_EXHAUSTED. Please retry in 46s. quota exceeded"
                )
            if model == "gemini-3.8-flash":
                raise Exception("503 UNAVAILABLE high demand")
            # Gemma без response_schema
            self.assertIsNone(getattr(config, "response_schema", None))
            return _FakeResponse(
                _TutorSchema(reply="запас", subject="Алгебра", title="т", skill="с")
            )

        tutor = Tutor(
            "test-key",
            "gemini-primary",
            fallback_model="gemini-3.8-flash,gemma-4-31b-it",
        )
        models = _FakeModels(behavior)
        tutor._client = _FakeClient(models)
        tutor._thinking_ok["gemini-primary"] = False
        tutor._thinking_ok["gemini-3.8-flash"] = False
        tutor._thinking_ok["gemma-4-31b-it"] = False
        with patch("app.services.tutor.asyncio.sleep", fake_sleep):
            data = await tutor._ask("sys", [], _TutorSchema, 0.4)
        self.assertEqual(data.reply, "запас")
        self.assertEqual(
            models.models,
            ["gemini-primary", "gemini-3.8-flash", "gemma-4-31b-it"],
        )
        self.assertEqual(sleeps, [])

    async def test_gemma_skips_response_schema(self) -> None:
        seen: dict = {}

        async def generate_content(model, contents, config):
            seen["schema"] = getattr(config, "response_schema", None)
            seen["system"] = getattr(config, "system_instruction", None)
            seen["user"] = contents[0].parts[0].text if contents else ""
            return _FakeResponse(
                _QuizSchema(
                    title="Тема",
                    questions=[
                        _QuestionSchema(
                            prompt=f"Вопрос {index}",
                            options=["1", "2", "3", "4"],
                            correct_index=0,
                            explanation="так",
                        )
                        for index in range(4)
                    ],
                )
            )

        tutor = Tutor("test-key", "gemma-4-31b-it")
        models = _FakeModels(lambda config, model: None)
        models.generate_content = generate_content  # type: ignore[method-assign]
        tutor._client = _FakeClient(models)
        data = await tutor._ask("инструкция", [], _QuizSchema, 0.4)
        self.assertIsNone(seen["schema"])
        self.assertIsNone(seen["system"])
        self.assertIn("инструкция", seen["user"])
        self.assertIn("prompt", seen["user"])
        self.assertEqual(len(data.questions), 4)

    async def test_make_quiz_asks_with_larger_token_budget(self) -> None:
        seen: dict = {}

        async def fake_ask(system, contents, schema, temperature, max_output_tokens=2048):
            seen["max_output_tokens"] = max_output_tokens
            seen["schema"] = schema
            return _QuizSchema(
                title="Тема",
                questions=[
                    _QuestionSchema(
                        prompt=f"Вопрос {index}",
                        options=["1", "2", "3", "4"],
                        correct_index=0,
                        explanation="так",
                    )
                    for index in range(5)
                ],
            )

        tutor = Tutor("test-key", "gemini-test")
        with patch.object(tutor, "_ask", fake_ask):
            title, drafts = await tutor.make_quiz(topic="дроби", errors=None, wanted=5)
        self.assertEqual(title, "Тема")
        self.assertEqual(len(drafts), 5)
        self.assertEqual(seen["max_output_tokens"], QUIZ_OUTPUT_TOKENS)
        self.assertIs(seen["schema"], _QuizSchema)


class FakeTutor:
    enabled = True

    def __init__(self, ids: list[int] | None = None) -> None:
        self.ids = ids or []
        self.calls = 0

    async def pick_notes(self, query: str, cards: list[dict]) -> list[int]:
        self.calls += 1
        return [note_id for note_id in self.ids if any(card["id"] == note_id for card in cards)]

    async def make_quiz(
        self,
        topic: str | None,
        errors: list[dict] | None,
        fallback: str | None = None,
        wanted: int = 6,
        grade: int | None = None,
        exam_goal: str | None = None,
    ):
        self.topic = topic
        self.errors = errors
        self.grade = grade
        self.exam_goal = exam_goal
        title = fallback or topic or "По моим ошибкам"
        if title and len(title) > 80:
            title = title.split("\n", 1)[0][:80]
        return title, [
            QuestionDraft(
                prompt="Сколько будет 2+2?",
                options=["3", "4"],
                correct_index=1,
                explanation="Четыре.",
                skill="сложение",
                subject="Алгебра",
            )
        ]


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
        note_id = await self.db.insert_note(1, "Дроби", "Алгебра", "Общий знаменатель.", "manual")
        self.assertEqual(len(await search_notes(self.db, FakeTutor(), 1, "знаменатель")), 1)
        self.assertTrue(await self.db.delete_note(1, note_id))
        self.assertEqual(await search_notes(self.db, FakeTutor(), 1, "знаменатель"), [])
        self.assertFalse(await self.db.delete_note(2, note_id))

    async def test_update_note_refreshes_search(self) -> None:
        note_id = await self.db.insert_note(1, "Дроби", "Алгебра", "Общий знаменатель.", "manual")
        self.assertFalse(await self.db.update_note(2, note_id, "Чужое", "Физика", "нельзя"))
        original = await self.db.get_note(1, note_id)
        assert original is not None
        self.assertEqual(original["title"], "Дроби")
        self.assertTrue(await self.db.update_note(1, note_id, "Проценты", "Алгебра", "Часть от числа."))
        note = await self.db.get_note(1, note_id)
        assert note is not None
        self.assertEqual(note["title"], "Проценты")
        self.assertEqual(note["subject"], "Алгебра")
        self.assertEqual(note["body"], "Часть от числа.")
        self.assertEqual(await search_notes(self.db, FakeTutor(), 1, "знаменатель"), [])
        hits = await search_notes(self.db, FakeTutor(), 1, "числа")
        self.assertEqual([hit.id for hit in hits], [note_id])

    async def test_pinned_notes_stay_above_newer_ones(self) -> None:
        older = await self.db.insert_note(1, "Дроби", "Алгебра", "Общий знаменатель.", "manual")
        newer = await self.db.insert_note(1, "Проценты", "Алгебра", "Находим часть от числа.", "manual")
        self.assertEqual([row["id"] for row in await self.db.list_notes(1, 0, 10)], [newer, older])
        self.assertTrue(await self.db.set_note_pinned(1, older, True))
        self.assertFalse(await self.db.set_note_pinned(2, older, True))
        listed = await self.db.list_notes(1, 0, 10)
        self.assertEqual([row["id"] for row in listed], [older, newer])
        self.assertEqual([bool(row["pinned"]) for row in listed], [True, False])
        self.assertTrue(await self.db.set_note_pinned(1, older, False))
        self.assertEqual([row["id"] for row in await self.db.list_notes(1, 0, 10)], [newer, older])

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
        summary = await _quiz_summary(self.db, quiz_id)
        self.assertEqual(summary["correct"], 2)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["review"], ["Сколько будет 0?"])
        self.assertIn("Сколько будет 0?", summary["advice"])

        again = await answer_question(self.db, 1, returned["id"], 1)
        self.assertTrue(again.already_closed)
        stranger = await answer_question(self.db, 2, returned["id"], 1)
        self.assertTrue(stranger.missing)

    async def test_cabinet_quiz_hides_the_answer(self) -> None:
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            topic = await client.post("/api/quizzes/topic", json={"topic": "дроби"})
            mistakes = await client.post("/api/quizzes/errors")
            self.assertEqual(topic.status_code, 401)
            self.assertEqual(mistakes.status_code, 401)
            opened = await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            self.assertEqual(opened.status_code, 200)
            empty = await client.post("/api/quizzes/errors")
            self.assertEqual(empty.status_code, 409)
            started = await client.post("/api/quizzes/topic", json={"topic": "дроби"})
            self.assertEqual(started.status_code, 200)
            body = started.json()
            self.assertNotIn("correct_index", started.text)
            self.assertEqual(body["question"]["prompt"], "Сколько будет 2+2?")
            self.assertEqual(body["question"]["options"], ["3", "4"])
            self.assertFalse(body["finished"])
            answered = await client.post(
                "/api/quizzes/answer",
                json={"question_id": body["question"]["id"], "option": 1},
            )
            self.assertEqual(answered.status_code, 200)
            done = answered.json()
            self.assertTrue(done["feedback"]["correct"])
            self.assertEqual(done["feedback"]["correct_index"], 1)
            self.assertTrue(done["finished"])
            self.assertIsNone(done["question"])
            self.assertEqual(done["summary"]["correct"], 1)
            self.assertEqual(done["summary"]["total"], 1)
            self.assertEqual(done["summary"]["strong"], ["сложение"])
            self.assertEqual(done["summary"]["review"], [])
            self.assertIn("Отлично", done["summary"]["advice"])
            wrong = await client.post("/api/quizzes/topic", json={"topic": "дроби"})
            self.assertEqual(wrong.status_code, 200)
            bad = await client.post(
                "/api/quizzes/answer",
                json={"question_id": wrong.json()["question"]["id"], "option": 0},
            )
            self.assertEqual(bad.status_code, 200)
            self.assertFalse(bad.json()["feedback"]["correct"])
            self.assertEqual(bad.json()["feedback"]["correct_index"], 1)

    async def test_cabinet_opens_for_the_signed_student(self) -> None:
        await self.db.set_grade(1, 7)
        await self.db.set_explain_style(1, "detail")
        await self.db.insert_note(
            1,
            "Комбинаторика",
            "Алгебра",
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
            self.assertIn("Личный кабинет", page.text)
            self.assertIn("Открой кабинет кнопкой в чате MAX", page.text)
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
            payload = mistakes.json()
            self.assertIn("counts", payload)
            self.assertIn("items", payload)
            error_rows = payload["items"]
            self.assertEqual([item["mistake"] for item in error_rows], ["приём-деления"])
            self.assertEqual(error_rows[0]["status"], "new")
            self.assertEqual(payload["counts"]["new"], 1)
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
        own = await self.db.insert_note(1, "Свой конспект", "Алгебра", "только Аня", "manual")
        kept = await self.db.insert_note(1, "Второй конспект", "Алгебра", "тоже Аня", "manual")
        foreign = await self.db.insert_note(2, "Чужой конспект", "Физика", "только Боря", "manual")
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            page = await anon.get("/")
            self.assertIn("Личный кабинет", page.text)
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

    async def test_cabinet_edit_belongs_to_the_session(self) -> None:
        own = await self.db.insert_note(1, "Свой конспект", "Алгебра", "только Аня", "manual")
        await self.db.set_note_pinned(1, own, True)
        foreign = await self.db.insert_note(2, "Чужой конспект", "Физика", "только Боря", "manual")
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        edited = {
            "title": "Новый заголовок",
            "subject": "Алгебра",
            "body": "Общий знаменатель больше не нужен.",
        }
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            page = await anon.get("/")
            self.assertIn('id="edit-note"', page.text)
            self.assertIn("Редактировать конспект", page.text)
            self.assertEqual((await anon.put(f"/api/notes/{own}", json=edited)).status_code, 401)
            stored = await self.db.get_note(1, own)
            assert stored is not None
            self.assertEqual(stored["title"], "Свой конспект")
        async with AsyncClient(transport=transport, base_url="https://cabinet") as owner:
            opened = await owner.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            self.assertEqual(opened.status_code, 200)
            missed = await owner.put(f"/api/notes/{foreign}", json=edited)
            self.assertEqual(missed.status_code, 404)
            foreign_note = await self.db.get_note(2, foreign)
            assert foreign_note is not None
            self.assertEqual(foreign_note["body"], "только Боря")
            empty = await owner.put(
                f"/api/notes/{own}",
                json={"title": "  ", "subject": "Алгебра", "body": "текст"},
            )
            self.assertEqual(empty.status_code, 400)
            saved = await owner.put(f"/api/notes/{own}", json=edited)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["title"], "Новый заголовок")
            self.assertEqual(saved.json()["body"], edited["body"])
            note = await self.db.get_note(1, own)
            assert note is not None
            self.assertEqual(note["subject"], "Алгебра")
            listed = await self.db.list_notes(1, 0, 10)
            self.assertTrue(listed[0]["pinned"])
            hits = await search_notes(self.db, FakeTutor(), 1, "знаменатель")
            self.assertEqual([hit.id for hit in hits], [own])
        async with AsyncClient(transport=transport, base_url="https://cabinet") as stranger:
            await stranger.post("/api/session", json={"init_data": _init_data(2, "Боря")})
            blocked = await stranger.put(f"/api/notes/{own}", json=edited)
            self.assertEqual(blocked.status_code, 404)
            kept = await self.db.get_note(1, own)
            assert kept is not None
            self.assertEqual(kept["title"], "Новый заголовок")

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

    async def test_cabinet_profile_keeps_the_saved_name(self) -> None:
        await self.db.set_grade(1, 7)
        await self.db.set_explain_style(1, "detail")
        await self.db.upsert_user(3, "")
        await self.db.upsert_user(3, "Кира")
        self.assertEqual((await self.db.get_user(3))["first_name"], "Кира")
        await self.db.upsert_user(3, "Другое")
        self.assertEqual((await self.db.get_user(3))["first_name"], "Кира")
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            denied = await anon.patch(
                "/api/profile",
                json={"first_name": "Саша", "grade": 6},
            )
            self.assertEqual(denied.status_code, 401)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            opened = await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            self.assertEqual(opened.status_code, 200)
            missing = await client.patch("/api/profile", json={"first_name": "   ", "grade": 6})
            self.assertEqual(missing.status_code, 400)
            bad_grade = await client.patch("/api/profile", json={"first_name": "Саша", "grade": 4})
            self.assertEqual(bad_grade.status_code, 400)
            flagged = await client.patch("/api/profile", json={"first_name": "Саша", "grade": True})
            self.assertEqual(flagged.status_code, 400)
            saved = await client.patch(
                "/api/profile",
                json={"first_name": "  Саша  ", "grade": 6, "exam_goal": "self"},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["first_name"], "Саша")
            self.assertEqual(saved.json()["grade"], 6)
            self.assertEqual(saved.json()["explain_style"], "detail")
            self.assertEqual(saved.json()["exam_goal"], "self")
            self.assertIn("quizzes_done", saved.json())
            self.assertIn("closed_errors", saved.json())
            self.assertIn("accuracy", saved.json())
            profile = await client.get("/api/profile")
            self.assertEqual(profile.json()["first_name"], "Саша")
            self.assertEqual(profile.json()["grade"], 6)
            self.assertEqual(profile.json()["exam_goal"], "self")
        async with AsyncClient(transport=transport, base_url="https://cabinet") as again:
            opened = await again.post("/api/session", json={"init_data": _init_data(1, "Макс")})
            self.assertEqual(opened.status_code, 200)
            profile = await again.get("/api/profile")
            self.assertEqual(profile.json()["first_name"], "Саша")
            self.assertEqual(profile.json()["grade"], 6)

    async def test_cabinet_tour_marks_only_the_session_user(self) -> None:
        self.assertFalse((await self.db.get_user(1))["cabinet_tour"])
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            page = await anon.get("/")
            self.assertIn("Пропустить", page.text)
            self.assertIn("Как устроен кабинет", page.text)
            denied = await anon.post("/api/tour")
            self.assertEqual(denied.status_code, 401)
            self.assertFalse((await self.db.get_user(1))["cabinet_tour"])
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anya:
            opened = await anya.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            self.assertEqual(opened.status_code, 200)
            self.assertFalse((await anya.get("/api/profile")).json()["cabinet_tour"])
            marked = await anya.post("/api/tour")
            self.assertEqual(marked.status_code, 200)
            self.assertTrue(marked.json()["ok"])
            self.assertTrue((await anya.get("/api/profile")).json()["cabinet_tour"])
        self.assertTrue((await self.db.get_user(1))["cabinet_tour"])
        self.assertFalse((await self.db.get_user(2))["cabinet_tour"])
        async with AsyncClient(transport=transport, base_url="https://cabinet") as borya:
            opened = await borya.post("/api/session", json={"init_data": _init_data(2, "Боря")})
            self.assertEqual(opened.status_code, 200)
            self.assertFalse((await borya.get("/api/profile")).json()["cabinet_tour"])

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
        self.assertEqual(grade_payloads, [f"grade:{number}" for number in range(5, 12)])
        self.assertEqual(style_payloads, ["style:short", "style:detail"])
        self.assertNotIn("menu:home", grade_payloads + style_payloads)
        menu_payloads = [
            button.payload for row in menu_keyboard().payload.buttons for button in row
        ]
        self.assertIn("menu:ask", menu_payloads)
        self.assertIn("menu:search", menu_payloads)
        self.assertIn("menu:notes", menu_payloads)
        self.assertNotIn("menu:tour", menu_payloads)
        self.assertNotIn("menu:cabinet", menu_payloads)
        self.assertNotIn("menu:quiz", menu_payloads)
        self.assertNotIn("menu:errors", menu_payloads)
        opened = menu_keyboard(True, "ideya_fiks_60_bot")
        with_cabinet = [
            button.payload for row in opened.payload.buttons for button in row if button.payload
        ]
        self.assertNotIn("menu:cabinet", with_cabinet)
        self.assertIn("menu:ask", with_cabinet)
        self.assertIn("menu:own", with_cabinet)
        self.assertNotIn("menu:tour", with_cabinet)
        self.assertNotIn("menu:search", with_cabinet)
        self.assertNotIn("menu:notes", with_cabinet)
        self.assertNotIn("menu:quiz", with_cabinet)
        self.assertNotIn("menu:errors", with_cabinet)
        dumped = opened.model_dump()["payload"]["buttons"]
        quiz_button = dumped[-2][0]
        self.assertEqual(quiz_button["type"], "open_app")
        self.assertEqual(quiz_button["text"], "Квиз")
        self.assertEqual(quiz_button["web_app"], "ideya_fiks_60_bot")
        self.assertNotIn("contact_id", quiz_button)
        self.assertNotIn("payload", quiz_button)
        cabinet_button = dumped[-1][0]
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
        counts = []
        for stored in edited.attachments:
            open_app = [
                button
                for row in stored.model_dump()["payload"]["buttons"]
                for button in row
                if button["type"] == "open_app"
            ]
            counts.append(len(open_app))
            for button in open_app:
                self.assertNotIn("contact_id", button)
                self.assertNotIn("payload", button)
        self.assertEqual(counts, [2, 1])
        await self.db.set_explain_style(1, "short")
        self.assertIsNone(onboard_step(await self.db.get_user(1)))

    async def test_home_stats_count_open_errors_streak_and_week(self) -> None:
        from zoneinfo import ZoneInfo

        moscow = ZoneInfo("Europe/Moscow")
        today = datetime.now(moscow).date()

        def stamp(day):
            moment = datetime(day.year, day.month, day.day, 15, tzinfo=moscow)
            return moment.astimezone(timezone.utc).isoformat(timespec="seconds")

        await self.db.insert_error(1, "Алгебра", "2x=4", "не разделил", "раздели обе части")
        await self.db.insert_error(1, "Алгебра", "x=1", "знак", "проверь знак")
        resolved = await self.db.insert_error(1, "Алгебра", "x=0", "уже закрыта", "готово")
        await self.db.conn.execute(
            "UPDATE errors SET resolved_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", resolved),
        )
        days = [today, today, today - timedelta(days=1), today - timedelta(days=8)]
        for day in days:
            moment = stamp(day)
            await self.db.conn.execute(
                """
                INSERT INTO quizzes (user_id, title, source, status, started_at, finished_at)
                VALUES (1, 'Квиз', 'topic', 'done', ?, ?)
                """,
                (moment, moment),
            )
        await self.db.conn.commit()

        stats = await self.db.home_stats(1, today)
        week_start = today - timedelta(days=today.weekday())
        self.assertEqual(stats["open_errors"], 2)
        self.assertEqual(stats["streak_days"], 2)
        self.assertEqual(stats["week_goal"], 5)
        self.assertEqual(stats["week_done"], sum(1 for day in days if week_start <= day <= today))
        self.assertEqual((await self.db.home_stats(2, today))["streak_days"], 0)

    async def test_error_bank_and_profile_stats(self) -> None:
        skill_id = await self.db.insert_skill(
            1, "Алгебра", "Квадратные уравнения", 0.4, 2, utcnow(), utcnow()
        )
        await self.db.insert_skill_event(skill_id, "quiz", True)
        await self.db.insert_error(1, "Алгебра", "x²=4", "забыл минус", "два корня", skill_id=skill_id)
        await self.db.insert_error(1, "Геометрия", "треугольник", "углы", "180", skill_id=None)
        closed = await self.db.insert_error(1, "Алгебра", "уже", "закрыта", "ок", skill_id=skill_id)
        await self.db.conn.execute(
            "UPDATE errors SET resolved_at = ? WHERE id = ?",
            (utcnow(), closed),
        )
        await self.db.conn.commit()
        note_id = await self.db.insert_note(1, "Из чата", "Алгебра", "тело", "chat")
        manual_id = await self.db.insert_note(1, "Вручную", "Алгебра", "тело", "manual")
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            bank = await client.get("/api/errors")
            self.assertEqual(bank.status_code, 200)
            data = bank.json()
            self.assertEqual(data["counts"]["closed"], 1)
            self.assertEqual(data["counts"]["active"], 1)
            self.assertEqual(data["counts"]["new"], 1)
            by_status = {item["status"]: item for item in data["items"]}
            self.assertEqual(by_status["active"]["progress"], 1)
            self.assertEqual(by_status["closed"]["progress"], 3)
            self.assertEqual(by_status["new"]["progress"], 0)
            notes = await client.get("/api/notes")
            sources = {row["id"]: row["source"] for row in notes.json()["notes"]}
            self.assertEqual(sources[note_id], "chat")
            self.assertEqual(sources[manual_id], "manual")
            profile = await client.patch(
                "/api/profile",
                json={
                    "first_name": "Аня",
                    "grade": 9,
                    "exam_goal": "oge",
                    "explain_style": "short",
                },
            )
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["exam_goal"], "oge")
            self.assertEqual(profile.json()["closed_errors"], 1)
            stats = await self.db.profile_stats(1)
            self.assertEqual(stats["closed_errors"], 1)


class SkillTests(unittest.TestCase):
    def test_box_and_brightness(self) -> None:
        self.assertEqual(next_box(1, True), 2)
        self.assertEqual(next_box(5, True), 5)
        self.assertEqual(next_box(4, False), 1)
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        seen = (now - timedelta(days=2)).isoformat()
        fresh = brightness(1.0, 5, now.isoformat(), now)
        faded = brightness(1.0, 1, seen, now)
        self.assertGreater(fresh, 0.9)
        self.assertLess(faded, fresh)
        known = [{"id": 3, "name": "деление обеих частей"}]
        exact = _local_skill_match(normalize_name("деление обеих частей"), known)
        soft = _local_skill_match(normalize_name("деление обеих"), known)
        self.assertEqual(exact, (3, "деление обеих частей"))
        self.assertEqual(soft, (3, "деление обеих частей"))
        self.assertIsNone(_local_skill_match(normalize_name("дискриминант"), known))

    def test_quiet_hours(self) -> None:
        noon = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        night = noon.replace(hour=23)
        self.assertFalse(in_quiet_hours(noon))
        self.assertTrue(in_quiet_hours(night))

    def test_rate_limit(self) -> None:
        reset()
        self.assertTrue(all(allow(5, now=float(index)) for index in range(30)))
        self.assertFalse(allow(5, now=31))
        reset()


class SkillGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "tutor.db"))
        await self.db.connect()
        await self.db.upsert_user(1, "Аня")
        await self.db.upsert_user(2, "Боря")
        reset()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()
        reset()

    async def test_graph_empty_for_new_user(self) -> None:
        graph = await build_skill_graph(self.db, 1)
        self.assertEqual(graph, {"nodes": [], "edges": [], "due_count": 0})
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            response = await client.get("/api/skills/graph")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["nodes"], [])

    async def test_graph_links_skill_to_open_error_and_hides_stranger(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        skill_id = await self.db.insert_skill(
            1, "Алгебра", "Квадратные уравнения", 0.2, 1, past, past
        )
        other = await self.db.insert_skill(
            2, "Физика", "Чужой приём", 0.9, 4, utcnow(), utcnow()
        )
        error_id = await self.db.insert_error(
            1, "Алгебра", "x²=4", "забыл минус", "два корня", skill_id=skill_id
        )
        await self.db.insert_error(
            2, "Физика", "чужая", "секрет", "не светить", skill_id=other
        )
        graph = await build_skill_graph(self.db, 1)
        ids = {node["id"] for node in graph["nodes"]}
        self.assertIn("subj:Алгебра", ids)
        self.assertIn(f"skill:{skill_id}", ids)
        self.assertIn(f"error:{error_id}", ids)
        self.assertNotIn(f"skill:{other}", ids)
        edges = {(edge["from"], edge["to"]) for edge in graph["edges"]}
        self.assertIn(("subj:Алгебра", f"skill:{skill_id}"), edges)
        self.assertIn((f"skill:{skill_id}", f"error:{error_id}"), edges)
        skill_node = next(node for node in graph["nodes"] if node["id"] == f"skill:{skill_id}")
        self.assertTrue(skill_node["due"])
        self.assertEqual(skill_node["open_errors"], 1)
        self.assertGreaterEqual(graph["due_count"], 1)

        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            mine = await client.get("/api/skills/graph")
            self.assertEqual(mine.status_code, 200)
            mine_ids = {node["id"] for node in mine.json()["nodes"]}
            self.assertIn(f"skill:{skill_id}", mine_ids)
            self.assertNotIn(f"skill:{other}", mine_ids)

    async def test_skill_and_due_quiz_routes(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        skill_id = await self.db.insert_skill(
            1, "Алгебра", "деление", 0.2, 1, past, past
        )
        await self.db.insert_error(
            1, "Алгебра", "2x=4", "не разделил", "раздели обе части", skill_id=skill_id
        )
        foreign = await self.db.insert_skill(
            2, "Физика", "сила", 0.2, 1, past, past
        )
        quiz_id = await start_skill_quiz(self.db, FakeTutor(), 1, skill_id)
        self.assertIsNotNone(quiz_id)
        missing = await start_skill_quiz(self.db, FakeTutor(), 1, foreign)
        self.assertIsNone(missing)
        due_id = await start_due_quiz(self.db, FakeTutor(), 1)
        self.assertIsNotNone(due_id)

        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            ok = await client.post(f"/api/quizzes/skill/{skill_id}")
            self.assertEqual(ok.status_code, 200)
            self.assertIn("question", ok.json())
            denied = await client.post(f"/api/quizzes/skill/{foreign}")
            self.assertEqual(denied.status_code, 404)
            due = await client.post("/api/quizzes/due")
            self.assertEqual(due.status_code, 200)

        fresh = await self.db.insert_skill(
            1, "Геометрия", "уже крепкий", 1.0, 5, utcnow(),
            (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(timespec="seconds"),
        )
        await self.db.conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        await self.db.conn.execute("DELETE FROM errors WHERE user_id = 1")
        await self.db.conn.commit()
        self.assertIsNotNone(await self.db.get_skill(1, fresh))
        empty = await start_due_quiz(self.db, FakeTutor(), 1)
        self.assertIsNone(empty)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            conflict = await client.post("/api/quizzes/due")
            self.assertEqual(conflict.status_code, 409)
            self.assertIn("нечего", conflict.json()["detail"].lower())


class NoteQuizTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "tutor.db"))
        await self.db.connect()
        await self.db.upsert_user(1, "Аня")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_practice_closes_the_error(self) -> None:
        wrong = CheckResult(
            is_schoolwork=True,
            subject="Алгебра",
            problem_text="2x = 4",
            student_solution_text="x = 4",
            is_correct=False,
            error_step="не разделил",
            hint="Раздели обе части.",
            full_explanation="x = 2",
            skill="деление обеих частей",
            praise="",
        )
        await register_check(self.db, 1, wrong)
        self.assertEqual(await self.db.count_errors(1), 1)
        await record(self.db, 1, "Алгебра", "деление обеих частей", True, "quiz")
        self.assertEqual(await self.db.count_errors(1), 1)
        await record(self.db, 1, "Алгебра", "деление обеих частей", True, "quiz")
        self.assertEqual(await self.db.count_errors(1), 0)

    async def test_note_quiz_and_removed_sky_routes(self) -> None:
        note_id = await self.db.insert_note(
            1,
            "Дроби",
            "Алгебра",
            "Общий знаменатель важен.",
            "manual",
        )
        tutor = FakeTutor()
        app = create_app(self.db, tutor, BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            self.assertEqual((await client.get("/api/sky")).status_code, 404)
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            self.assertEqual((await client.get("/api/sky")).status_code, 404)
            self.assertEqual((await client.get("/api/skills/1")).status_code, 404)
            self.assertEqual((await client.post("/api/quizzes/review", json={})).status_code, 404)
            started = await client.post(f"/api/quizzes/note/{note_id}")
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["title"], "Дроби")
            self.assertIn("Общий знаменатель", tutor.topic)
            foreign = await self.db.insert_note(2, "Чужое", "Физика", "скрыто", "chat")
            self.assertEqual((await client.post(f"/api/quizzes/note/{foreign}")).status_code, 404)
            profile = await client.patch(
                "/api/profile",
                json={
                    "first_name": "Аня",
                    "grade": 8,
                    "exam_goal": "self",
                    "explain_style": "short",
                },
            )
            self.assertEqual(profile.status_code, 200)
            self.assertTrue(profile.json()["reminders"])
            muted = await client.patch(
                "/api/profile",
                json={"first_name": "Аня", "grade": 8, "reminders": False},
            )
            self.assertEqual(muted.status_code, 200)
            self.assertFalse(muted.json()["reminders"])
            self.assertFalse((await client.get("/api/profile")).json()["reminders"])
            broken = await client.patch(
                "/api/profile",
                json={"first_name": "Аня", "grade": 8, "reminders": "нет"},
            )
            self.assertEqual(broken.status_code, 400)

    async def test_wrong_quiz_answer_goes_to_error_bank(self) -> None:
        quiz_id = await self.db.create_quiz(
            1,
            "Производная",
            "topic",
            [
                {
                    "prompt": "Чему равна производная x²?",
                    "options": ["x", "2x", "2", "x²"],
                    "correct_index": 1,
                    "explanation": "Степень умножается и уменьшается на 1.",
                    "skill": "производная степени",
                    "subject": "Алгебра",
                }
            ],
        )
        question = await self.db.next_pending(quiz_id)
        assert question is not None
        wrong = await answer_question(self.db, 1, question["id"], 0)
        self.assertFalse(wrong.correct)
        errors = await self.db.list_errors(1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["source"], "quiz")
        self.assertEqual(errors[0]["mistake"], "производная степени")
        again = await answer_question(self.db, 1, question["id"], 0)
        self.assertFalse(again.correct)
        self.assertEqual(len(await self.db.list_errors(1)), 1)
        self.assertEqual(await self.db.list_errors(2), [])
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            bank = await client.get("/api/errors")
            self.assertEqual(bank.status_code, 200)
            self.assertEqual(bank.json()["items"][0]["source"], "quiz")

    async def test_error_quiz_does_not_spawn_new_bank_rows(self) -> None:
        await self.db.insert_error(1, "Алгебра", "2x=4", "деление", "раздели обе части")
        quiz_id = await self.db.create_quiz(
            1,
            "По ошибкам",
            "errors",
            [
                {
                    "prompt": "2x = 4",
                    "options": ["1", "2", "3", "4"],
                    "correct_index": 1,
                    "explanation": "Раздели обе части на 2.",
                    "skill": "деление",
                    "subject": "Алгебра",
                }
            ],
        )
        question = await self.db.next_pending(quiz_id)
        assert question is not None
        before = await self.db.count_errors(1)
        wrong = await answer_question(self.db, 1, question["id"], 0)
        self.assertFalse(wrong.correct)
        self.assertEqual(await self.db.count_errors(1), before)

    async def test_old_errors_table_gains_source_columns(self) -> None:
        path = Path(self._tmp.name) / "legacy.db"
        import aiosqlite

        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                CREATE TABLE errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    problem TEXT NOT NULL,
                    mistake TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    skill_id INTEGER,
                    resolved_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            await conn.commit()
        legacy = Database(str(path))
        await legacy.connect()
        await legacy.upsert_user(1, "Аня")
        error_id = await legacy.insert_error(
            1, "Алгебра", "задача", "ошибка", "подсказка", source="quiz", question_id=7
        )
        self.assertTrue(await legacy.has_error_for_question(1, 7))
        rows = await legacy.list_error_bank(1)
        self.assertEqual(rows[0]["id"], error_id)
        self.assertEqual(rows[0]["source"], "quiz")
        await legacy.close()


class SubjectTests(unittest.TestCase):
    def test_synonyms_collapse_to_one_subject(self) -> None:
        self.assertEqual(normalize_subject("Алгебра"), "Алгебра")
        self.assertEqual(normalize_subject("  математика 8 класс "), "Алгебра")
        self.assertEqual(normalize_subject("Геометрия"), "Геометрия")
        self.assertEqual(normalize_subject("русский"), "Русский язык")
        self.assertEqual(normalize_subject("Английский язык"), "Английский")
        self.assertEqual(normalize_subject("русская литература"), "Литература")
        self.assertEqual(normalize_subject(""), "Другое")
        self.assertEqual(normalize_subject("Свой конспект"), "Другое")
        self.assertEqual(normalize_subject("Школьный предмет"), "Другое")
        self.assertEqual(normalize_subject("астрономия"), "Астрономия")

    def test_manual_title_cuts_by_word(self) -> None:
        self.assertEqual(manual_title("Закон Ома\nU = IR"), "Закон Ома")
        long_line = "Квадратные уравнения решаются через дискриминант " * 4
        title = manual_title(long_line)
        self.assertLessEqual(len(title), 80)
        self.assertTrue(title.endswith("…"))
        self.assertTrue(long_line.startswith(title[:-1]))
        self.assertEqual(manual_title("   \n  "), "Свой конспект")

    def test_tutor_reply_is_clipped_and_normalized(self) -> None:
        reply = Tutor("", "gemini-test")._to_reply(
            _TutorSchema(
                subject="алгебра",
                title="т" * 200,
                reply="а" * 2000,
                full_solution="б" * 2000,
                skill="с" * 200,
            )
        )
        self.assertEqual(reply.subject, "Алгебра")
        self.assertLessEqual(len(reply.reply), 1200)
        self.assertLessEqual(len(reply.full_solution), 1500)
        self.assertEqual(len(reply.title), 80)
        self.assertEqual(len(reply.skill), 80)

    def test_reminder_text_counts_open_errors(self) -> None:
        self.assertIsNone(reminder_text(0))
        self.assertIn("1 незакреплённая ошибка", reminder_text(1) or "")
        self.assertIn("3 незакреплённые ошибки", reminder_text(3) or "")
        self.assertIn("12 незакреплённых ошибок", reminder_text(12) or "")


class PlanFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "tutor.db")
        self.db = Database(self.path)
        await self.db.connect()
        await self.db.upsert_user(1, "Аня")
        await self.db.upsert_user(2, "Боря")

    async def asyncTearDown(self) -> None:
        reset()
        await self.db.close()
        self._tmp.cleanup()

    @asynccontextmanager
    async def _client(self, tutor=None, **kwargs) -> AsyncIterator[AsyncClient]:
        app = create_app(self.db, tutor or FakeTutor(), BOT_TOKEN, **kwargs)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            yield client

    async def test_old_subjects_are_normalized_on_connect(self) -> None:
        note_id = await self.db.insert_note(1, "Дроби", "Алгебра", "Общий знаменатель.", "manual")
        await self.db.insert_error(1, "алгебра", "2x=4", "деление", "раздели")
        own = await self.db.insert_note(1, "Своё", "Свой конспект", "текст", "manual")
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        self.assertEqual((await self.db.get_note(1, note_id))["subject"], "Алгебра")
        self.assertEqual((await self.db.get_note(1, own))["subject"], "Другое")
        self.assertEqual((await self.db.list_error_bank(1))[0]["subject"], "Алгебра")
        hits = await search_notes(self.db, FakeTutor(), 1, "знаменатель")
        self.assertEqual(hits[0].subject, "Алгебра")

    async def test_manual_note_keeps_subject_and_title(self) -> None:
        plain = await save_manual(self.db, 1, "Закон Ома\nU = IR")
        stored = await self.db.get_note(1, plain)
        self.assertEqual(stored["subject"], "Другое")
        self.assertEqual(stored["title"], "Закон Ома")
        async with self._client() as client:
            created = await client.post(
                "/api/notes",
                json={"text": "Сила тока равна U/R.", "title": "Закон Ома", "subject": "физика"},
            )
            self.assertEqual(created.status_code, 200)
            self.assertEqual(created.json()["subject"], "Физика")
            self.assertEqual(created.json()["title"], "Закон Ома")
            fallback = await client.post("/api/notes", json={"text": "Первая строка\nтекст"})
            self.assertEqual(fallback.json()["title"], "Первая строка")
            self.assertEqual(fallback.json()["subject"], "Другое")
            broken = await client.post("/api/notes", json={"text": "текст", "subject": 5})
            self.assertEqual(broken.status_code, 400)
            detail = await client.get(f"/api/notes/{created.json()['id']}")
            self.assertEqual(detail.json()["source"], "manual")
            self.assertFalse(detail.json()["pinned"])

    async def test_semantic_search_spends_the_limit(self) -> None:
        note_id = await self.db.insert_note(1, "Дроби", "Алгебра", "Общий знаменатель.", "manual")
        tutor = FakeTutor([note_id])
        async with self._client(tutor) as client:
            while allow(1):
                pass
            exact = await client.get("/api/search", params={"q": "знаменатель"})
            self.assertEqual(exact.status_code, 200)
            self.assertEqual(len(exact.json()), 1)
            limited = await client.get("/api/search", params={"q": "как складывать части"})
            self.assertEqual(limited.status_code, 429)
            self.assertIn("через час", limited.json()["detail"])
            self.assertEqual(tutor.calls, 0)

    async def test_quiz_uses_grade_and_goal(self) -> None:
        await self.db.set_profile(1, "Аня", 9, exam_goal="oge")
        tutor = FakeTutor()
        await start_topic_quiz(self.db, tutor, 1, "дроби")
        self.assertEqual(tutor.grade, 9)
        self.assertEqual(tutor.exam_goal, "oge")
        await self.db.insert_error(1, "Алгебра", "2x=4", "деление", "раздели")
        await start_error_quiz(self.db, tutor, 1)
        self.assertEqual(tutor.grade, 9)

    async def test_quiz_answer_reports_asked_and_retry(self) -> None:
        async with self._client() as client:
            started = (await client.post("/api/quizzes/topic", json={"topic": "дроби"})).json()
            self.assertEqual(started["asked"], 0)
            self.assertFalse(started["question"]["retry"])
            wrong = (
                await client.post(
                    "/api/quizzes/answer",
                    json={"question_id": started["question"]["id"], "option": 0},
                )
            ).json()
            self.assertEqual(wrong["asked"], 1)
            self.assertEqual(wrong["done"], 0)
            self.assertTrue(wrong["question"]["retry"])

    async def test_error_bank_progress_in_one_query(self) -> None:
        skill_id = await self.db.insert_skill(1, "Алгебра", "деление", 0.4, 2, utcnow(), utcnow())
        for correct in (True, False, True, True):
            await self.db.insert_skill_event(skill_id, "quiz", correct)
        await self.db.insert_error(1, "Алгебра", "2x=4", "не разделил", "СКРЫТЫЙ РАЗБОР", skill_id)
        await self.db.insert_error(1, "Физика", "U=IR", "перепутал", "разбор")
        foreign = await self.db.insert_skill(2, "Алгебра", "чужой", 0.4, 2, utcnow(), utcnow())
        await self.db.insert_error(2, "Алгебра", "чужое", "чужое", "чужое", foreign)
        async with self._client() as client:
            data = (await client.get("/api/errors")).json()
        rows = {row["problem"]: row for row in data["items"]}
        self.assertEqual(set(rows), {"2x=4", "U=IR"})
        self.assertEqual(rows["2x=4"]["progress"], 2)
        self.assertEqual(rows["2x=4"]["status"], "active")
        self.assertEqual(rows["2x=4"]["skill"], "деление")
        self.assertEqual(rows["U=IR"]["status"], "new")
        self.assertEqual(data["counts"], {"new": 1, "active": 1, "closed": 0})
        self.assertNotIn("СКРЫТЫЙ РАЗБОР", json.dumps(data, ensure_ascii=False))

    async def test_reminders_only_for_open_errors_once_a_day(self) -> None:
        await self.db.set_chat(1, 100)
        await self.db.set_chat(2, 200)
        await self.db.insert_error(1, "Алгебра", "2x=4", "деление", "раздели")
        resolved = await self.db.insert_error(2, "Физика", "U=IR", "формула", "разбор")
        await self.db.conn.execute("UPDATE errors SET resolved_at = ? WHERE id = ?", (utcnow(), resolved))
        await self.db.conn.commit()
        due = await self.db.users_to_remind("2026-11-04")
        self.assertEqual([(row["user_id"], row["open_errors"]) for row in due], [(1, 1)])
        await self.db.mark_reminded(1, "2026-11-04")
        self.assertEqual(await self.db.users_to_remind("2026-11-04"), [])
        self.assertEqual(len(await self.db.users_to_remind("2026-11-05")), 1)
        await self.db.set_reminders(1, False)
        self.assertEqual(await self.db.users_to_remind("2026-11-05"), [])
        quiet = datetime(2026, 11, 4, 22, 30, tzinfo=MOSCOW)
        self.assertTrue(in_quiet_hours(quiet))
        self.assertFalse(in_quiet_hours(quiet.replace(hour=12)))

    async def test_streak_counts_checks_and_notes_and_goal_is_configurable(self) -> None:
        today = datetime.now(MOSCOW).date()
        yesterday = datetime.combine(today - timedelta(days=1), datetime.min.time(), MOSCOW).replace(hour=15)
        await self.db.insert_note(1, "Дроби", "Алгебра", "текст", "manual")
        await self.db.insert_check(1, {"subject": "Алгебра", "is_correct": True})
        await self.db.conn.execute(
            "UPDATE checks SET created_at = ? WHERE user_id = 1",
            (yesterday.astimezone(timezone.utc).isoformat(timespec="seconds"),),
        )
        await self.db.conn.commit()
        stats = await self.db.home_stats(1, today, 7)
        self.assertEqual(stats["streak_days"], 2)
        self.assertEqual(stats["week_goal"], 7)
        self.assertEqual((await self.db.home_stats(2, today))["streak_days"], 0)
        async with self._client(week_goal=7) as client:
            self.assertEqual((await client.get("/api/home")).json()["week_goal"], 7)

    async def test_calendar_link_token_opens_without_cookie(self) -> None:
        await self.db.insert_error(1, "Алгебра", "2x=4", "деление", "разбор")
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            linked = await client.post("/api/calendar/link")
            self.assertEqual(linked.status_code, 200)
            token = linked.json()["token"]
            self.assertTrue(token)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            first = await anon.get("/api/calendar.ics", params={"t": token})
            self.assertEqual(first.status_code, 200)
            self.assertIn("text/calendar", first.headers["content-type"])
            self.assertIn("inline", first.headers["content-disposition"])
            self.assertIn("2x=4", first.text)
            again = await anon.get("/api/calendar.ics", params={"t": token})
            self.assertEqual(again.status_code, 200)
            self.assertIn("2x=4", again.text)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as empty:
            await empty.post("/api/session", json={"init_data": _init_data(3, "Вася")})
            self.assertEqual((await empty.post("/api/calendar/link")).status_code, 409)

    async def test_calendar_export_keeps_owner_events(self) -> None:
        noon = datetime(2026, 11, 5, 12, 0, tzinfo=MOSCOW)
        skill_id = await self.db.insert_skill(
            1,
            "Алгебра",
            "деление дробей",
            0.4,
            2,
            utcnow(),
            (noon + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds"),
        )
        await self.db.insert_error(
            1,
            "Алгебра",
            "Уравнение, 2x=4",
            "не разделил",
            "СКРЫТЫЙ РАЗБОР",
            skill_id,
        )
        closed = await self.db.insert_error(1, "Физика", "закрытая", "формула", "разбор")
        await self.db.conn.execute(
            "UPDATE errors SET resolved_at = ? WHERE id = ?",
            (utcnow(), closed),
        )
        await self.db.conn.commit()
        overdue = await self.db.insert_skill(
            1,
            "Геометрия",
            "площадь треугольника",
            0.2,
            1,
            utcnow(),
            (noon - timedelta(days=2)).astimezone(timezone.utc).isoformat(timespec="seconds"),
        )
        future = await self.db.insert_skill(
            1,
            "Алгебра",
            "квадрат числа",
            0.6,
            3,
            utcnow(),
            (noon + timedelta(days=3)).astimezone(timezone.utc).isoformat(timespec="seconds"),
        )
        await self.db.insert_error(2, "Физика", "чужая задача", "секрет Бори", "чужой разбор")
        await self.db.insert_skill(
            2,
            "Физика",
            "чужой навык",
            0.4,
            2,
            utcnow(),
            (noon + timedelta(days=3)).astimezone(timezone.utc).isoformat(timespec="seconds"),
        )

        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as anon:
            self.assertEqual((await anon.get("/api/calendar.ics")).status_code, 401)

        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            await client.post("/api/session", json={"init_data": _init_data(1, "Аня")})
            with patch("app.web.moscow_now", return_value=noon):
                response = await client.get("/api/calendar.ics")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/calendar", response.headers["content-type"])
            disposition = response.headers["content-disposition"]
            self.assertIn("inline", disposition)
            self.assertIn("lumi-povtoreniya.ics", disposition)
            body = response.text
            self.assertIn("SUMMARY:Разобрать: деление дробей", body)
            self.assertIn("Уравнение\\, 2x=4", body)
            self.assertNotIn("закрытая", body)
            self.assertNotIn("чужая задача", body)
            self.assertNotIn("чужой навык", body)
            self.assertNotIn("СКРЫТЫЙ РАЗБОР", body)
            self.assertIn("UID:lumi-error-", body)
            self.assertIn(f"UID:lumi-skill-{overdue}@tutor", body)
            self.assertIn(f"UID:lumi-skill-{future}@tutor", body)
            self.assertNotIn(f"UID:lumi-skill-{skill_id}@tutor", body)
            self.assertIn("DTSTART:20261105T160000Z", body)
            self.assertIn("DTSTART:20261106T160000Z", body)
            self.assertIn("DTSTART:20261108T160000Z", body)
            self.assertIn("Повторить: площадь треугольника", body)
            self.assertIn("Повторить: квадрат числа", body)

        async with AsyncClient(transport=transport, base_url="https://cabinet") as stranger:
            await stranger.post("/api/session", json={"init_data": _init_data(2, "Боря")})
            other = await stranger.get("/api/calendar.ics")
            self.assertEqual(other.status_code, 200)
            self.assertIn("чужая задача", other.text)
            self.assertNotIn("деление дробей", other.text)

        async with AsyncClient(transport=transport, base_url="https://cabinet") as blank:
            await blank.post("/api/session", json={"init_data": _init_data(3, "Вася")})
            empty = await blank.get("/api/calendar.ics")
            self.assertEqual(empty.status_code, 409)
            self.assertEqual(empty.json()["detail"], EMPTY_CALENDAR)

    def test_calendar_builder_escapes_and_slots(self) -> None:
        noon = datetime(2026, 11, 5, 12, 0, tzinfo=MOSCOW)
        self.assertEqual(next_evening_slot(noon).hour, 19)
        late = datetime(2026, 11, 5, 19, 30, tzinfo=MOSCOW)
        self.assertEqual(next_evening_slot(late).day, 6)
        payload = build_calendar(
            [
                {
                    "id": 12,
                    "subject": "Алгебра",
                    "problem": "Сложи a, b",
                    "mistake": "знаки",
                    "skill_name": "сложение",
                    "skill_id": 5,
                    "resolved_at": None,
                }
            ],
            [
                {
                    "id": 5,
                    "subject": "Алгебра",
                    "name": "сложение",
                    "due_at": (noon + timedelta(days=2)).isoformat(timespec="seconds"),
                },
                {
                    "id": 9,
                    "subject": "Геометрия",
                    "name": "углы",
                    "due_at": (noon - timedelta(days=1)).isoformat(timespec="seconds"),
                },
            ],
            noon,
        )
        assert payload is not None
        self.assertIn("SUMMARY:Разобрать: сложение", payload)
        self.assertIn("Сложи a\\, b", payload)
        self.assertIn("UID:lumi-error-12@tutor", payload)
        self.assertIn("UID:lumi-skill-9@tutor", payload)
        self.assertNotIn("UID:lumi-skill-5@tutor", payload)
        self.assertIsNone(build_calendar([], [], noon))


class RenderCleanupTests(unittest.TestCase):
    def test_render_has_no_star_lines(self) -> None:
        result = CheckResult(
            is_schoolwork=True,
            subject="Алгебра",
            problem_text="2x=4",
            student_solution_text="x=4",
            is_correct=False,
            error_step="деление",
            hint="Раздели обе части.",
            full_explanation="x=2",
            skill="деление обеих частей",
            praise="",
        )
        self.assertNotIn("Звезда", render_check(result))
        view = AnswerView(
            correct=False,
            already_closed=False,
            closed_quiz=False,
            missing=False,
            explanation="Смотри на степень.",
            finished=False,
            done=0,
            total=1,
            elapsed="1 сек",
            quiz_title="Производная",
            next_question=None,
        )
        self.assertNotIn("Звезда", render_feedback(view))


class DemoCabinetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "tutor.db"))
        await self.db.connect()
        await self.db.upsert_user(1, "Аня")
        await self.db.insert_note(1, "Секрет Бори", "Физика", "Чужой конспект.", "manual")

    async def asyncTearDown(self) -> None:
        reset()
        await self.db.close()
        self._tmp.cleanup()

    async def test_demo_page_opens_filled_cabinet_without_init_data(self) -> None:
        app = create_app(self.db, FakeTutor(), BOT_TOKEN)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            home = await client.get("/")
            self.assertNotIn('name="lumi-demo"', home.text)
            page = await client.get("/demo")
            self.assertEqual(page.status_code, 200)
            self.assertIn('name="lumi-demo"', page.text)
            self.assertIn('id="gate" class="gate" hidden', page.text)
            self.assertEqual((await client.get("/demo/")).status_code, 200)
            self.assertEqual((await client.get("/api/profile")).status_code, 401)

            opened = await client.post("/api/demo/session", json={"user_id": 1, "init_data": "nope"})
            self.assertEqual(opened.status_code, 200)
            cookie = opened.headers["set-cookie"]
            self.assertIn("cabinet=", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)

            profile = await client.get("/api/profile")
            self.assertEqual(profile.status_code, 200)
            body = profile.json()
            self.assertEqual(body["first_name"], "Маша")
            self.assertEqual(body["grade"], 9)
            self.assertEqual(body["exam_goal"], "oge")
            self.assertEqual(body["explain_style"], "detail")
            self.assertFalse(body["cabinet_tour"])
            await client.post("/api/tour")
            self.assertTrue((await client.get("/api/profile")).json()["cabinet_tour"])
            await client.post("/api/demo/session")
            self.assertFalse((await client.get("/api/profile")).json()["cabinet_tour"])
            self.assertEqual(body["quizzes_done"], 4)
            self.assertEqual(body["closed_errors"], 3)
            self.assertEqual(body["accuracy"], 80)
            self.assertEqual(body["streak_days"], 6)
            self.assertNotIn("user_id", body)

            home_stats = await client.get("/api/home")
            self.assertEqual(home_stats.json()["open_errors"], 6)
            self.assertEqual(home_stats.json()["streak_days"], 6)
            self.assertEqual(home_stats.json()["week_done"], 4)

            notes = await client.get("/api/notes")
            self.assertEqual(notes.json()["total"], 10)
            self.assertEqual(notes.json()["notes"][0]["title"], DEMO_NOTE)
            self.assertTrue(notes.json()["notes"][0]["pinned"])
            self.assertNotIn("Секрет", notes.text)

            bank = await client.get("/api/errors")
            self.assertEqual(bank.json()["counts"], {"new": 3, "active": 3, "closed": 3})
            graph = await client.get("/api/skills/graph")
            self.assertGreaterEqual(graph.json()["due_count"], 4)
            self.assertTrue(any(node["kind"] == "error" for node in graph.json()["nodes"]))
            self.assertNotIn("Секрет", graph.text)

            found = await client.get("/api/search", params={"q": "Пифагора"})
            self.assertEqual(found.status_code, 200)
            self.assertIn("Теорема Пифагора", [item["title"] for item in found.json()])

            again = await client.post("/api/demo/session")
            self.assertEqual(again.status_code, 200)
            self.assertEqual((await client.get("/api/notes")).json()["total"], 10)

        own = await self.db.list_notes(1, 0, 10)
        self.assertEqual([row["title"] for row in own], ["Секрет Бори"])
        reminded = await self.db.users_to_remind("2099-01-01")
        self.assertFalse(any(int(row["user_id"]) == DEMO_USER_ID for row in reminded))

    async def test_demo_account_stays_available_on_allowlist(self) -> None:
        app = create_app(self.db, FakeTutor(), BOT_TOKEN, {1})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://cabinet") as client:
            opened = await client.post("/api/demo/session")
            self.assertEqual(opened.status_code, 200)
            profile = await client.get("/api/profile")
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["first_name"], "Маша")
            blocked = await client.post("/api/session", json={"init_data": _init_data(99, "Гость")})
            self.assertEqual(blocked.status_code, 403)

        await self.db.conn.execute(
            "DELETE FROM notes WHERE user_id = ? AND title = ?",
            (DEMO_USER_ID, DEMO_NOTE),
        )
        await self.db.conn.commit()
        await ensure_demo(self.db)
        titles = {row["title"] for row in await self.db.list_notes(DEMO_USER_ID, 0, 100)}
        self.assertIn(DEMO_NOTE, titles)
        self.assertEqual(len(titles), 10)
        own = await self.db.list_notes(1, 0, 10)
        self.assertEqual(own[0]["title"], "Секрет Бори")


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
