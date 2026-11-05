import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.textutil import clip, normalize_subject

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
REPLY_LIMIT = 1200
SOLUTION_LIMIT = 1500
QUIZ_OUTPUT_TOKENS = 4096
QUOTA_MESSAGE = "Сейчас много запросов к модели. Подожди минуту и попробуй снова."
RETRY_CAP_SECONDS = 30.0
SERVICE_BACKOFF_SECONDS = 1.5
# Без лимита proxy/API может висеть 2+ минуты — быстрее уходим на запасную.
GEMINI_HTTP_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
# Gemma на Free Tier часто отдаёт 500 на response_schema — просим JSON текстом.
JSON_OBJECT_HINT = (
    "\nВерни один JSON-объект вида "
    '{"title":"строка","questions":[{"prompt":"вопрос","options":["а","б","в","г"],'
    '"correct_index":0,"explanation":"почему","skill":"приём","subject":"Алгебра"}]}.'
    " Поле prompt обязательно. Не возвращай массив верхнего уровня."
)
_RETRY_IN = re.compile(
    r"(?:Please retry in\s+|retryDelay['\":\s]+)(\d+(?:\.\d+)?)\s*s?",
    re.IGNORECASE,
)


class TutorError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def _error_kind(error: BaseException) -> str:
    message = str(error).lower()
    if (
        "429" in message
        or "resource_exhausted" in message
        or "quota exceeded" in message
        or "rate-limit" in message
    ):
        return "quota"
    if (
        "503" in message
        or "unavailable" in message
        or "high demand" in message
        or "500" in message
        or "internal error" in message
        or "timeout" in message
        or "timed out" in message
        or "readtimeout" in message
        or "connecttimeout" in message
    ):
        return "unavailable"
    return "other"


def _retry_seconds(error: BaseException) -> float:
    match = _RETRY_IN.search(str(error))
    if match:
        return float(match.group(1))
    return 20.0


def _thinking_unsupported(error: BaseException) -> bool:
    message = str(error).lower()
    return "thinking" in message or "invalid_argument" in message


def _schema_in_api(model_name: str) -> bool:
    # Gemma 4 на Free Tier с response_schema отвечает 500 INTERNAL.
    return not model_name.startswith("gemma-")


def _fallback_models(raw: str, primary: str) -> list[str]:
    names: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if name and name != primary and name not in names:
            names.append(name)
    return names


def _coerce_quiz_payload(raw: str) -> str:
    """Приводит свободный JSON Gemma к виду _QuizSchema."""
    cleaned = _strip_json(raw)
    try:
        data = json.loads(cleaned)
    except Exception:
        return cleaned
    if isinstance(data, list):
        questions = []
        for item in data:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if "prompt" not in row and "question" in row:
                row["prompt"] = row.pop("question")
            questions.append(row)
        data = {"title": "", "questions": questions}
    elif isinstance(data, dict) and "questions" in data and isinstance(data["questions"], list):
        fixed = []
        for item in data["questions"]:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if "prompt" not in row and "question" in row:
                row["prompt"] = row.pop("question")
            fixed.append(row)
        data = {**data, "questions": fixed}
    return json.dumps(data, ensure_ascii=False)


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
    skill: str = ""
    subject: str = ""


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
    skill: str = ""
    subject: str = ""


class _QuizSchema(BaseModel):
    title: str = ""
    questions: list[_QuestionSchema] = Field(default_factory=list)


class _SearchSchema(BaseModel):
    note_ids: list[int] = Field(default_factory=list)


class _SkillMatchSchema(BaseModel):
    id: int = 0
    name: str = ""


class _TranscriptSchema(BaseModel):
    text: str = ""


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def normalize_quiz(
    pack: _QuizSchema,
    fallback_title: str,
    wanted: int = 6,
) -> tuple[str, list[QuestionDraft]]:
    drafts: list[QuestionDraft] = []
    minimum = 4 if wanted >= 6 else 3
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
                explanation=question.explanation.strip() or "Разбери приём ещё раз.",
                skill=question.skill.strip()[:80],
                subject=normalize_subject(question.subject),
            )
        )
        if len(drafts) == wanted:
            break
    if len(drafts) < minimum:
        raise TutorError("Модель собрала квиз не полностью. Попроси тему ещё раз.")
    title = pack.title.strip() or fallback_title
    return title[:120], drafts


_GOALS = {"oge": "подготовка к ОГЭ", "ege": "подготовка к ЕГЭ", "self": "для себя"}


def _learner_line(grade: int | None, exam_goal: str | None) -> str:
    parts: list[str] = []
    if grade is not None:
        parts.append(f"Класс: {grade}.")
    goal = _GOALS.get(exam_goal or "")
    if goal:
        parts.append(f"Цель: {goal}.")
    return " ".join(parts)


