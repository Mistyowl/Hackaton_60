from maxapi.types import CallbackButton, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.services.notes import NoteHit
from app.textutil import LETTERS, button_label


def _markup(*rows: list[CallbackButton]):
    keyboard = InlineKeyboardBuilder()
    for row in rows:
        if row:
            keyboard.row(*row)
    return keyboard.as_markup()


def menu_keyboard(cabinet: bool = False):
    ask = [CallbackButton(text="💬 Спросить", payload="menu:ask")]
    notes = [CallbackButton(text="✍️ Свой конспект", payload="menu:own")]
    if not cabinet:
        ask.append(CallbackButton(text="🔍 Поиск", payload="menu:search"))
        notes.insert(0, CallbackButton(text="📚 Мои конспекты", payload="menu:notes"))
    rows = [
        ask,
        [CallbackButton(text="📝 Проверить решение", payload="menu:check")],
        notes,
        [
            CallbackButton(text="🎯 Квиз по теме", payload="menu:quiz"),
            CallbackButton(text="🔁 Квиз по ошибкам", payload="menu:errors"),
        ],
    ]
    if cabinet:
        rows.append([CallbackButton(text="Кабинет", payload="menu:cabinet")])
    return _markup(*rows)


def cabinet_link_keyboard(url: str):
    return _markup([LinkButton(text="Открыть", url=url)])


def back_keyboard():
    return _markup([CallbackButton(text="🏠 Меню", payload="menu:home")])


def grade_keyboard():
    buttons = [
        CallbackButton(text=str(number), payload=f"grade:{number}")
        for number in range(1, 12)
    ]
    return _markup(buttons[:4], buttons[4:8], buttons[8:])


def style_keyboard():
    return _markup(
        [
            CallbackButton(text="Короче", payload="style:short"),
            CallbackButton(text="Подробнее", payload="style:detail"),
        ]
    )


def tutor_keyboard(has_solution: bool):
    second = [CallbackButton(text="Похожий пример", payload="tutor:similar")]
    if has_solution:
        second.append(CallbackButton(text="Показать решение", payload="tutor:solution"))
    return _markup(
        [
            CallbackButton(text="В конспект", payload="tutor:save"),
            CallbackButton(text="Проще", payload="tutor:simpler"),
        ],
        second,
        [CallbackButton(text="🏠 Меню", payload="menu:home")],
    )


def check_keyboard(check_id: int, is_correct: bool):
    actions = []
    if not is_correct:
        actions.append(
            CallbackButton(text="Показать решение", payload=f"check:reveal:{check_id}")
        )
    actions.append(CallbackButton(text="В конспект", payload=f"check:save:{check_id}"))
    return _markup(
        actions,
        [
            CallbackButton(text="Похожая задача", payload=f"check:similar:{check_id}"),
            CallbackButton(text="🏠 Меню", payload="menu:home"),
        ],
    )


def quiz_keyboard(question_id: int, option_count: int):
    letters = [
        CallbackButton(text=LETTERS[index], payload=f"quiz:{question_id}:{index}")
        for index in range(min(option_count, len(LETTERS)))
    ]
    return _markup(letters, [CallbackButton(text="🏠 Меню", payload="menu:home")])


def notes_keyboard(notes: list[dict], page: int, has_prev: bool, has_next: bool):
    keyboard = InlineKeyboardBuilder()
    for note in notes:
        keyboard.row(
            CallbackButton(
                text=button_label(note["title"]),
                payload=f"note:open:{note['id']}",
            )
        )
    navigation = []
    if has_prev:
        navigation.append(CallbackButton(text="Назад", payload=f"note:page:{page - 1}"))
    if has_next:
        navigation.append(CallbackButton(text="Дальше", payload=f"note:page:{page + 1}"))
    if navigation:
        keyboard.row(*navigation)
    keyboard.row(CallbackButton(text="🏠 Меню", payload="menu:home"))
    return keyboard.as_markup()


def note_keyboard(note_id: int):
    return _markup(
        [
            CallbackButton(text="Удалить", payload=f"note:del:{note_id}"),
            CallbackButton(text="К списку", payload="menu:notes"),
        ],
        [CallbackButton(text="🏠 Меню", payload="menu:home")],
    )


def search_keyboard(hits: list[NoteHit]):
    keyboard = InlineKeyboardBuilder()
    for hit in hits:
        keyboard.row(
            CallbackButton(
                text=button_label(hit.title),
                payload=f"note:open:{hit.id}",
            )
        )
    keyboard.row(CallbackButton(text="🏠 Меню", payload="menu:home"))
    return keyboard.as_markup()


def saved_keyboard(note_id: int):
    return _markup(
        [CallbackButton(text="Открыть конспект", payload=f"note:open:{note_id}")],
        [CallbackButton(text="🏠 Меню", payload="menu:home")],
    )
