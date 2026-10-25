"""Заполняет ученика живым небом для показа жюри.

Запуск: python -m scripts.seed_demo [user_id]
Повторный запуск обновляет звёзды этого ученика и не плодит одинаковые конспекты.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import Database

SAMPLES = [
    ("Математика", "деление дробей", 0.8, 4, 1, "Демо: деление дробей", "Чтобы разделить дроби, умножь на перевёрнутую."),
    ("Математика", "проценты", 0.4, 2, 6, "Демо: проценты", "Процент — это сотая часть числа."),
    ("Математика", "линейные уравнения", 0.2, 1, 9, "Демо: уравнения", "Переноси слагаемые и деляй обе части на коэффициент."),
    ("Геометрия", "теорема пифагора", 1.0, 5, 2, "Демо: Пифагор", "Квадрат гипотенузы равен сумме квадратов катетов."),
    ("Геометрия", "площадь треугольника", 0.4, 2, 8, "Демо: площадь", "Площадь равна половине произведения основания на высоту."),
    ("Физика", "закон ома", 0.6, 3, 3, "Демо: закон Ома", "Сила тока равна напряжению, делённому на сопротивление."),
    ("Физика", "скорость", 0.2, 1, 12, "Демо: скорость", "Скорость — это путь, делённый на время."),
    ("Русский язык", "н и нн", 0.8, 4, 4, "Демо: н и нн", "В кратком причастии пишется одна н."),
]


async def main() -> None:
    user_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    settings = get_settings()
    db = Database(settings.database_path)
    await db.connect()
    await db.upsert_user(user_id, "Аня")
    await db.set_grade(user_id, 8)
    await db.set_explain_style(user_id, "detail")
    await db.conn.execute("DELETE FROM skill_events WHERE skill_id IN (SELECT id FROM skills WHERE user_id = ?)", (user_id,))
    await db.conn.execute("DELETE FROM skills WHERE user_id = ?", (user_id,))
    await db.conn.commit()
    now = datetime.now(timezone.utc)
    existing = {row["title"] for row in await db.list_notes(user_id, 0, 100)}
    for subject, name, strength, box, days_ago, title, body in SAMPLES:
        seen = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        due = (now - timedelta(days=1)).isoformat(timespec="seconds") if days_ago >= 6 else (now + timedelta(days=box)).isoformat(timespec="seconds")
        skill_id = await db.insert_skill(user_id, subject, name, strength, box, seen, due)
        await db.insert_skill_event(skill_id, "check", strength >= 0.6)
        if title not in existing:
            note_id = await db.insert_note(user_id, title, subject, body, "manual")
            await db.set_note_skill(user_id, note_id, skill_id)
        if strength <= 0.2:
            await db.insert_error(user_id, subject, body, name, "Нужно повторить приём.", skill_id)
    await db.close()
    print(f"Небо ученика {user_id} готово: {len(SAMPLES)} звёзд.")


if __name__ == "__main__":
    asyncio.run(main())
