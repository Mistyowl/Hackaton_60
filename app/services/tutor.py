import json
import logging
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


class TutorError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@dataclass
class TutorReply:
    subject: str
    title: str
    reply: str
    full_solution: str
    skill: str

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "title": self.title,
            "reply": self.reply,
            "full_solution": self.full_solution,
            "skill": self.skill,
        }


@dataclass
class CheckResult:
    is_schoolwork: bool
    subject: str
    problem_text: str
    student_solution_text: str
    is_correct: bool
    error_step: str
    hint: str
    full_explanation: str
    skill: str
    praise: str


@dataclass
class QuestionDraft:
    prompt: str
    options: list[str]
    correct_index: int
    explanation: str


class _TutorSchema(BaseModel):
    subject: str = ""
    title: str = ""
    reply: str = ""
    full_solution: str = ""
    skill: str = ""


class _CheckSchema(BaseModel):
    is_schoolwork: bool = False
    subject: str = ""
    problem_text: str = ""
    student_solution_text: str = ""
    is_correct: bool = False
    error_step: str = ""
    hint: str = ""
    full_explanation: str = ""
    skill: str = ""
    praise: str = ""


class _QuestionSchema(BaseModel):
    prompt: str
    options: list[str]
    correct_index: int
    explanation: str = ""


class _QuizSchema(BaseModel):
    title: str = ""
    questions: list[_QuestionSchema] = Field(default_factory=list)


class _SearchSchema(BaseModel):
    note_ids: list[int] = Field(default_factory=list)


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def normalize_quiz(pack: _QuizSchema, fallback_title: str) -> tuple[str, list[QuestionDraft]]:
    drafts: list[QuestionDraft] = []
    for question in pack.questions:
        options = [option.strip() for option in question.options if option and option.strip()]
        if len(options) < 4:
            continue
        options = options[:4]
        if not 0 <= question.correct_index <= 3:
            continue
        prompt = question.prompt.strip()
        if not prompt:
            continue
        drafts.append(
            QuestionDraft(
                prompt=prompt,
                options=options,
                correct_index=question.correct_index,
                explanation=question.explanation.strip() or "Разберите приём ещё раз.",
            )
        )
        if len(drafts) == 6:
            break
    if len(drafts) < 4:
        raise TutorError("Модель собрала квиз не полностью. Попросите тему ещё раз.")
    title = pack.title.strip() or fallback_title
    return title[:120], drafts


