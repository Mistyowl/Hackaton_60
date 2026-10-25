import json

from app.services.notes import NoteHit
from app.services.quiz import AnswerView, question_options
from app.services.tutor import CheckResult
from app.textutil import LETTERS, esc, format_elapsed, format_stamp, model_html

GRADE_PROMPT = "В каком ты классе? Выбери цифру от 5 до 11."

GRADE_NUDGE = "Выбери цифру от 5 до 11."

STYLE_PROMPT = (
    "Как объяснять? «Короче» — короткими фразами. "
    "«Подробнее» — чуть длиннее и с примером, без готового ответа."
)

WELCOME = (
    "⭐Привет! Я Люми - твой надежный помощник в учебе.\n\n"
    "Что я умею:\n"
    "📸 Проверять решения по фото\n"
    "🧠 Объяснять сложные темы простым языком\n"
    "📝 Сохранять конспекты и делать квизы по твоим ошибкам"
)

ASK_PROMPT = (
    "Напишите вопрос своими словами. Я разберу шаги и не выдам готовый ответ, "
    "пока вы сами не попросите."
)

CHECK_PROMPT = (
    "Пришлите фото тетради или текст решения. "
    "В обычном чате можно начать сообщение со слова «проверь»."
)

SEARCH_PROMPT = "Напишите слово или фразу из конспекта."

QUIZ_PROMPT = "Напишите тему квиза. Например: квадратные уравнения."

OWN_PROMPT = (
    "Пришлите текст конспекта одним сообщением. Первая строка станет заголовком."
)

EMPTY_NOTES = (
    "Конспектов пока нет. Нажмите «В конспект» под ответом или сохраните свой текст."
)

EMPTY_ERRORS = (
    "Банк ошибок пуст. Пришлите решение с ошибкой — по этому пробелу я соберу отдельный квиз."
)


def render_check(result: CheckResult) -> str:
    if not result.is_schoolwork:
        return (
            "Не вижу учебное задание. Пришлите фото условия и решения "
            "или напишите его текстом."
        )
    lines: list[str] = []
    if result.subject:
        lines.append(f"<b>Предмет:</b> {model_html(result.subject)}")
    if result.is_correct:
        lines.append("<b>Решение верное.</b>")
        if result.praise:
            lines.append(model_html(result.praise))
    else:
        lines.append("<b>В решении есть ошибка.</b>")
        if result.error_step:
            lines.append("<b>Шаг:</b> " + model_html(result.error_step))
        if result.hint:
            lines.append(model_html(result.hint))
        lines.append("Полный разбор откроется кнопкой «Показать решение».")
    if result.skill:
        lines.append("<b>Приём:</b> " + model_html(result.skill))
    if result.star_line:
        lines.append(esc(result.star_line))
    return "\n\n".join(lines)


def render_notes_page(notes: list[dict], page: int, total: int) -> str:
    if total == 0:
        return EMPTY_NOTES
    lines = [f"Конспекты, страница {page + 1}. Всего {total}."]
    for index, note in enumerate(notes, start=1):
        subject = note["subject"] or "без предмета"
        lines.append(f"{index}. {note['title']} · {subject}")
    lines.append("Нажмите название, чтобы открыть.")
    return "\n".join(esc(line) for line in lines)


def render_note(note: dict) -> str:
    lines = [f"<b>{esc(note['title'])}</b>"]
    if note["subject"]:
        lines.append(esc(note["subject"]))
    lines.append(esc(format_stamp(note["created_at"])))
    lines.append("")
    lines.append(model_html(note["body"]))
    return "\n".join(lines)


def render_search(query: str, hits: list[NoteHit]) -> str:
    if not hits:
        return esc(f"В конспектах ничего не нашлось по запросу «{query}».")
    parts = [esc(f"Нашла по запросу «{query}»."), ""]
    for hit in hits:
        parts.append(f"<b>{esc(hit.title)}</b>")
        if hit.subject:
            parts.append(esc(hit.subject))
        parts.append(hit.snippet_html)
        if hit.semantic:
            parts.append("Совпадение по смыслу, точного слова нет.")
        parts.append("")
    return "\n".join(parts).strip()


def render_question(title: str, started_at: str, done: int, total: int, question: dict) -> str:
    options = json.loads(question["options_json"])
    lines = [
        f"<b>{esc(f'Квиз «{title}»')}</b>",
        esc(f"Закрыто {done} из {total} · прошло {format_elapsed(started_at)}"),
        "",
        esc(question["prompt"]),
        "",
    ]
    for index, option in enumerate(options):
        letter = LETTERS[index] if index < len(LETTERS) else str(index + 1)
        lines.append(esc(f"{letter}) {option}"))
    return "\n".join(lines)


def render_feedback(view: AnswerView) -> str:
    if view.correct:
        text = "<b>Верно, задача закрыта.</b>"
    else:
        text = "<b>Пока неверно.</b> Задача вернётся в конец очереди."
    if view.explanation:
        text += " " + model_html(view.explanation)
    if view.star:
        text += "\n" + esc(view.star)
    return text


def render_finished(view: AnswerView) -> str:
    prefix = "<b>Верно.</b> " if view.correct else ""
    return (
        f"{prefix}Квиз <b>{esc(view.quiz_title)}</b> пройден. "
        + esc(f"Закрыто {view.done} из {view.total} · прошло {view.elapsed}.")
    )


def option_count(question: dict) -> int:
    return len(question_options(question))
