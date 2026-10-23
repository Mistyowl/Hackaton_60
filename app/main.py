import asyncio
import logging

import uvicorn
from maxapi import Bot, Dispatcher
from maxapi.enums.parse_mode import ParseMode

from app.bot.commands import bot_commands
from app.bot.handlers import BotApp
from app.config import get_settings
from app.db import Database
from app.services.tutor import Tutor
from app.web import create_app

log = logging.getLogger(__name__)


async def run_bot(
    token: str,
    db: Database,
    tutor: Tutor,
    cabinet_url: str,
    allowed_user_ids: set[int],
) -> None:
    bot = Bot(token=token, parse_mode=ParseMode.HTML)
    dispatcher = Dispatcher()
    web_app = ""
    try:
        me = await bot.get_me()
        web_app = getattr(me, "username", "") or ""
    except Exception:
        log.warning("Не удалось узнать имя бота для кнопки кабинета.", exc_info=True)
    BotApp(bot, db, tutor, cabinet_url, web_app, allowed_user_ids).register(dispatcher)
    try:
        await bot.delete_webhook()
    except Exception:
        log.warning(
            "Не удалось снять webhook. Если polling молчит, удалите подписку в кабинете MAX.",
            exc_info=True,
        )
    try:
        await bot.set_commands(*bot_commands(bool(cabinet_url.strip())))
    except Exception:
        log.warning(
            "Не удалось опубликовать меню команд. Останется список с прошлого запуска.",
            exc_info=True,
        )
    log.info("Бот слушает MAX через long polling")
    await dispatcher.start_polling(bot)


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
    proxy_url = settings.gemini_proxy_url()
    if settings.proxy_host.strip():
        log.info("Gemini через SOCKS5 %s:%s", settings.proxy_host.strip(), settings.proxy_port)
    tutor = Tutor(settings.gemini_api_key, settings.gemini_model, proxy_url)
    app = create_app(db, tutor, settings.max_bot_token, allowed)
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
            token = settings.max_bot_token.strip()
            if token:
                group.create_task(run_bot(token, db, tutor, settings.cabinet_url, allowed))
            else:
                log.warning("MAX_BOT_TOKEN пуст: бот не запущен, открыт только кабинет.")
    finally:
        await db.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
