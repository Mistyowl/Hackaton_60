import asyncio
import logging

import uvicorn
from maxapi import Bot, Dispatcher
from maxapi.enums.parse_mode import ParseMode
from maxapi.enums.update import UpdateType
from maxapi.webhook.fastapi import FastAPIMaxWebhook

from app.bot.commands import bot_commands
from app.bot.handlers import BotApp
from app.bot.keyboards import reminder_keyboard
from app.config import get_settings, webhook_path
from app.db import Database
from app.services.demo import ensure_demo
from app.services.reminders import in_quiet_hours, moscow_now, reminder_text
from app.services.tutor import Tutor
from app.textutil import esc
from app.web import create_app

log = logging.getLogger(__name__)
REMIND_EVERY_SECONDS = 1800
WEBHOOK_UPDATES = [
    UpdateType.BOT_STARTED,
    UpdateType.MESSAGE_CREATED,
    UpdateType.MESSAGE_CALLBACK,
]


async def _bot_username(bot: Bot) -> str:
    try:
        me = await bot.get_me()
        return getattr(me, "username", "") or ""
    except Exception:
        log.warning("Не удалось узнать имя бота для кнопки кабинета.", exc_info=True)
        return ""


async def _publish_commands(bot: Bot, cabinet_url: str) -> None:
    try:
        await bot.set_commands(*bot_commands(bool(cabinet_url.strip())))
    except Exception:
        log.warning(
            "Не удалось опубликовать меню команд. Останется список с прошлого запуска.",
            exc_info=True,
        )


async def run_bot_polling(
    bot: Bot,
    dispatcher: Dispatcher,
    db: Database,
    cabinet_url: str,
    web_app: str,
) -> None:
    try:
        await bot.delete_webhook()
    except Exception:
        log.warning(
            "Не удалось снять webhook. Если polling молчит, удалите подписку в кабинете MAX.",
            exc_info=True,
        )
    await _publish_commands(bot, cabinet_url)
    log.info(
        "Бот слушает MAX через long polling. "
        "Нативные голосовые могут приходить пустыми — для них нужен MAX_WEBHOOK_URL."
    )
    reminders = asyncio.create_task(_remind_loop(bot, db, web_app))
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminders.cancel()


async def run_bot_webhook(
    bot: Bot,
    dispatcher: Dispatcher,
    db: Database,
    cabinet_url: str,
    web_app: str,
    webhook_url: str,
    webhook_secret: str,
) -> None:
    await dispatcher.startup(bot)
    await _publish_commands(bot, cabinet_url)
    try:
        await bot.delete_webhook()
    except Exception:
        log.warning("Не удалось снять старые webhook-подписки.", exc_info=True)
    try:
        await bot.subscribe_webhook(
            webhook_url,
            update_types=WEBHOOK_UPDATES,
            secret=webhook_secret,
        )
    except Exception:
        log.exception("Не удалось подписаться на webhook %s", webhook_url)
        raise
    log.info("Бот слушает MAX через webhook %s", webhook_url)
    reminders = asyncio.create_task(_remind_loop(bot, db, web_app))
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        reminders.cancel()
        try:
            await bot.unsubscribe_webhook(webhook_url)
        except Exception:
            log.warning("Не удалось отписаться от webhook при остановке.", exc_info=True)
        await dispatcher.shutdown()


async def _remind_loop(bot, db: Database, web_app: str) -> None:
    await asyncio.sleep(45)
    while True:
        try:
            if not in_quiet_hours():
                await _send_reminders(bot, db, web_app)
        except Exception:
            log.exception("Цикл напоминаний о банке ошибок упал")
        await asyncio.sleep(REMIND_EVERY_SECONDS)


async def _send_reminders(bot, db: Database, web_app: str) -> None:
    today = moscow_now().date().isoformat()
    for user in await db.users_to_remind(today):
        user_id = int(user["user_id"])
        text = reminder_text(int(user["open_errors"]))
        if text is None:
            continue
        try:
            await bot.send_message(
                chat_id=user["chat_id"],
                user_id=user_id,
                text=esc(text),
                attachments=[reminder_keyboard(web_app)],
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            log.warning("Напоминание не отправилось user_id=%s", user_id, exc_info=True)
        await db.mark_reminded(user_id, today)


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    allowed = settings.allowed_ids()
    if allowed:
        log.info("Доступ ограничен, пользователей: %s", len(allowed))
    db = Database(settings.database_path)
    await db.connect()
    try:
        await ensure_demo(db)
    except Exception:
        log.exception("Не удалось заполнить демо-кабинет")
    proxy_url = settings.gemini_proxy_url()
    if settings.proxy_host.strip():
        log.info("Gemini через SOCKS5 %s:%s", settings.proxy_host.strip(), settings.proxy_port)
    tutor = Tutor(
        settings.gemini_api_key,
        settings.gemini_model,
        proxy_url,
        fallback_model=settings.gemini_fallback_model,
    )
    if tutor.fallback_models:
        log.info(
            "Gemini: основная %s, запасные %s",
            settings.gemini_model,
            ", ".join(tutor.fallback_models),
        )

    token = settings.max_bot_token.strip()
    bot: Bot | None = None
    dispatcher: Dispatcher | None = None
    web_app = ""
    webhook = None
    hook_url = ""
    hook_secret = ""
    hook_path = "/max-webhook"

    if token:
        try:
            hook_url, hook_secret = settings.webhook()
        except ValueError as error:
            log.error("%s — включаю long polling без webhook.", error)
            hook_url, hook_secret = "", ""
    if token:
        bot = Bot(token=token, parse_mode=ParseMode.HTML)
        dispatcher = Dispatcher()
        web_app = await _bot_username(bot)
        BotApp(bot, db, tutor, settings.cabinet_url, web_app, allowed).register(dispatcher)
        if hook_url:
            webhook = FastAPIMaxWebhook(dp=dispatcher, bot=bot, secret=hook_secret)
            hook_path = webhook_path(hook_url)

    app = create_app(
        db,
        tutor,
        settings.max_bot_token,
        allowed,
        settings.week_quiz_goal,
        max_webhook=webhook,
        max_webhook_path=hook_path,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.web_host,
            port=settings.web_port,
            log_level="info",
        )
    )
    log.info("Кабинет: http://%s:%s", settings.web_host, settings.web_port)
    if not settings.gemini_api_key.strip():
        log.warning("GEMINI_API_KEY пуст: разбор, проверка и квиз ответят просьбой добавить ключ.")
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(server.serve())
            if bot is not None and dispatcher is not None:
                if hook_url:
                    group.create_task(
                        run_bot_webhook(
                            bot,
                            dispatcher,
                            db,
                            settings.cabinet_url,
                            web_app,
                            hook_url,
                            hook_secret,
                        )
                    )
                else:
                    group.create_task(
                        run_bot_polling(
                            bot, dispatcher, db, settings.cabinet_url, web_app
                        )
                    )
            else:
                log.warning("MAX_BOT_TOKEN пуст: бот не запущен, открыт только кабинет.")
    finally:
        await db.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