def _strip_json(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        fence = cleaned.rfind("```")
        if fence != -1:
            cleaned = cleaned[:fence]
    return cleaned.strip()


class Tutor:
    def __init__(
        self,
        api_key: str,
        model: str,
        proxy_url: str = "",
        fallback_model: str = "",
    ) -> None:
        self.model = model
        self.fallback_models = _fallback_models(fallback_model, model)
        self.enabled = bool(api_key.strip())
        # По модели: None — ещё не пробовали; False — не принимает ThinkingConfig.
        self._thinking_ok: dict[str, bool | None] = {}
        # The async SDK prefers aiohttp, which treats SOCKS URLs as HTTP proxies.
        client_args = {"timeout": GEMINI_HTTP_TIMEOUT, "trust_env": False}
        async_args = {"timeout": GEMINI_HTTP_TIMEOUT, "trust_env": False}
        if proxy_url:
            client_args["proxy"] = proxy_url
            async_args["proxy"] = proxy_url
        http_options = types.HttpOptions(
            httpx_client=httpx.Client(**client_args),
            httpx_async_client=httpx.AsyncClient(**async_args),
        )
        self._client = (
            genai.Client(api_key=api_key, http_options=http_options) if self.enabled else None
        )

    def _models(self) -> list[str]:
        return [self.model, *self.fallback_models]

    def _require(self) -> genai.Client:
        if self._client is None:
            raise TutorError(
                "Добавьте GEMINI_API_KEY в файл .env и перезапустите бота."
            )
        return self._client

    def _contents_with_system(self, system: str, contents: list) -> list:
        """Gemma на API ломается от system_instruction — кладём инструкцию в user-текст."""
        if not contents:
            return [
                types.Content(role="user", parts=[types.Part.from_text(text=system)])
            ]
        first = contents[0]
        first_text = ""
        extra_parts = []
        if getattr(first, "parts", None):
            first_text = getattr(first.parts[0], "text", None) or ""
            extra_parts = list(first.parts[1:])
        merged = types.Content(
            role="user",
            parts=[types.Part.from_text(text=system + "\n\n" + first_text), *extra_parts],
        )
        return [merged, *contents[1:]]

    async def _generate(
        self,
        system: str,
        contents: list,
        schema: type[BaseModel],
        temperature: float,
        max_output_tokens: int = 2048,
        model: str | None = None,
    ):
        model_name = model or self.model
        client = self._require()
        use_schema = _schema_in_api(model_name)
        if use_schema:
            request_contents = contents
            base: dict = {
                "system_instruction": system,
                "response_mime_type": "application/json",
                "response_schema": schema,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
        else:
            request_contents = self._contents_with_system(
                system + JSON_OBJECT_HINT, contents
            )
            base = {
                "response_mime_type": "application/json",
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
        plain = types.GenerateContentConfig(**base)
        thinking_state = self._thinking_ok.get(model_name)
        if thinking_state is False or not use_schema:
            configs = [plain]
        else:
            configs = [
                types.GenerateContentConfig(
                    **base,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
                plain,
            ]
        last_error: Exception | None = None
        for index, config in enumerate(configs):
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=request_contents,
                    config=config,
                )
                if index == 0 and thinking_state is None and len(configs) > 1:
                    self._thinking_ok[model_name] = True
                return response
            except Exception as error:
                last_error = error
                if (
                    index == 0
                    and len(configs) > 1
                    and _thinking_unsupported(error)
                ):
                    self._thinking_ok[model_name] = False
                    continue
                raise
        raise TutorError("Модель не ответила. Попробуй ещё раз.") from last_error

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
            raise TutorError("Модель вернула пустой ответ. Попробуй ещё раз.")
        payload = text
        if schema is _QuizSchema:
            payload = _coerce_quiz_payload(text)
        try:
            return schema.model_validate_json(
                payload if schema is _QuizSchema else _strip_json(text)
            )
        except Exception as error:
            raise TutorError("Не удалось разобрать ответ модели. Попробуй ещё раз.") from error

    async def _ask_model(
        self,
        model_name: str,
        system: str,
        contents: list,
        schema: type[BaseModel],
        temperature: float,
        max_output_tokens: int,
        wait_on_quota: bool,
    ):
        last_error: Exception | None = None
        # На последней модели даём больше попыток — запасных уже нет.
        attempts = 3 if wait_on_quota else 1
        for attempt in range(attempts):
            try:
                response = await self._generate(
                    system,
                    contents,
                    schema,
                    temperature,
                    max_output_tokens,
                    model=model_name,
                )
                return self._parse(response, schema)
            except TutorError as error:
                if "GEMINI_API_KEY" in error.user_message:
                    raise
                last_error = error
                log.warning(
                    "Gemini parse attempt %s on %s failed: %s",
                    attempt + 1,
                    model_name,
                    error,
                )
            except Exception as error:
                last_error = error
                kind = _error_kind(error)
                log.warning(
                    "Gemini attempt %s on %s failed: %s",
                    attempt + 1,
                    model_name,
                    error,
                )
                # Есть запасная модель — не тратим время на повтор этой же.
                if not wait_on_quota and kind in {"quota", "unavailable"}:
                    if kind == "quota":
                        raise TutorError(QUOTA_MESSAGE) from error
                    raise TutorError("Модель не ответила. Попробуй ещё раз.") from error
                if attempt + 1 < attempts:
                    if kind == "quota":
                        delay = min(_retry_seconds(error), RETRY_CAP_SECONDS)
                        await asyncio.sleep(delay)
                        continue
                    if kind == "unavailable":
                        await asyncio.sleep(SERVICE_BACKOFF_SECONDS * (attempt + 1))
                        continue
                if kind == "quota":
                    raise TutorError(QUOTA_MESSAGE) from error
        if isinstance(last_error, TutorError):
            if _error_kind(last_error) == "quota":
                raise TutorError(QUOTA_MESSAGE) from last_error
            raise last_error
        if last_error is not None and _error_kind(last_error) == "quota":
            raise TutorError(QUOTA_MESSAGE) from last_error
        raise TutorError("Модель не ответила. Попробуй ещё раз.") from last_error

    async def _ask(
        self,
        system: str,
        contents: list,
        schema: type[BaseModel],
        temperature: float,
        max_output_tokens: int = 2048,
    ):
        models = self._models()
        last_error: Exception | None = None
        for index, model_name in enumerate(models):
            has_next = index + 1 < len(models)
            try:
                return await self._ask_model(
                    model_name,
                    system,
                    contents,
                    schema,
                    temperature,
                    max_output_tokens,
                    wait_on_quota=not has_next,
                )
            except TutorError as error:
                if "GEMINI_API_KEY" in error.user_message:
                    raise
                last_error = error
                if has_next:
                    log.warning(
                        "model %s failed, trying fallback %s: %s",
                        model_name,
                        models[index + 1],
                        error.user_message,
                    )
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise TutorError("Модель не ответила. Попробуй ещё раз.")

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
            raise TutorError("Модель не прислала объяснение. Спроси ещё раз.")
        return TutorReply(
            subject=normalize_subject(data.subject),
            title=data.title.strip()[:80] or reply[:80],
            reply=clip(reply, REPLY_LIMIT),
            full_solution=clip(data.full_solution.strip(), SOLUTION_LIMIT),
            skill=data.skill.strip()[:80],
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

    async def transcribe(self, audio: bytes, mime: str) -> str:
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=audio, mime_type=mime),
                    types.Part.from_text(text="Расшифруй это голосовое."),
                ],
            )
        ]
        data = await self._ask(load_prompt("transcribe.txt"), contents, _TranscriptSchema, 0.1)
        text = data.text.strip()
        if not text:
            raise TutorError("Не разобрала речь. Скажи чётче или напиши текстом.")
        return clip(text, 4000)

    def _to_check(self, data: _CheckSchema) -> CheckResult:
        return CheckResult(
            is_schoolwork=bool(data.is_schoolwork),
            subject=normalize_subject(data.subject),
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
        fallback: str | None = None,
        wanted: int = 6,
        grade: int | None = None,
        exam_goal: str | None = None,
    ) -> tuple[str, list[QuestionDraft]]:
        if errors:
            packed = json.dumps(errors, ensure_ascii=False)
            user_text = "Собери квиз по этим ошибкам ученика. Задачи должны быть новыми.\n" + packed[:5000]
            title = fallback or "По моим ошибкам"
        else:
            user_text = "Тема квиза: " + (topic or "школьная математика")
            title = fallback or topic or "Квиз"
        learner = _learner_line(grade, exam_goal)
        if learner:
            user_text += "\n" + learner
        user_text += f"\nНужно ровно {wanted} вопросов."
        started = time.monotonic()
        try:
            data = await self._ask(
                load_prompt("quiz.txt"),
                self._text_contents([], user_text),
                _QuizSchema,
                0.4,
                max_output_tokens=QUIZ_OUTPUT_TOKENS,
            )
            quiz_title, drafts = normalize_quiz(data, title[:120], wanted)
        except TutorError as error:
            if "GEMINI_API_KEY" in error.user_message or error.user_message == QUOTA_MESSAGE:
                raise
            raise TutorError("Не получилось собрать квиз. Назови тему ещё раз.") from error
        log.info(
            "quiz generated in %.1fs, questions=%s",
            time.monotonic() - started,
            len(drafts),
        )
        return quiz_title, drafts

    async def match_skill(self, raw: str, known: list[dict]) -> tuple[int, str] | None:
        catalog = [{"id": item["id"], "name": item["name"], "subject": item["subject"]} for item in known]
        user_text = (
            f"Приём: {raw}\nНавыки:\n" + json.dumps(catalog, ensure_ascii=False)[:4000]
        )
        data = await self._ask(
            load_prompt("skill.txt"),
            self._text_contents([], user_text),
            _SkillMatchSchema,
            0.1,
        )
        return int(data.id), data.name.strip()

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