def _strip_json(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        fence = cleaned.rfind("```")
        if fence != -1:
            cleaned = cleaned[:fence]
    return cleaned.strip()


class Tutor:
    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self.enabled = bool(api_key.strip())
        self._client = genai.Client(api_key=api_key) if self.enabled else None

    def _require(self) -> genai.Client:
        if self._client is None:
            raise TutorError(
                "Добавьте GEMINI_API_KEY в файл .env и перезапустите бота."
            )
        return self._client

    async def _generate(self, system: str, contents: list, schema: type[BaseModel], temperature: float):
        client = self._require()
        base = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": temperature,
            "max_output_tokens": 2048,
        }
        configs = [
            types.GenerateContentConfig(
                **base,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
            types.GenerateContentConfig(**base),
        ]
        last_error: Exception | None = None
        for index, config in enumerate(configs):
            try:
                return await client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as error:
                last_error = error
                message = str(error).lower()
                unsupported_thinking = index == 0 and (
                    "thinking" in message or "invalid_argument" in message
                )
                if unsupported_thinking:
                    continue
                raise
        raise TutorError("Модель не ответила. Попробуйте ещё раз.") from last_error

    def _parse(self, response, schema: type[BaseModel]):
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        if isinstance(parsed, BaseModel):
            return schema.model_validate(parsed.model_dump())
        if isinstance(parsed, dict):
            return schema.model_validate(parsed)
        text = getattr(response, "text", None) or ""
        if not text.strip():
            raise TutorError("Модель вернула пустой ответ. Попробуйте ещё раз.")
        try:
            return schema.model_validate_json(_strip_json(text))
        except Exception as error:
            raise TutorError("Не удалось разобрать ответ модели. Попробуйте ещё раз.") from error

    async def _ask(self, system: str, contents: list, schema: type[BaseModel], temperature: float):
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._generate(system, contents, schema, temperature)
                return self._parse(response, schema)
            except TutorError as error:
                if "GEMINI_API_KEY" in error.user_message:
                    raise
                last_error = error
                log.warning("Gemini parse attempt %s failed: %s", attempt + 1, error)
            except Exception as error:
                last_error = error
                log.warning("Gemini attempt %s failed: %s", attempt + 1, error)
        if isinstance(last_error, TutorError):
            raise last_error
        raise TutorError("Модель не ответила. Попробуйте ещё раз.") from last_error

    def _text_contents(self, history: list[dict], user_text: str) -> list:
        items: list[dict] = []
        for message in history:
            role = "user" if message["role"] == "user" else "model"
            items.append({"role": role, "text": message["text"][:2000]})
        items.append({"role": "user", "text": user_text})
        merged: list[dict] = []
        for item in items:
            if merged and merged[-1]["role"] == item["role"]:
                merged[-1]["text"] += "\n" + item["text"]
            else:
                merged.append(dict(item))
        if merged and merged[0]["role"] != "user":
            merged.insert(0, {"role": "user", "text": "Продолжаем занятие."})
        return [
            types.Content(
                role=item["role"],
                parts=[types.Part.from_text(text=item["text"])],
            )
            for item in merged
        ]

    def _with_dossier(self, user_text: str, dossier: str) -> str:
        cleaned = dossier.strip()
        if not cleaned:
            return user_text
        return user_text + "\n\n" + cleaned

    def _to_reply(self, data: _TutorSchema) -> TutorReply:
        reply = data.reply.strip()
        if not reply:
            raise TutorError("Модель не прислала объяснение. Спросите ещё раз.")
        return TutorReply(
            subject=data.subject.strip() or "Школьный предмет",
            title=data.title.strip()[:80] or reply[:80],
            reply=reply,
            full_solution=data.full_solution.strip(),
            skill=data.skill.strip(),
        )

    async def answer(self, history: list[dict], user_text: str, dossier: str = "") -> TutorReply:
        data = await self._ask(
            load_prompt("tutor.txt"),
            self._text_contents(history, self._with_dossier(user_text, dossier)),
            _TutorSchema,
            0.4,
        )
        return self._to_reply(data)

    async def simplify(self, previous: str, dossier: str = "") -> TutorReply:
        contents = self._text_contents(
            [],
            self._with_dossier("Прошлый разбор:\n" + previous[:3000], dossier),
        )
        data = await self._ask(load_prompt("simplify.txt"), contents, _TutorSchema, 0.4)
        return self._to_reply(data)

    async def similar(self, skill: str, context: str, dossier: str = "") -> TutorReply:
        text = f"Приём: {skill}\nКонтекст:\n{context[:2500]}"
        data = await self._ask(
            load_prompt("similar.txt"),
            self._text_contents([], self._with_dossier(text, dossier)),
            _TutorSchema,
            0.7,
        )
        return self._to_reply(data)

    async def check_text(self, solution: str) -> CheckResult:
        contents = self._text_contents([], "Проверь это решение:\n" + solution[:4000])
        data = await self._ask(load_prompt("check.txt"), contents, _CheckSchema, 0.2)
        return self._to_check(data)

    async def check_image(self, image: bytes, mime: str, caption: str) -> CheckResult:
        prompt = caption.strip() or "Проверь учебное решение на фото."
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=image, mime_type=mime),
                    types.Part.from_text(text=prompt[:1000]),
                ],
            )
        ]
        data = await self._ask(load_prompt("check.txt"), contents, _CheckSchema, 0.2)
        return self._to_check(data)

    def _to_check(self, data: _CheckSchema) -> CheckResult:
        return CheckResult(
            is_schoolwork=bool(data.is_schoolwork),
            subject=data.subject.strip(),
            problem_text=data.problem_text.strip(),
            student_solution_text=data.student_solution_text.strip(),
            is_correct=bool(data.is_correct),
            error_step=data.error_step.strip(),
            hint=data.hint.strip(),
            full_explanation=data.full_explanation.strip(),
            skill=data.skill.strip(),
            praise=data.praise.strip(),
        )

    async def make_quiz(
        self,
        topic: str | None,
        errors: list[dict] | None,
    ) -> tuple[str, list[QuestionDraft]]:
        if errors:
            packed = json.dumps(errors, ensure_ascii=False)
            user_text = "Собери квиз по этим ошибкам ученика. Задачи должны быть новыми.\n" + packed[:5000]
            fallback = "По моим ошибкам"
        else:
            user_text = "Тема квиза: " + (topic or "школьная математика")
            fallback = topic or "Квиз"
        last_error: Exception | None = None
        for _ in range(2):
            try:
                data = await self._ask(
                    load_prompt("quiz.txt"),
                    self._text_contents([], user_text),
                    _QuizSchema,
                    0.6,
                )
                return normalize_quiz(data, fallback)
            except TutorError as error:
                if "GEMINI_API_KEY" in error.user_message:
                    raise
                last_error = error
        raise TutorError("Не получилось собрать квиз. Назовите тему ещё раз.") from last_error

    async def pick_notes(self, query: str, cards: list[dict]) -> list[int]:
        if not cards:
            return []
        catalog = [
            {
                "id": card["id"],
                "title": card["title"],
                "subject": card["subject"],
                "preview": card["preview"],
            }
            for card in cards
        ]
        known = {card["id"] for card in cards}
        user_text = (
            f"Запрос: {query}\nКонспекты:\n"
            + json.dumps(catalog, ensure_ascii=False)[:6000]
        )
        data = await self._ask(
            load_prompt("search.txt"),
            self._text_contents([], user_text),
            _SearchSchema,
            0.1,
        )
        picked: list[int] = []
        for note_id in data.note_ids:
            if note_id in known and note_id not in picked:
                picked.append(note_id)
            if len(picked) == 5:
                break
        return picked
