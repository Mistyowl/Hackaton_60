"""Публичная учётка для страницы /demo: кабинет уже заполнен, как у девятиклассницы."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db import Database
from app.services.reminders import moscow_now

log = logging.getLogger(__name__)

# Отрицательный id не пересекается с пользователями MAX.
DEMO_USER_ID = -4242
DEMO_NAME = "Маша"
DEMO_NOTE = "Линейные уравнения: скобки"
_MOSCOW = ZoneInfo("Europe/Moscow")
_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class _Skill:
    subject: str
    name: str
    strength: float
    box: int
    seen_days: int
    due_days: int
    results: tuple[bool, ...]


@dataclass(frozen=True)
class _Note:
    title: str
    skill: str
    subject: str
    body: str
    source: str
    days_ago: int
    pinned: bool = False


@dataclass(frozen=True)
class _Mistake:
    skill: str
    subject: str
    problem: str
    mistake: str
    explanation: str
    source: str
    days_ago: int
    closed: bool = False


SKILLS = (
    _Skill("Алгебра", "Линейные уравнения", 0.4, 2, 1, -1, (False,)),
    _Skill("Алгебра", "Проценты", 0.6, 3, 2, 2, (False, True, True)),
    _Skill("Алгебра", "Деление дробей", 0.8, 4, 0, 8, (False, True, True, True)),
    _Skill("Геометрия", "Теорема Пифагора", 1.0, 5, 1, 16, (False, True, True, True)),
    _Skill("Геометрия", "Площадь треугольника", 0.2, 1, 10, -1, (False,)),
    _Skill("Физика", "Скорость", 0.4, 2, 7, -2, (False, True)),
    _Skill("Физика", "Закон Ома", 0.6, 3, 3, -1, (False, True)),
    _Skill("Русский язык", "Н и НН", 0.8, 4, 4, 6, (False, True, True, True)),
    _Skill("Химия", "Валентность", 0.2, 1, 5, -1, (False,)),
    _Skill("Английский", "Present Perfect", 0.6, 3, 2, 4, (True, True)),
)

NOTES = (
    _Note(
        DEMO_NOTE,
        "Линейные уравнения",
        "Алгебра",
        "\n".join(
            [
                "Условие:",
                "Реши уравнение `3(x − 2) = 15`.",
                "",
                "Решение ученика:",
                "Раскрыла скобки и получила `3x − 2 = 15`.",
                "",
                "Разбор:",
                "На 3 умножается **каждый** член в скобках, вместе со знаком.",
                "",
                "Полный разбор:",
                "Шаг 1: `3x − 6 = 15`",
                "Шаг 2: `3x = 21`",
                "Ответ: `x = 7`",
                "",
                "Приём: линейные уравнения",
            ]
        ),
        "photo",
        0,
        True,
    ),
    _Note(
        "Проценты: цена после подорожания",
        "Проценты",
        "Алгебра",
        "\n".join(
            [
                "Условие:",
                "Куртка стоила 2 400 ₽ и подорожала на 15%. Какая цена теперь?",
                "",
                "Разбор:",
                "15% — это `0,15` от цены, а не 15 рублей.",
                "Формула: `2400 × 0,15 = 360`",
                "Шаг 1: надбавка 360 ₽",
                "Шаг 2: новая цена `2400 + 360 = 2760`",
                "",
                "Приём: проценты",
            ]
        ),
        "chat",
        1,
    ),
    _Note(
        "Деление дробей",
        "Деление дробей",
        "Алгебра",
        "\n".join(
            [
                "Правило: чтобы разделить на дробь, умножь на перевёрнутую.",
                "Пример: `1/2 : 1/4 = 1/2 × 4/1 = 2`",
                "Шаг 1: переверни делитель",
                "Шаг 2: умножь числители и знаменатели",
                "Шаг 3: сократи, если можно",
                "",
                "Приём: деление дробей",
            ]
        ),
        "chat",
        4,
    ),
    _Note(
        "Теорема Пифагора",
        "Теорема Пифагора",
        "Геометрия",
        "\n".join(
            [
                "Условие:",
                "В прямоугольном треугольнике катеты 6 см и 8 см. Найди гипотенузу.",
                "",
                "Разбор:",
                "Квадрат гипотенузы равен сумме квадратов катетов.",
                "Формула: `c² = a² + b²`",
                "Пример: `36 + 64 = 100`, значит `c = 10`",
                "",
                "Приём: теорема Пифагора",
            ]
        ),
        "photo",
        3,
    ),
    _Note(
        "Площадь треугольника",
        "Площадь треугольника",
        "Геометрия",
        "\n".join(
            [
                "Условие:",
                "Основание 8 см, высота 5 см. Найди площадь треугольника.",
                "",
                "Решение ученика:",
                "Умножила 8 на 5 и получила 40 см².",
                "",
                "Разбор:",
                "Площадь — **половина** произведения основания на высоту.",
                "Формула: `S = a · h / 2`",
                "Ответ: `S = 20` см²",
                "",
                "Приём: площадь треугольника",
            ]
        ),
        "chat",
        5,
    ),
    _Note(
        "Скорость, путь и время",
        "Скорость",
        "Физика",
        "\n".join(
            [
                "Условие:",
                "Велосипедист проехал 36 км за 2 часа. Найди скорость.",
                "",
                "Разбор:",
                "Скорость — это путь, **делённый** на время, а не произведение.",
                "Формула: `v = s / t`",
                "Пример: `36 / 2 = 18` км/ч",
                "",
                "Приём: скорость",
            ]
        ),
        "manual",
        12,
    ),
    _Note(
        "Закон Ома",
        "Закон Ома",
        "Физика",
        "\n".join(
            [
                "Условие:",
                "На участке цепи напряжение 12 В, сопротивление 4 Ом. Найди силу тока.",
                "",
                "Разбор:",
                "Сила тока равна напряжению, делённому на сопротивление.",
                "Формула: `I = U / R`",
                "Пример: `12 / 4 = 3` А",
                "Запомни: если ищут сопротивление, формула переворачивается: `R = U / I`",
                "",
                "Приём: закон Ома",
            ]
        ),
        "photo",
        2,
    ),
    _Note(
        "Н и НН в причастиях",
        "Н и НН",
        "Русский язык",
        "\n".join(
            [
                "Правило: в кратком причастии пишется одна **н**.",
                "Пример: задача решена, поля вспаханы.",
                "Шаг 1: проверь, краткая ли форма",
                "Шаг 2: если краткая — одна н",
                "Шаг 3: в полной форме смотри суффикс и приставку",
                "Пример: `решённая задача`, но `задача решена`",
                "",
                "Приём: н и нн",
            ]
        ),
        "manual",
        3,
    ),
    _Note(
        "Валентность кислорода",
        "Валентность",
        "Химия",
        "\n".join(
            [
                "Условие:",
                "Какая валентность у кислорода в оксидах?",
                "",
                "Разбор:",
                "В оксидах кислород почти всегда двухвалентный.",
                "Пример: `H₂O`, `CO₂`, `CaO`",
                "Запомни: II, а не IV. Четыре бывает у углерода в `CO₂`, не у кислорода.",
                "",
                "Приём: валентность",
            ]
        ),
        "chat",
        0,
    ),
    _Note(
        "Present Perfect",
        "Present Perfect",
        "Английский",
        "\n".join(
            [
                "Правило: have/has + третья форма глагола, если важен результат к настоящему.",
                "Пример: `I have lost my keys` — ключей нет сейчас.",
                "Шаг 1: выбери have или has",
                "Шаг 2: поставь глагол в третью форму",
                "Пример: `She has finished the test`",
                "",
                "Приём: Present Perfect",
            ]
        ),
        "manual",
        2,
    ),
)

MISTAKES = (
    _Mistake(
        "Деление дробей",
        "Алгебра",
        "Вычисли `3/4 : 2/5`.",
        "Перевернула делимое, а не делитель.",
        "Переворачивается вторая дробь: `3/4 × 5/2 = 15/8`.",
        "quiz",
        4,
        True,
    ),
    _Mistake(
        "Теорема Пифагора",
        "Геометрия",
        "Катеты 5 см и 12 см. Найди гипотенузу.",
        "Сложила катеты, а не их квадраты.",
        "`5² + 12² = 25 + 144 = 169`, гипотенуза 13 см.",
        "check",
        6,
        True,
    ),
    _Mistake(
        "Н и НН",
        "Русский язык",
        "Вставь пропущенные буквы: задача реше_а, реше_ая задача.",
        "В кратком причастии написала две н.",
        "Краткое причастие — одна н: решена. Полное с приставкой — решённая.",
        "quiz",
        5,
        True,
    ),
    _Mistake(
        "Линейные уравнения",
        "Алгебра",
        "Реши уравнение `3(x − 2) = 15`.",
        "При раскрытии скобок не умножила 2 на 3.",
        "Скобка целиком умножается на 3: `3x − 6 = 15`, `x = 7`.",
        "check",
        1,
    ),
    _Mistake(
        "Проценты",
        "Алгебра",
        "Куртка стоила 2 400 ₽ и подорожала на 15%. Какая цена теперь?",
        "Прибавила 15 рублей вместо 15% от цены.",
        "15% от 2400 — это 360 ₽. Новая цена 2 760 ₽.",
        "check",
        2,
    ),
    _Mistake(
        "Площадь треугольника",
        "Геометрия",
        "Основание 8 см, высота 5 см. Найди площадь.",
        "Забыла разделить произведение основания на высоту пополам.",
        "`S = 8 × 5 / 2 = 20` см².",
        "chat",
        5,
    ),
    _Mistake(
        "Скорость",
        "Физика",
        "Велосипедист проехал 36 км за 2 часа. Найди скорость.",
        "Умножила путь на время вместо деления.",
        "`v = 36 / 2 = 18` км/ч.",
        "check",
        8,
    ),
    _Mistake(
        "Закон Ома",
        "Физика",
        "Напряжение 12 В, сопротивление 4 Ом. Найди силу тока.",
        "Умножила напряжение на сопротивление.",
        "`I = U / R = 12 / 4 = 3` А.",
        "quiz",
        3,
    ),
    _Mistake(
        "Валентность",
        "Химия",
        "Расставь валентности в формуле воды.",
        "У кислорода написала IV вместо II.",
        "В `H₂O` у кислорода валентность II, у водорода I.",
        "chat",
        0,
    ),
)

_QUIZZES = (
    (
        "Деление дробей",
        "topic",
        "Алгебра",
        "Деление дробей",
        (
            ("Чему равно `1/2 : 1/4`?", ("1/8", "2", "1/2", "4"), 1, 1, "Деление на дробь — умножение на перевёрнутую: `1/2 × 4 = 2`."),
            ("Чему равно `3/5 : 3`?", ("9/5", "3/15", "1/5", "3/5"), 2, 1, "Делить на 3 — то же, что умножить на `1/3`: `3/5 × 1/3 = 1/5`."),
            ("Чему равно `2/3 : 4/9`?", ("8/27", "3/2", "2/3", "8/9"), 1, 1, "`2/3 × 9/4 = 3/2`."),
            ("Что делают с делителем при делении дробей?", ("Переворачивают делимое", "Переворачивают делитель", "Складывают знаменатели", "Вычитают числители"), 1, 1, "Переворачивается вторая дробь, потом дроби умножают."),
            ("Чему равно `5/6 : 5/3`?", ("25/18", "1/2", "3/2", "5/2"), 1, 1, "`5/6 × 3/5 = 1/2`."),
        ),
    ),
    (
        "Теорема Пифагора",
        "topic",
        "Геометрия",
        "Теорема Пифагора",
        (
            ("Катеты 3 и 4. Чему равна гипотенуза?", ("6", "5", "7", "12"), 1, 1, "`3² + 4² = 25`, гипотенуза 5."),
            ("Катеты 6 и 8. Чему равна гипотенуза?", ("14", "12", "10", "48"), 2, 1, "`36 + 64 = 100`, гипотенуза 10."),
            ("Гипотенуза 13, один катет 5. Чему равен второй?", ("8", "12", "14", "18"), 1, 1, "`13² − 5² = 144`, катет 12."),
            ("Чему равен квадрат гипотенузы?", ("Сумме катетов", "Произведению катетов", "Сумме квадратов катетов", "Разности катетов"), 2, 1, "В прямоугольном треугольнике `c² = a² + b²`."),
            ("Катеты 5 и 12. Чему равна гипотенуза?", ("13", "17", "15", "7"), 0, 1, "`25 + 144 = 169`, гипотенуза 13."),
        ),
    ),
    (
        "Проценты",
        "topic",
        "Алгебра",
        "Проценты",
        (
            ("Сколько будет 20% от 150?", ("20", "30", "15", "50"), 1, 1, "`150 × 0,2 = 30`."),
            ("Число увеличили на 10%. Во сколько раз оно выросло?", ("в 10 раз", "на 0,1", "в 1,1 раза", "в 2 раза"), 2, 1, "Плюс 10% — это умножить на 1,1."),
            ("Куртка за 2 400 ₽ подорожала на 15%. Новая цена?", ("2 415 ₽", "2 760 ₽", "2 640 ₽", "3 600 ₽"), 1, 1, "15% от 2400 — это 360. Вместе 2 760 ₽."),
            ("Сколько будет 1% от 500?", ("50", "1", "5", "10"), 2, 2, "1% — это одна сотая: `500 / 100 = 5`."),
            ("Скидка 25% от 800 ₽. Сколько заплатят?", ("600 ₽", "200 ₽", "775 ₽", "825 ₽"), 0, 2, "25% — это 200 ₽. К оплате `800 − 200 = 600`."),
        ),
    ),
    (
        "Закон Ома",
        "errors",
        "Физика",
        "Закон Ома",
        (
            ("U = 12 В, R = 4 Ом. Чему равен ток?", ("48 А", "3 А", "8 А", "16 А"), 1, 1, "`I = 12 / 4 = 3` А."),
            ("I = 2 А, R = 5 Ом. Чему равно напряжение?", ("2,5 В", "7 В", "10 В", "3 В"), 2, 1, "`U = I × R = 10` В."),
            ("Как записать закон Ома для силы тока?", ("I = U × R", "I = R / U", "I = U / R", "I = U + R"), 2, 1, "Ток равен напряжению, делённому на сопротивление."),
            ("U = 9 В, I = 3 А. Чему равно сопротивление?", ("27 Ом", "12 Ом", "3 Ом", "6 Ом"), 2, 2, "`R = U / I = 9 / 3 = 3` Ом."),
            ("I = 0,5 А, U = 6 В. Чему равно сопротивление?", ("3 Ом", "6,5 Ом", "12 Ом", "0,5 Ом"), 2, 2, "`R = 6 / 0,5 = 12` Ом."),
        ),
    ),
)


def _stamp(day: date) -> str:
    moment = datetime(day.year, day.month, day.day, 16, 10, tzinfo=_MOSCOW)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _quiz_dates(today: date) -> list[date]:
    week_start = today - timedelta(days=today.weekday())
    if today.weekday() == 6:
        week_start += timedelta(days=1)
    days = [week_start + timedelta(days=offset) for offset in range((today - week_start).days + 1)]
    return [days[-(index % len(days))] for index in range(len(_QUIZZES))]


async def _is_ready(db: Database) -> bool:
    rows = await db.list_notes(DEMO_USER_ID, 0, 100)
    titles = {row["title"] for row in rows}
    if DEMO_NOTE not in titles or len(rows) < len(NOTES):
        return False
    stats = await db.profile_stats(DEMO_USER_ID)
    return int(stats["quizzes_done"]) >= len(_QUIZZES) and int(stats["closed_errors"]) >= 3


async def ensure_demo(db: Database, *, force: bool = False) -> None:
    async with _LOCK:
        if not force and await _is_ready(db):
            return
        await _fill(db)
        log.info("Демо-кабинет заполнен, user_id=%s", DEMO_USER_ID)


async def _fill(db: Database) -> None:
    await db.upsert_user(DEMO_USER_ID, DEMO_NAME)
    await db.clear_learning(DEMO_USER_ID)
    await db.set_profile(DEMO_USER_ID, DEMO_NAME, 9, exam_goal="oge")
    await db.set_explain_style(DEMO_USER_ID, "detail")
    await db.set_reminders(DEMO_USER_ID, True)
    await db.clear_cabinet_tour(DEMO_USER_ID)

    now = datetime.now(timezone.utc)
    today = moscow_now().date()
    skill_ids: dict[str, int] = {}
    for skill in SKILLS:
        seen = (now - timedelta(days=skill.seen_days, hours=3)).isoformat(timespec="seconds")
        due = (now + timedelta(days=skill.due_days)).isoformat(timespec="seconds")
        skill_id = await db.insert_skill(
            DEMO_USER_ID,
            skill.subject,
            skill.name,
            skill.strength,
            skill.box,
            seen,
            due,
        )
        skill_ids[skill.name] = skill_id
        for offset, correct in enumerate(skill.results):
            moment = (now - timedelta(days=skill.seen_days, minutes=20 - offset)).isoformat(timespec="seconds")
            await db.insert_skill_event(skill_id, "check", correct, moment)

    for note in NOTES:
        note_id = await db.insert_note(
            DEMO_USER_ID,
            note.title,
            note.subject,
            note.body,
            note.source,
            _stamp(today - timedelta(days=note.days_ago)),
        )
        await db.set_note_skill(DEMO_USER_ID, note_id, skill_ids[note.skill])
        if note.pinned:
            await db.set_note_pinned(DEMO_USER_ID, note_id, True)

    for mistake in MISTAKES:
        error_id = await db.insert_error(
            DEMO_USER_ID,
            mistake.subject,
            mistake.problem,
            mistake.mistake,
            mistake.explanation,
            skill_ids[mistake.skill],
            mistake.source,
            created_at=_stamp(today - timedelta(days=mistake.days_ago)),
        )
        if mistake.closed:
            await db.close_error(
                DEMO_USER_ID,
                error_id,
                _stamp(today - timedelta(days=max(mistake.days_ago - 2, 1))),
            )

    for spec, day in zip(_QUIZZES, _quiz_dates(today), strict=True):
        title, source, subject, skill, questions = spec
        await db.insert_done_quiz(
            DEMO_USER_ID,
            title,
            source,
            _stamp(day),
            [
                {
                    "prompt": prompt,
                    "options": list(options),
                    "correct_index": correct_index,
                    "explanation": explanation,
                    "skill": skill,
                    "subject": subject,
                    "attempts": attempts,
                }
                for prompt, options, correct_index, attempts, explanation in questions
            ],
        )

    wrong = await db.insert_check(
        DEMO_USER_ID,
        {
            "subject": "Алгебра",
            "problem": "Реши уравнение 3(x − 2) = 15.",
            "student_solution": "3x − 2 = 15, значит x = 17/3.",
            "is_correct": False,
            "error_step": "При раскрытии скобок не умножила 2 на 3.",
            "hint": "Умножь на 3 оба слагаемых в скобках.",
            "full_explanation": "3x − 6 = 15, 3x = 21, x = 7.",
            "skill": "Линейные уравнения",
            "praise": "",
            "created_at": _stamp(today),
        },
    )
    await db.set_check_skill(wrong, skill_ids["Линейные уравнения"])
    right = await db.insert_check(
        DEMO_USER_ID,
        {
            "subject": "Геометрия",
            "problem": "Катеты 6 см и 8 см. Найди гипотенузу.",
            "student_solution": "36 + 64 = 100, гипотенуза 10 см.",
            "is_correct": True,
            "error_step": "",
            "hint": "",
            "full_explanation": "√(6² + 8²) = 10.",
            "skill": "Теорема Пифагора",
            "praise": "Верно сложила квадраты катетов.",
            "created_at": _stamp(today - timedelta(days=3)),
        },
    )
    await db.set_check_skill(right, skill_ids["Теорема Пифагора"])
