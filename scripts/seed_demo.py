"""Заполняет демо-учётку для страницы /demo.

Запуск: python -m scripts.seed_demo
Повторный запуск заново собирает конспекты, ошибки и квизы этой учётки.
"""

import asyncio

from app.config import get_settings
from app.db import Database
from app.services.demo import DEMO_NAME, DEMO_USER_ID, NOTES, ensure_demo


async def main() -> None:
    settings = get_settings()
    db = Database(settings.database_path)
    await db.connect()
    await ensure_demo(db, force=True)
    await db.close()
    print(f"Демо-кабинет «{DEMO_NAME}» готов: {len(NOTES)} конспектов, user_id={DEMO_USER_ID}.")


if __name__ == "__main__":
    asyncio.run(main())
