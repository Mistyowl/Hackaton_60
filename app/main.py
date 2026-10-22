import asyncio
import logging

import uvicorn
from maxapi import Bot, Dispatcher
from maxapi.enums.parse_mode import ParseMode

from app.bot.handlers import BotApp
from app.config import get_settings
from app.db import Database
from app.services.tutor import Tutor
from app.web import create_app

log = logging.getLogger(__name__)


async def run_bot(token: str, db: Database, tutor: Tutor) -> None:
    bot = Bot(token=token, parse_mode=ParseMode.HTML)
    dispatcher = Dispatcher()
    BotApp(bot, db, tutor).register(dispatcher)
    try:
        await bot.delete_webhook()
    except Exception:
        log.warning(
            "Не удалось снять webhook. Если polling молчит, удалите подписку в кабинете MAX.",
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
    db = Database(settings.database_path)
    await db.connect()
    tutor = Tutor(settings.gemini_api_key, settings.gemini_model)
    app = create_app(db, tutor)
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
                group.create_task(run_bot(token, db, tutor))
            else:
                log.warning("MAX_BOT_TOKEN пуст: бот не запущен, открыт только кабинет.")
    finally:
        await db.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
