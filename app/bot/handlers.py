import logging

from maxapi.enums.attachment import AttachmentType
from maxapi.enums.parse_mode import ParseMode
from maxapi.types import BotStarted, MessageCallback, MessageCreated

from app.bot.keyboards import (
    check_keyboard,
    menu_keyboard,
    note_keyboard,
    notes_keyboard,
    quiz_keyboard,
    saved_keyboard,
    search_keyboard,
    tutor_keyboard,
)
from app.bot.render import (
    ASK_PROMPT,
    CHECK_PROMPT,
    EMPTY_ERRORS,
    OWN_PROMPT,
    QUIZ_PROMPT,
    SEARCH_PROMPT,
    WELCOME,
    option_count,
    render_check,
    render_feedback,
    render_finished,
    render_note,
    render_notes_page,
    render_question,
    render_search,
)
from app.db import Database
from app.services.notes import PAGE_SIZE, register_check, save_check, save_manual, save_reply, search_notes
from app.services.quiz import answer_question, ensure_topic, start_error_quiz, start_topic_quiz
from app.services.tutor import Tutor, TutorError, TutorReply
from app.textutil import check_prefix, clip, esc, image_mime, parse_command

log = logging.getLogger(__name__)


class BotApp:
    def __init__(self, bot, db: Database, tutor: Tutor) -> None:
        self.bot = bot
        self.db = db
        self.tutor = tutor

    def register(self, dp) -> None:
        dp.bot_started()(self.on_started)
        dp.message_created()(self.on_message)
        dp.message_callback()(self.on_callback)

    async def on_started(self, event: BotStarted) -> None:
        user = event.user
        if user is None:
            return
        await self.db.upsert_user(user.user_id, user.first_name or "")
        await self.db.set_mode(user.user_id, "chat")
        await self.send(event.chat_id, user.user_id, WELCOME, menu_keyboard())

    async def on_message(self, event: MessageCreated) -> None:
        try:
            await self._on_message(event)
        except Exception:
            log.exception("message handler failed")
            chat_id, user_id, _ = self._person(event)
            if user_id is not None:
                await self.send(
                    chat_id,
                    user_id,
                    "Не получилось обработать сообщение. Попробуйте ещё раз.",
                    menu_keyboard(),
                )

    async def on_callback(self, event: MessageCallback) -> None:
        try:
            await self._on_callback(event)
        except Exception:
            log.exception("callback handler failed")
            _, user_id = event.get_ids()
            await self._notify(event, "Не получилось выполнить действие")
            if user_id:
                chat_id, _ = event.get_ids()
                await self.send(
                    chat_id,
                    user_id,
                    "Не получилось выполнить действие. Откройте меню и попробуйте ещё раз.",
                    menu_keyboard(),
                )

    async def _on_message(self, event: MessageCreated) -> None:
        chat_id, user_id, first_name = self._person(event)
        if user_id is None:
            return
        await self.db.upsert_user(user_id, first_name)
        body = event.message.body
        text = (body.text or "").strip() if body else ""
        image_url = self._image_url(event)
        if image_url and not parse_command(text):
            await self._handle_photo(chat_id, user_id, image_url, text)
            return
        if not text:
            return
        command = parse_command(text)
        if command is not None:
            await self._handle_command(chat_id, user_id, command[0], command[1])
            return
        prefixed = check_prefix(text)
        if prefixed is not None:
            await self.db.set_mode(user_id, "chat")
            if not prefixed:
                await self.send(chat_id, user_id, "После «проверь» напишите решение.", menu_keyboard())
                return
            await self._handle_check_text(chat_id, user_id, prefixed)
            return
        session = await self.db.get_session(user_id)
        mode = session["mode"]
        if mode == "search":
            await self.db.set_mode(user_id, "chat")
            await self._handle_search(chat_id, user_id, text)
            return
        if mode == "quiz_topic":
            await self.db.set_mode(user_id, "chat")
            await self._handle_quiz_topic(chat_id, user_id, text)
            return
        if mode == "check":
            await self.db.set_mode(user_id, "chat")
            await self._handle_check_text(chat_id, user_id, text)
            return
        if mode == "save_note":
            await self.db.set_mode(user_id, "chat")
            await self._handle_own_note(chat_id, user_id, text)
            return
        await self._handle_tutor(chat_id, user_id, text)

    async def _on_callback(self, event: MessageCallback) -> None:
        chat_id, user_id = event.get_ids()
        if user_id is None:
            return
        name = ""
        if event.callback.user is not None:
            name = event.callback.user.first_name or ""
        await self.db.upsert_user(user_id, name)
        payload = event.callback.payload or ""
        if payload == "menu:home":
            await self.db.set_mode(user_id, "chat")
            await self.show(event, WELCOME, menu_keyboard())
            return
        if payload == "menu:ask":
            await self.db.set_mode(user_id, "chat")
            await self.show(event, ASK_PROMPT, menu_keyboard())
            return
        if payload == "menu:check":
            await self.db.set_mode(user_id, "check")
            await self.show(event, CHECK_PROMPT, menu_keyboard())
            return
        if payload == "menu:search":
            await self.db.set_mode(user_id, "search")
            await self.show(event, SEARCH_PROMPT, menu_keyboard())
            return
        if payload == "menu:quiz":
            await self.db.set_mode(user_id, "quiz_topic")
            await self.show(event, QUIZ_PROMPT, menu_keyboard())
            return
        if payload == "menu:own":
            await self.db.set_mode(user_id, "save_note")
            await self.show(event, OWN_PROMPT, menu_keyboard())
            return
        if payload == "menu:notes":
            await self._show_notes(event, user_id, 0)
            return
        if payload == "menu:errors":
            await self._start_error_quiz(event, chat_id, user_id)
            return
        if payload.startswith("note:page:"):
            page = _payload_int(payload, 2)
            if page is None or page < 0:
                return
            await self._show_notes(event, user_id, page)
            return
        if payload.startswith("note:open:"):
            note_id = _payload_int(payload, 2)
            if note_id is None:
                return
            await self._open_note(event, user_id, note_id)
            return
        if payload.startswith("note:del:"):
            note_id = _payload_int(payload, 2)
            if note_id is None:
                return
            deleted = await self.db.delete_note(user_id, note_id)
            notice = "Конспект удалён" if deleted else "Конспект уже удалён"
            await self._show_notes(event, user_id, 0, notification=notice)
            return
        if payload == "tutor:save":
            await self._save_tutor(event, chat_id, user_id)
            return
        if payload == "tutor:simpler":
            await self._simpler(event, chat_id, user_id)
            return
        if payload == "tutor:similar":
            await self._similar_tutor(event, chat_id, user_id)
            return
        if payload == "tutor:solution":
            await self._show_tutor_solution(event, chat_id, user_id)
            return
        if payload.startswith("check:reveal:"):
            await self._reveal_check(event, chat_id, user_id, _payload_int(payload, 2))
            return
        if payload.startswith("check:save:"):
            await self._save_check_note(event, chat_id, user_id, _payload_int(payload, 2))
            return
        if payload.startswith("check:similar:"):
            await self._similar_check(event, chat_id, user_id, _payload_int(payload, 2))
            return
        if payload.startswith("quiz:"):
            question_id = _payload_int(payload, 1)
            option = _payload_int(payload, 2)
            if question_id is None or option is None:
                return
            await self._answer_quiz(event, user_id, question_id, option)

    async def _handle_command(self, chat_id, user_id: int, command: str, argument: str) -> None:
        if command in {"/start", "/menu"}:
            await self.db.set_mode(user_id, "chat")
            await self.send(chat_id, user_id, WELCOME, menu_keyboard())
            return
        if command == "/notes":
            await self.db.set_mode(user_id, "chat")
            await self._send_notes(chat_id, user_id, 0)
            return
        if command == "/search":
            if argument:
                await self.db.set_mode(user_id, "chat")
                await self._handle_search(chat_id, user_id, argument)
                return
            await self.db.set_mode(user_id, "search")
            await self.send(chat_id, user_id, SEARCH_PROMPT, menu_keyboard())
            return
        if command == "/quiz":
            if argument:
                await self.db.set_mode(user_id, "chat")
                await self._handle_quiz_topic(chat_id, user_id, argument)
                return
            await self.db.set_mode(user_id, "quiz_topic")
            await self.send(chat_id, user_id, QUIZ_PROMPT, menu_keyboard())
            return
        if command == "/check":
            await self.db.set_mode(user_id, "check")
            await self.send(chat_id, user_id, CHECK_PROMPT, menu_keyboard())
            return
        if command == "/errors":
            await self.db.set_mode(user_id, "chat")
            await self._send_error_quiz(chat_id, user_id)
            return
        await self.send(chat_id, user_id, "Такой команды нет. Выберите действие в меню.", menu_keyboard())

    async def _handle_tutor(self, chat_id, user_id: int, text: str) -> None:
        await self.send(chat_id, user_id, "Разбираю вопрос.", None)
        history = await self.db.recent_messages(user_id)
        try:
            reply = await self.tutor.answer(history, text)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self._remember_reply(user_id, text, reply)
        await self.send(chat_id, user_id, esc(reply.reply), tutor_keyboard(bool(reply.full_solution)))

    async def _handle_check_text(self, chat_id, user_id: int, text: str) -> None:
        await self.send(chat_id, user_id, "Проверяю решение.", None)
        try:
            result = await self.tutor.check_text(text)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self._present_check(chat_id, user_id, result)

    async def _handle_photo(self, chat_id, user_id: int, url: str, caption: str) -> None:
        await self.send(chat_id, user_id, "Смотрю решение.", None)
        try:
            data = await self.bot.download_bytes(url)
        except Exception:
            log.exception("photo download failed")
            await self.send(
                chat_id,
                user_id,
                "Не смогла скачать фото. Пришлите его ещё раз.",
                menu_keyboard(),
            )
            return
        if len(data) > 8 * 1024 * 1024:
            await self.send(
                chat_id,
                user_id,
                "Фото слишком большое. Пришлите снимок до 8 МБ, лучше JPG или PNG.",
                menu_keyboard(),
            )
            return
        try:
            result = await self.tutor.check_image(data, image_mime(data), caption)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self.db.set_mode(user_id, "chat")
        await self._present_check(chat_id, user_id, result)

    async def _present_check(self, chat_id, user_id: int, result) -> None:
        check_id = await register_check(self.db, user_id, result)
        text = render_check(result)
        if check_id is None:
            await self.send(chat_id, user_id, text, menu_keyboard())
            return
        summary = result.hint or result.praise or result.problem_text or "Проверка решения"
        await self.db.add_message(user_id, "user", "Проверка решения")
        await self.db.add_message(user_id, "assistant", summary)
        await self.send(chat_id, user_id, text, check_keyboard(check_id, result.is_correct))

    async def _handle_search(self, chat_id, user_id: int, query: str) -> None:
        try:
            hits = await search_notes(self.db, self.tutor, user_id, query)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        text = render_search(query, hits)
        if not hits and not self.tutor.enabled and await self.db.count_notes(user_id):
            text += (
                "\n\n"
                + esc("Точного слова нет. Смысловой поиск включится после GEMINI_API_KEY в .env.")
            )
        await self.send(chat_id, user_id, text, search_keyboard(hits))

    async def _handle_quiz_topic(self, chat_id, user_id: int, topic: str) -> None:
        try:
            cleaned = ensure_topic(topic)
        except TutorError as error:
            await self.db.set_mode(user_id, "quiz_topic")
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self.send(chat_id, user_id, "Собираю квиз по теме.", None)
        try:
            quiz_id = await start_topic_quiz(self.db, self.tutor, user_id, cleaned)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self._send_quiz(chat_id, user_id, quiz_id)

    async def _handle_own_note(self, chat_id, user_id: int, text: str) -> None:
        if not text.strip():
            await self.db.set_mode(user_id, "save_note")
            await self.send(chat_id, user_id, OWN_PROMPT, menu_keyboard())
            return
        note_id = await save_manual(self.db, user_id, text)
        note = await self.db.get_note(user_id, note_id)
        title = note["title"] if note else "Конспект"
        await self.send(
            chat_id,
            user_id,
            f"Сохранила конспект <b>{esc(title)}</b>.",
            saved_keyboard(note_id),
        )

    async def _send_notes(self, chat_id, user_id: int, page: int) -> None:
        text, markup = await self._notes_view(user_id, page)
        await self.send(chat_id, user_id, text, markup)

    async def _show_notes(
        self,
        event: MessageCallback,
        user_id: int,
        page: int,
        notification: str | None = None,
    ) -> None:
        text, markup = await self._notes_view(user_id, page)
        await self.show(event, text, markup, notification=notification)

    async def _notes_view(self, user_id: int, page: int):
        total = await self.db.count_notes(user_id)
        offset = page * PAGE_SIZE
        if total and offset >= total:
            page = max((total - 1) // PAGE_SIZE, 0)
            offset = page * PAGE_SIZE
        notes = await self.db.list_notes(user_id, offset, PAGE_SIZE) if total else []
        has_prev = page > 0
        has_next = offset + len(notes) < total
        markup = menu_keyboard() if total == 0 else notes_keyboard(notes, page, has_prev, has_next)
        return render_notes_page(notes, page, total), markup

    async def _open_note(self, event: MessageCallback, user_id: int, note_id: int) -> None:
        note = await self.db.get_note(user_id, note_id)
        if note is None:
            await self._show_notes(event, user_id, 0, notification="Конспект не найден")
            return
        await self.show(event, render_note(note), note_keyboard(note_id))

    async def _save_tutor(self, event: MessageCallback, chat_id, user_id: int) -> None:
        last = await self.db.get_last_tutor(user_id)
        if last is None:
            await self._notify(event, "Сначала попросите разбор")
            return
        reply = TutorReply(**last)
        note_id = await save_reply(self.db, user_id, reply, "chat")
        await self._notify(event, "Сохранила в конспекты")
        await self.send(
            chat_id,
            user_id,
            f"Конспект <b>{esc(reply.title)}</b> лежит в личном кабинете.",
            saved_keyboard(note_id),
        )

    async def _simpler(self, event: MessageCallback, chat_id, user_id: int) -> None:
        last = await self.db.get_last_tutor(user_id)
        if last is None:
            await self._notify(event, "Сначала попросите разбор")
            return
        await self._notify(event, "Упрощаю объяснение")
        try:
            reply = await self.tutor.simplify(last.get("reply") or "")
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self._remember_reply(user_id, "Объясни проще", reply)
        await self.send(chat_id, user_id, esc(reply.reply), tutor_keyboard(bool(reply.full_solution)))

    async def _similar_tutor(self, event: MessageCallback, chat_id, user_id: int) -> None:
        last = await self.db.get_last_tutor(user_id)
        if last is None:
            await self._notify(event, "Сначала попросите разбор")
            return
        await self._notify(event, "Подбираю похожую задачу")
        context = (last.get("reply") or "") + "\n" + (last.get("full_solution") or "")
        try:
            reply = await self.tutor.similar(last.get("skill") or "", context)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self._remember_reply(user_id, "Похожий пример", reply)
        await self.send(chat_id, user_id, esc(reply.reply), tutor_keyboard(bool(reply.full_solution)))

    async def _show_tutor_solution(self, event: MessageCallback, chat_id, user_id: int) -> None:
        last = await self.db.get_last_tutor(user_id)
        solution = (last or {}).get("full_solution") or ""
        if not solution.strip():
            await self._notify(event, "Полный разбор для этого ответа не прятался")
            return
        await self._notify(event, "Показываю полный разбор")
        await self.send(chat_id, user_id, esc(solution), tutor_keyboard(False))

    async def _reveal_check(self, event: MessageCallback, chat_id, user_id: int, check_id: int | None) -> None:
        check = await self._owned_check(user_id, check_id)
        if check is None:
            await self._notify(event, "Эта проверка уже недоступна")
            return
        solution = check["full_explanation"] or "Полный разбор не собрался. Пришлите решение ещё раз."
        await self._notify(event, "Показываю полный разбор")
        await self.send(chat_id, user_id, esc(solution), check_keyboard(check["id"], True))

    async def _save_check_note(self, event: MessageCallback, chat_id, user_id: int, check_id: int | None) -> None:
        check = await self._owned_check(user_id, check_id)
        if check is None:
            await self._notify(event, "Эта проверка уже недоступна")
            return
        note_id = await save_check(self.db, user_id, check)
        note = await self.db.get_note(user_id, note_id)
        title = note["title"] if note else "Разбор"
        await self._notify(event, "Сохранила в конспекты")
        await self.send(
            chat_id,
            user_id,
            f"Конспект <b>{esc(title)}</b> лежит в личном кабинете.",
            saved_keyboard(note_id),
        )

    async def _similar_check(self, event: MessageCallback, chat_id, user_id: int, check_id: int | None) -> None:
        check = await self._owned_check(user_id, check_id)
        if check is None:
            await self._notify(event, "Эта проверка уже недоступна")
            return
        await self._notify(event, "Подбираю похожую задачу")
        context = check["problem"] or check["hint"] or check["skill"]
        try:
            reply = await self.tutor.similar(check["skill"], context)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        await self._remember_reply(user_id, "Похожая задача", reply)
        await self.send(chat_id, user_id, esc(reply.reply), tutor_keyboard(bool(reply.full_solution)))

    async def _start_error_quiz(self, event: MessageCallback, chat_id, user_id: int) -> None:
        if await self.db.count_errors(user_id) == 0:
            await self.show(event, EMPTY_ERRORS, menu_keyboard())
            return
        await self._notify(event, "Собираю квиз по ошибкам")
        await self._send_error_quiz(chat_id, user_id)

    async def _send_error_quiz(self, chat_id, user_id: int) -> None:
        if await self.db.count_errors(user_id) == 0:
            await self.send(chat_id, user_id, EMPTY_ERRORS, menu_keyboard())
            return
        await self.send(chat_id, user_id, "Собираю квиз по ошибкам. Это займёт несколько секунд.", None)
        try:
            quiz_id = await start_error_quiz(self.db, self.tutor, user_id)
        except TutorError as error:
            await self.send(chat_id, user_id, esc(error.user_message), menu_keyboard())
            return
        if quiz_id is None:
            await self.send(chat_id, user_id, EMPTY_ERRORS, menu_keyboard())
            return
        await self._send_quiz(chat_id, user_id, quiz_id)

    async def _send_quiz(self, chat_id, user_id: int, quiz_id: int) -> None:
        text, question = await self._quiz_screen(quiz_id)
        if question is None:
            await self.send(chat_id, user_id, "Квиз не собрался. Попробуйте ещё раз.", menu_keyboard())
            return
        await self.send(chat_id, user_id, text, quiz_keyboard(question["id"], option_count(question)))

    async def _answer_quiz(self, event: MessageCallback, user_id: int, question_id: int, option: int) -> None:
        view = await answer_question(self.db, user_id, question_id, option)
        if view.missing:
            await self._notify(event, "Эта кнопка уже не работает")
            return
        if view.already_closed:
            notice = "Этот квиз уже закрыт" if view.closed_quiz else "Эта задача уже закрыта"
            await self._notify(event, notice)
            return
        if view.finished or view.next_question is None:
            await self.show(event, render_finished(view), menu_keyboard())
            return
        quiz = await self.db.get_quiz(view.next_question["quiz_id"])
        if quiz is None:
            await self.show(event, render_finished(view), menu_keyboard())
            return
        started = quiz["started_at"]
        screen = render_question(view.quiz_title, started, view.done, view.total, view.next_question)
        text = render_feedback(view) + "\n\n" + screen
        await self.show(
            event,
            text,
            quiz_keyboard(view.next_question["id"], option_count(view.next_question)),
        )

    async def _quiz_screen(self, quiz_id: int):
        quiz = await self.db.get_quiz(quiz_id)
        question = await self.db.next_pending(quiz_id)
        if quiz is None or question is None:
            return "Квиз не найден.", None
        done, total = await self.db.quiz_counts(quiz_id)
        return (
            render_question(quiz["title"], quiz["started_at"], done, total, question),
            question,
        )

    async def _remember_reply(self, user_id: int, user_text: str, reply: TutorReply) -> None:
        await self.db.add_message(user_id, "user", user_text)
        await self.db.add_message(user_id, "assistant", reply.reply)
        await self.db.set_last_tutor(user_id, reply.as_dict())

    async def _owned_check(self, user_id: int, check_id: int | None) -> dict | None:
        if check_id is None:
            return None
        return await self.db.get_check(user_id, check_id)

    async def send(self, chat_id, user_id: int, text: str, markup) -> None:
        await self.bot.send_message(
            chat_id=chat_id,
            user_id=user_id,
            text=clip(text),
            attachments=[markup] if markup is not None else None,
            parse_mode=ParseMode.HTML,
        )

    async def show(
        self,
        event: MessageCallback,
        text: str,
        markup,
        notification: str | None = None,
    ) -> None:
        try:
            await event.edit(
                text=clip(text),
                attachments=[markup] if markup is not None else [],
                format=ParseMode.HTML,
                notification=notification,
            )
        except Exception:
            log.exception("callback edit failed")
            if notification:
                await self._notify(event, notification)
            chat_id, user_id = event.get_ids()
            if user_id is not None:
                await self.send(chat_id, user_id, text, markup)

    async def _notify(self, event: MessageCallback, notification: str) -> None:
        try:
            await event.ack(notification=notification[:180])
        except Exception:
            log.debug("callback ack failed", exc_info=True)

    def _person(self, event: MessageCreated) -> tuple[int | None, int | None, str]:
        message = event.message
        sender = message.sender
        user_id = sender.user_id if sender is not None else None
        first_name = sender.first_name or "" if sender is not None else ""
        chat_id = message.recipient.chat_id if message.recipient is not None else None
        return chat_id, user_id, first_name

    def _image_url(self, event: MessageCreated) -> str | None:
        body = event.message.body
        if body is None or not body.attachments:
            return None
        for attachment in body.attachments:
            if attachment.type != AttachmentType.IMAGE:
                continue
            url = getattr(attachment.payload, "url", None)
            if url:
                return url
        return None


def _payload_int(payload: str, index: int) -> int | None:
    parts = payload.split(":")
    if len(parts) <= index:
        return None
    try:
        return int(parts[index])
    except ValueError:
        return None
