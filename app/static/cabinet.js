const LOCKED = "Откройте кабинет кнопкой в чате MAX.";
const EMPTY_NOTES = "Конспекты появятся, когда в чате нажмёшь «В конспект».";
const EMPTY_ERRORS = "Банк ошибок пуст. Пришли решение с ошибкой — по этому пробелу я соберу отдельный квиз.";
const BOOK = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';
const CHEV = '<span class="chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg></span>';
const PIN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 4h6l-1 7 3 3v2H7v-2l3-3-1-7z"/><path d="M12 16v5"/></svg>';
const MARK = '<span class="mark"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg></span>';
const CHECK_MINI = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg>';
const SWIPE_REVEAL = 118;

const gate = document.querySelector("#gate");
const cabinet = document.querySelector("#cabinet");
const tabs = document.querySelector("#tabs");
const nameInput = document.querySelector("#name-input");
const gradeInput = document.querySelector("#grade-input");
const gradeToggle = document.querySelector("#grade-toggle");
const gradeMenu = document.querySelector("#grade-menu");
const gradeLabel = document.querySelector("#grade-label");
const saveButton = document.querySelector("#save-profile");
const saveStatus = document.querySelector("#save-status");
const remindersInput = document.querySelector("#reminders-input");

const state = {
  profile: { first_name: "", grade: null, explain_style: null, reminders: true },
  notes: [],
  errors: [],
  sky: null,
  selectedNoteId: null,
  noteReturn: "notes",
  quizReturn: "home",
  quizKind: "topic",
  screen: "home",
  star: null
};
let quiz = null;
let currentNote = null;
let quizToken = 0;
let noteTicket = 0;
let errorsTicket = 0;
let searchTicket = 0;
const deletingNotes = new Set();
const pinningNotes = new Set();
let swipeDrag = null;
let pendingDelete = null;

function bridge() {
  return window.WebApp || null;
}

function haptic(kind) {
  const app = bridge();
  const feedback = app && app.HapticFeedback;
  if (!feedback) return;
  if (kind === "ok" && feedback.notificationOccurred) feedback.notificationOccurred("success");
  else if (kind === "bad" && feedback.notificationOccurred) feedback.notificationOccurred("error");
  else if (feedback.impactOccurred) feedback.impactOccurred("light");
}

function confirmClose(on) {
  const app = bridge();
  if (!app) return;
  if (on && app.enableClosingConfirmation) app.enableClosingConfirmation();
  if (!on && app.disableClosingConfirmation) app.disableClosingConfirmation();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;"
  })[char]);
}

function plain(value) {
  return String(value || "").replace(/\*\*/g, "").replace(/`/g, "").replace(/\s+/g, " ").trim();
}

function detailText(data, fallback) {
  return data && typeof data.detail === "string" ? data.detail : fallback;
}

function profileName() {
  const name = String(state.profile.first_name || "").trim();
  return name || "Ученик";
}

function metaLine(subject) {
  const name = subject || "без предмета";
  if (state.profile.grade == null) return name;
  return name + " · " + state.profile.grade + " класс";
}

function showGate(text) {
  cabinet.hidden = true;
  tabs.hidden = true;
  gate.hidden = false;
  gate.textContent = text;
}

function showCabinet() {
  gate.hidden = true;
  cabinet.hidden = false;
  tabs.hidden = false;
}

function tabFor(screen) {
  if (screen === "notes" || screen === "note") return "notes";
  if (screen === "quiz" || screen.startsWith("quiz-")) return "quiz";
  if (screen === "sky") return "sky";
  return "home";
}

function closeGradeMenu() {
  gradeMenu.hidden = true;
  gradeToggle.setAttribute("aria-expanded", "false");
}

function closeConfirm() {
  pendingDelete = null;
  document.querySelector("#confirm-delete").hidden = true;
}

function closeStar() {
  state.star = null;
  document.querySelector("#star-sheet").hidden = true;
}

function closeSwipes() {
  document.querySelectorAll(".swipe").forEach((row) => setSwipeOpen(row, ""));
}

function showScreen(name) {
  closeGradeMenu();
  closeSwipes();
  closeConfirm();
  if (name !== "sky") closeStar();
  state.screen = name;
  document.querySelectorAll("[data-screen]").forEach((section) => {
    section.hidden = section.dataset.screen !== name;
  });
  const tab = tabFor(name);
  tabs.querySelectorAll("[data-tab]").forEach((button) => {
    const on = button.dataset.tab === tab;
    button.classList.toggle("active", on);
    if (on) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const roots = ["sky", "home", "notes", "quiz"];
  const app = bridge();
  if (app && app.BackButton) {
    if (roots.includes(name)) {
      if (app.BackButton.hide) app.BackButton.hide();
    } else if (app.BackButton.show) {
      app.BackButton.show();
    }
  }
  confirmClose(name === "quiz-play" || name === "quiz-loading");
}

function goBack() {
  const screen = state.screen;
  if (screen === "note" && !document.querySelector("#note-form").hidden) {
    cancelNoteEdit();
    return;
  }
  if (screen === "note") showScreen(state.noteReturn || "notes");
  else if (screen === "quiz-play" || screen === "quiz-loading") showScreen(state.quizKind === "errors" ? "quiz-errors" : state.quizKind === "review" ? "sky" : "quiz-topic");
  else if (screen === "quiz-topic" || screen === "quiz-errors") showScreen(state.quizReturn || "home");
  else if (screen === "profile") showScreen("home");
  else showScreen("home");
}

function paintIdentity() {
  document.querySelector("#home-name").textContent = profileName();
  const chip = document.querySelector("#home-grade-text");
  if (state.profile.grade == null) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  chip.textContent = state.profile.grade + " класс";
}

function emptyBlock(text) {
  return `<div class="empty-card"><img class="mascot" src="/static/lumi-support.svg" alt=""><p>${escapeHtml(text)}</p></div>`;
}

function noteCard(note, mode) {
  const select = mode === "select";
  const selected = select && String(note.id) === String(state.selectedNoteId);
  const preview = mode === "read" && note.preview
    ? `<span class="preview">${escapeHtml(plain(note.preview))}</span>`
    : "";
  return `<button type="button" class="card${selected ? " selected" : ""}" ${select ? "data-select" : "data-note"}="${escapeHtml(note.id)}"${select ? ` aria-pressed="${selected ? "true" : "false"}"` : ""}>
    <span class="icon">${BOOK}</span>
    <span class="copy">
      <h3>${note.pinned ? `<span class="pin" title="Закреплено">${PIN}</span>` : ""}${escapeHtml(note.title || "Конспект")}</h3>
      <span class="meta">${escapeHtml(metaLine(note.subject))}</span>
      ${preview}
    </span>
    ${selected ? MARK : CHEV}
  </button>`;
}

function swipeCard(note, mode) {
  const id = escapeHtml(note.id);
  const pinLabel = note.pinned ? "Открепить" : "Закрепить";
  return `<div class="swipe" data-swipe="${id}">
    <button type="button" class="swipe-pin" data-pin="${id}" tabindex="-1" aria-hidden="true">${pinLabel}</button>
    <button type="button" class="swipe-delete" data-delete="${id}" tabindex="-1" aria-hidden="true">Удалить</button>
    <div class="swipe-front">${noteCard(note, mode)}</div>
  </div>`;
}

function showSwipeHint() {
  const hint = document.querySelector("#swipe-hint");
  hint.hidden = localStorage.getItem("lumi-swipe-hint") === "1" || !state.notes.length;
}

function paintHome() {
  paintIdentity();
  const box = document.querySelector("#home-notes");
  const all = document.querySelector("#show-all");
  if (!state.notes.length) {
    all.hidden = true;
    box.innerHTML = emptyBlock("Сфоткай задачу в чате, и здесь появится первый конспект.");
    showSwipeHint();
    return;
  }
  all.hidden = false;
  box.innerHTML = state.notes.slice(0, 3).map((note) => swipeCard(note, "home")).join("");
  showSwipeHint();
  localStorage.setItem("lumi-swipe-hint", "1");
}

function paintNotes() {
  const box = document.querySelector("#notes-list");
  if (!state.notes.length) {
    box.innerHTML = emptyBlock(EMPTY_NOTES);
    return;
  }
  box.innerHTML = state.notes.map((note) => swipeCard(note, "read")).join("");
}

function paintTopics() {
  const box = document.querySelector("#topic-list");
  const button = document.querySelector("#start-topic");
  if (!state.notes.length) {
    state.selectedNoteId = null;
    box.innerHTML = emptyBlock(EMPTY_NOTES);
    button.disabled = true;
    return;
  }
  if (!state.notes.some((note) => String(note.id) === String(state.selectedNoteId))) state.selectedNoteId = null;
  box.innerHTML = state.notes.map((note) => noteCard(note, "select")).join("");
  button.disabled = state.selectedNoteId == null;
}

function setGrade(value, notify) {
  const grade = value == null || value === "" ? "" : String(value);
  gradeInput.value = grade;
  gradeLabel.textContent = grade ? grade + " класс" : "Выбери класс";
  gradeMenu.querySelectorAll("[data-grade]").forEach((button) => {
    button.setAttribute("aria-selected", button.dataset.grade === grade ? "true" : "false");
  });
  if (notify) {
    if (saveButton.textContent !== "Сохранить") saveButton.textContent = "Сохранить";
    previewFromForm();
  }
}

function previewFromForm() {
  const name = nameInput.value.trim() || "Ученик";
  const grade = gradeInput.value;
  document.querySelector("#preview-name").textContent = name;
  document.querySelector("#preview-meta").textContent = grade ? "Ученик · " + grade + " класс" : "Ученик";
}

function paintStyle(style) {
  document.querySelectorAll("[data-style]").forEach((button) => {
    button.classList.toggle("active", button.dataset.style === style);
  });
}

function fillProfileForm() {
  nameInput.value = state.profile.first_name || "";
  setGrade(state.profile.grade, false);
  paintStyle(state.profile.explain_style || "");
  remindersInput.checked = state.profile.reminders !== false;
  saveButton.textContent = "Сохранить";
  saveStatus.textContent = "";
  saveStatus.className = "status";
  previewFromForm();
}

function renderInline(text) {
  return escapeHtml(text)
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`\n]+)`/g, '<span class="formula">$1</span>');
}

function renderNoteBody(body) {
  const labels = ["Условие", "Решение ученика", "Разбор", "Полный разбор", "Приём"];
  const blocks = [];
  let title = "";
  let lines = [];
  String(body || "").split("\n").forEach((line) => {
    const match = line.match(/^(Условие|Решение ученика|Разбор|Полный разбор|Приём):\s*(.*)$/);
    if (match && labels.includes(match[1])) {
      if (title || lines.length) blocks.push({ title, lines });
      title = match[1];
      lines = match[2] ? [match[2]] : [];
      return;
    }
    lines.push(line);
  });
  if (title || lines.length) blocks.push({ title, lines });
  return blocks.map((block) => {
    const html = renderInline(block.lines.join("\n")).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
    const heading = block.title ? `<strong>${escapeHtml(block.title)}</strong>` : "";
    return `<div class="note-block">${heading}<p>${html}</p></div>`;
  }).join("");
}

function highlight(text, query) {
  const safe = escapeHtml(plain(text));
  const needle = plain(query);
  if (!needle) return safe;
  const pattern = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safe.replace(new RegExp(pattern, "ig"), (found) => `<b>${found}</b>`);
}

async function openNote(id, back) {
  const ticket = ++noteTicket;
  state.noteReturn = back;
  const response = await fetch("/api/notes/" + encodeURIComponent(id));
  if (ticket !== noteTicket) return;
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) return;
  const note = await response.json();
  currentNote = note;
  document.querySelector("#note-form").hidden = true;
  document.querySelector("#note-body").hidden = false;
  document.querySelector("#edit-note").hidden = false;
  document.querySelector("#note-title").textContent = note.title || "Конспект";
  document.querySelector("#note-meta").textContent = metaLine(note.subject);
  document.querySelector("#note-body").innerHTML = renderNoteBody(note.body || "");
  showScreen("note");
}

function openNotes() {
  paintNotes();
  showScreen("notes");
}

function openTopicQuiz(from) {
  state.quizReturn = from === "quiz" ? "quiz" : "home";
  paintTopics();
  showScreen("quiz-topic");
}

function openProfile() {
  fillProfileForm();
  showScreen("profile");
}

async function openErrors(from) {
  const ticket = ++errorsTicket;
  state.quizReturn = from === "quiz" ? "quiz" : "home";
  showScreen("quiz-errors");
  const box = document.querySelector("#error-list");
  const status = document.querySelector("#error-status");
  const button = document.querySelector("#start-errors");
  const heading = document.querySelector("#error-heading");
  button.disabled = true;
  heading.hidden = true;
  status.className = "status";
  status.textContent = "";
  const response = await fetch("/api/errors");
  if (ticket !== errorsTicket) return;
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) {
    box.innerHTML = "";
    status.className = "status error";
    status.textContent = "Не удалось собрать темы.";
    return;
  }
  const rows = await response.json();
  state.errors = Array.isArray(rows) ? rows : [];
  if (!state.errors.length) {
    box.innerHTML = emptyBlock(EMPTY_ERRORS);
    button.disabled = true;
    return;
  }
  heading.hidden = false;
  box.innerHTML = state.errors.map((row) => `
    <article class="card plain">
      <span class="icon">${BOOK}</span>
      <span class="copy"><h3>${escapeHtml(row.subject || "Без предмета")}</h3><span class="meta">${escapeHtml(row.mistake || "")}</span></span>
    </article>
  `).join("");
  button.disabled = false;
}

function splitPrompt(prompt) {
  const lines = String(prompt || "").split(/\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length >= 2) return { caption: lines[0], main: lines.slice(1).join("\n") };
  return { caption: "", main: String(prompt || "") };
}

function promptHtml(prompt) {
  const parts = splitPrompt(prompt);
  const caption = parts.caption ? `<div class="caption">${escapeHtml(parts.caption)}</div>` : "";
  const long = parts.main.length > 42;
  return `${caption}<div class="eq${long ? " long" : ""}">${escapeHtml(parts.main)}</div>`;
}

function paintQuiz() {
  const live = document.querySelector("#quiz-live");
  const results = document.querySelector("#quiz-results");
  const button = document.querySelector("#quiz-go");
  document.querySelector("#quiz-kicker").textContent = quiz && quiz.kind === "errors" ? "Тренировка" : quiz && quiz.kind === "review" ? "Повторение" : "Квиз по конспектам";
  document.querySelector("#quiz-title").textContent = quiz && quiz.title ? quiz.title : "Квиз";
  if (quiz && quiz.finished && quiz.feedback) {
    live.hidden = true;
    results.hidden = false;
    button.hidden = true;
    const perfect = !quiz.misses.length;
    document.querySelector("#result-mascot").src = perfect ? "/static/lumi-happy.svg" : "/static/lumi-support.svg";
    document.querySelector("#result-title").textContent = perfect ? "Небо стало ярче" : "Квиз пройден";
    const elapsed = quiz.elapsed ? " · " + quiz.elapsed : "";
    document.querySelector("#result-score").textContent = "Закрыто " + (quiz.done || 0) + " из " + (quiz.total || 0) + elapsed;
    const star = document.querySelector("#result-star");
    star.hidden = !quiz.star;
    star.textContent = quiz.star || "";
    document.querySelector("#result-misses").innerHTML = (quiz.misses || []).map((miss) => `
      <article class="miss"><strong>${escapeHtml(miss.prompt || "")}</strong><span>${escapeHtml(miss.explanation || "")}</span></article>
    `).join("");
    document.querySelector("#quiz-retry").hidden = quiz.kind === "review" && !quiz.misses.length;
    return;
  }
  live.hidden = false;
  results.hidden = true;
  button.hidden = false;
  const progress = document.querySelector("#quiz-progress");
  const bar = document.querySelector("#quiz-bar");
  const fill = document.querySelector("#quiz-bar-fill");
  const card = document.querySelector("#quiz-card");
  const heading = document.querySelector("#quiz-answers-title");
  const options = document.querySelector("#quiz-options");
  const feedback = document.querySelector("#quiz-feedback");
  if (!quiz || !quiz.shown) {
    progress.hidden = true;
    bar.hidden = true;
    heading.hidden = true;
    options.innerHTML = "";
    card.innerHTML = `<div class="eq long">${escapeHtml(quiz && quiz.finished ? "Квиз пройден." : "Вопрос не пришёл.")}</div>`;
    feedback.hidden = true;
    button.textContent = "В кабинет";
    button.disabled = Boolean(quiz && quiz.busy);
    return;
  }
  const total = quiz.total || (quiz.shown.options || []).length || 1;
  const number = quiz.number || 1;
  progress.hidden = false;
  bar.hidden = false;
  progress.textContent = "Вопрос " + number + " из " + total;
  fill.style.width = Math.min(100, Math.round(((number - 1) / total) * 100)) + "%";
  heading.hidden = false;
  card.innerHTML = promptHtml(quiz.shown.prompt);
  options.innerHTML = (quiz.shown.options || []).map((option, index) => {
    const picked = quiz.picked === index;
    return `<button type="button" class="option${picked ? " picked" : ""}" data-option="${index}" aria-pressed="${picked ? "true" : "false"}"${quiz.feedback || quiz.busy ? " disabled" : ""}>
      <span class="dot">${picked ? CHECK_MINI : ""}</span><span>${escapeHtml(option)}</span>
    </button>`;
  }).join("");
  if (quiz.feedback) {
    const prefix = quiz.feedback.correct ? "Верно. " : "Пока неверно. ";
    feedback.hidden = false;
    feedback.className = "feedback " + (quiz.feedback.correct ? "ok" : "bad");
    feedback.textContent = prefix + (quiz.feedback.explanation || "") + (quiz.star ? " " + quiz.star : "");
    const hasNext = quiz.pending && quiz.pending.question && !quiz.finished;
    button.textContent = hasNext ? "Дальше" : "В кабинет";
    button.disabled = Boolean(quiz.busy);
    fill.style.width = "100%";
    return;
  }
  feedback.hidden = true;
  feedback.textContent = "";
  button.textContent = "Проверить ответ";
  button.disabled = quiz.picked == null || Boolean(quiz.busy);
}

function beginQuiz(kind, data, fallback, token) {
  state.quizKind = kind;
  const question = data.question || null;
  quiz = {
    token,
    kind,
    title: data.title || fallback || "Квиз",
    total: data.total || 0,
    number: (data.done || 0) + 1,
    done: data.done || 0,
    shown: question,
    picked: null,
    feedback: null,
    pending: question ? null : data,
    finished: Boolean(data.finished || !question),
    busy: false,
    misses: data.misses || [],
    elapsed: data.elapsed || "",
    star: data.star || "",
    skillIds: data.skill_ids || []
  };
  document.querySelector("#quiz-status").textContent = "";
  document.querySelector("#quiz-status").className = "status";
  paintQuiz();
  showScreen("quiz-play");
}

async function startQuiz(kind, extra) {
  const token = ++quizToken;
  const button = document.querySelector(kind === "topic" ? "#start-topic" : kind === "errors" ? "#start-errors" : "#ignite");
  const status = document.querySelector(kind === "topic" ? "#topic-status" : kind === "errors" ? "#error-status" : "#sky-status");
  let fallback = kind === "errors" ? "Квиз по ошибкам" : kind === "review" ? "Повторение" : "Квиз";
  let url = "/api/quizzes/errors";
  let body = "{}";
  if (kind === "topic") {
    const note = state.notes.find((item) => String(item.id) === String(state.selectedNoteId));
    if (!note) {
      status.className = "status error";
      status.textContent = "Выбери конспект.";
      return;
    }
    fallback = note.title || fallback;
    url = "/api/quizzes/note/" + encodeURIComponent(note.id);
  }
  if (kind === "review") {
    url = "/api/quizzes/review";
    body = JSON.stringify(extra && extra.skillId ? { skill_id: extra.skillId } : {});
  }
  if (button) button.disabled = true;
  if (status) {
    status.className = "status";
    status.textContent = "";
  }
  showScreen("quiz-loading");
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body });
    const data = await response.json().catch(() => ({}));
    if (token !== quizToken) return;
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) {
      showScreen(kind === "review" ? "sky" : kind === "errors" ? "quiz-errors" : "quiz-topic");
      if (status) {
        status.className = "status error";
        status.textContent = detailText(data, "Квиз не собрался.");
      }
      return;
    }
    beginQuiz(kind, data, fallback, token);
    if (kind === "topic") quiz.noteId = state.selectedNoteId;
  } finally {
    if (token === quizToken && button) {
      if (kind === "topic") button.disabled = state.selectedNoteId == null;
      else if (kind === "errors") button.disabled = !state.errors.length;
      else button.disabled = !(state.sky && state.sky.summary && state.sky.summary.dim);
    }
  }
}

function advanceQuiz() {
  const next = quiz.pending;
  if (!next || !next.question || quiz.finished) {
    if (quiz.finished) {
      paintQuiz();
      return;
    }
    showScreen(state.quizReturn || "home");
    return;
  }
  quiz.shown = next.question;
  quiz.number = (next.done || 0) + 1;
  quiz.total = next.total || quiz.total;
  quiz.title = next.title || quiz.title;
  quiz.feedback = null;
  quiz.pending = null;
  quiz.picked = null;
  quiz.finished = false;
  paintQuiz();
}

async function submitAnswer() {
  if (!quiz || quiz.busy || quiz.picked == null || !quiz.shown) return;
  const token = quiz.token;
  const status = document.querySelector("#quiz-status");
  quiz.busy = true;
  paintQuiz();
  try {
    const response = await fetch("/api/quizzes/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_id: Number(quiz.shown.id), option: quiz.picked })
    });
    const data = await response.json().catch(() => ({}));
    if (!quiz || quiz.token !== token) return;
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) {
      status.className = "status error";
      status.textContent = detailText(data, "Ответ не принят.");
      return;
    }
    status.textContent = "";
    status.className = "status";
    quiz.feedback = data.feedback;
    quiz.pending = data;
    quiz.finished = Boolean(data.finished || !data.question);
    quiz.done = data.done;
    quiz.total = data.total || quiz.total;
    quiz.elapsed = data.elapsed || quiz.elapsed;
    quiz.star = data.star || quiz.star;
    quiz.misses = data.misses || [];
    if (data.skill_id) quiz.skillIds = [data.skill_id].concat(quiz.skillIds || []);
    if (data.title) quiz.title = data.title;
    haptic(data.feedback && data.feedback.correct ? "ok" : "bad");
  } finally {
    if (quiz && quiz.token === token) {
      quiz.busy = false;
      paintQuiz();
    }
  }
}

function starWord(count) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "звезда";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "звезды";
  return "звёзд";
}

function paintSkyEntry() {
  const line = document.querySelector("#home-sky-line");
  const sky = state.sky;
  if (!sky || !sky.summary || !sky.summary.total) {
    line.textContent = "Появится после первой проверенной задачи";
    return;
  }
  const dim = sky.summary.dim || 0;
  const total = sky.summary.total;
  line.textContent = dim
    ? "На небе " + total + " " + starWord(total) + ", " + dim + " пора повторить"
    : "На небе " + total + " " + starWord(total);
}

function paintSky() {
  const summary = document.querySelector("#sky-summary");
  const empty = document.querySelector("#sky-empty");
  const canvas = document.querySelector("#sky-canvas");
  const ignite = document.querySelector("#ignite");
  const sky = state.sky;
  const igniteIds = JSON.parse(sessionStorage.getItem("lumi-ignite") || "[]").map(String);
  sessionStorage.removeItem("lumi-ignite");
  paintSkyEntry();
  if (!sky || !sky.summary || !sky.summary.total) {
    summary.textContent = "Каждый навык — звезда. Она зажжётся после первой задачи.";
    empty.hidden = false;
    canvas.innerHTML = "";
    ignite.hidden = true;
    return;
  }
  empty.hidden = true;
  const dim = sky.summary.dim || 0;
  summary.textContent = dim
    ? "Горит " + sky.summary.bright + ", пора повторить " + dim + ". Нажми на звезду, чтобы открыть навык."
    : "Все " + sky.summary.bright + " горят. Нажми на звезду, чтобы открыть навык.";
  ignite.hidden = dim === 0;
  ignite.textContent = "Зажечь тусклые звёзды" + (dim ? " (" + dim + ")" : "");
  canvas.innerHTML = (sky.constellations || []).map((group) => {
    const stars = group.stars || [];
    const nodes = stars.map((star) => {
      const klass = igniteIds.includes(String(star.id)) ? "ignite" : star.dim ? "" : "bright";
      return `<button type="button" class="star-node" data-star="${star.id}">
        <span class="star-glow ${klass}"></span>
        <span class="star-name">${escapeHtml(star.name || "Навык")}</span>
      </button>`;
    }).join("");
    const rowClass = stars.length < 2 ? "orbit-row solo" : "orbit-row";
    return `<section class="constellation"><h2>${escapeHtml(group.subject || "Предмет")}</h2><div class="${rowClass}">${nodes}</div></section>`;
  }).join("");
}

async function loadSky() {
  const response = await fetch("/api/sky");
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) return;
  state.sky = await response.json();
  paintSky();
}

async function openStar(id) {
  const response = await fetch("/api/skills/" + encodeURIComponent(id));
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) return;
  const star = await response.json();
  state.star = star;
  document.querySelector("#star-name").textContent = star.name || "Звезда";
  const percent = Math.round(Number(star.brightness || 0) * 100);
  document.querySelector("#star-meta").textContent = (star.subject || "Предмет") + " · владение " + percent + "%";
  const notes = document.querySelector("#star-notes");
  notes.innerHTML = (star.notes || []).slice(0, 1).map((note) => `<p>Конспект: ${escapeHtml(note.title)}</p>`).join("");
  const noteButton = document.querySelector("#star-note");
  noteButton.hidden = !(star.notes && star.notes.length);
  document.querySelector("#star-sheet").hidden = false;
}

async function loadCabinet() {
  const profileResponse = await fetch("/api/profile");
  if (profileResponse.status === 401) return showGate(LOCKED);
  if (!profileResponse.ok) return showGate("Кабинет не смог открыть базу.");
  state.profile = await profileResponse.json();
  const notesResponse = await fetch("/api/notes");
  if (notesResponse.status === 401) return showGate(LOCKED);
  if (!notesResponse.ok) return showGate("Кабинет не смог открыть базу.");
  const notesPayload = await notesResponse.json();
  state.notes = notesPayload.notes || [];
  state.selectedNoteId = null;
  paintHome();
  paintNotes();
  paintTopics();
  showCabinet();
  await loadSky();
  showScreen("home");
}

function setSwipeOpen(row, side) {
  const front = row.querySelector(".swipe-front");
  const remove = row.querySelector(".swipe-delete");
  const pin = row.querySelector(".swipe-pin");
  row.classList.toggle("open-left", side === "left");
  row.classList.toggle("open-right", side === "right");
  if (front) {
    const shift = side === "left" ? -SWIPE_REVEAL : side === "right" ? SWIPE_REVEAL : 0;
    front.style.transform = shift ? "translateX(" + shift + "px)" : "";
  }
  if (remove) {
    if (side === "left") remove.removeAttribute("aria-hidden");
    else remove.setAttribute("aria-hidden", "true");
  }
  if (pin) {
    if (side === "right") pin.removeAttribute("aria-hidden");
    else pin.setAttribute("aria-hidden", "true");
  }
}

function askDelete(id) {
  const key = String(id);
  const note = state.notes.find((item) => String(item.id) === key);
  pendingDelete = key;
  document.querySelector("#confirm-text").textContent = note ? "«" + note.title + "» пропадёт из кабинета." : "Конспект пропадёт из кабинета.";
  document.querySelector("#confirm-delete").hidden = false;
  closeSwipes();
}

function sortNotes() {
  state.notes.sort((left, right) => {
    const pinned = Number(Boolean(right.pinned)) - Number(Boolean(left.pinned));
    if (pinned) return pinned;
    return Number(right.id) - Number(left.id);
  });
}

async function deleteNote(id) {
  const key = String(id);
  if (deletingNotes.has(key)) return;
  deletingNotes.add(key);
  try {
    const response = await fetch("/api/notes/" + encodeURIComponent(key), { method: "DELETE" });
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok && response.status !== 404) return closeSwipes();
    state.notes = state.notes.filter((note) => String(note.id) !== key);
    if (String(state.selectedNoteId) === key) state.selectedNoteId = null;
    paintHome();
    paintNotes();
    paintTopics();
  } catch {
    closeSwipes();
  } finally {
    deletingNotes.delete(key);
  }
}

async function pinNote(id) {
  const key = String(id);
  const note = state.notes.find((item) => String(item.id) === key);
  if (!note || pinningNotes.has(key)) return;
  const pinned = !note.pinned;
  pinningNotes.add(key);
  try {
    const response = await fetch("/api/notes/" + encodeURIComponent(key), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned })
    });
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) return closeSwipes();
    note.pinned = pinned;
    sortNotes();
    paintHome();
    paintNotes();
    paintTopics();
  } catch {
    closeSwipes();
  } finally {
    pinningNotes.delete(key);
  }
}

function bindSwipe(root) {
  root.addEventListener("pointerdown", (event) => {
    if (event.button > 0) return;
    const front = event.target.closest(".swipe-front");
    if (!front || !root.contains(front)) return;
    const row = front.closest(".swipe");
    const open = row.classList.contains("open-left") ? -SWIPE_REVEAL : row.classList.contains("open-right") ? SWIPE_REVEAL : 0;
    swipeDrag = { row, front, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, base: open, dx: 0, axis: "" };
  });
  root.addEventListener("pointermove", (event) => {
    if (!swipeDrag || event.pointerId !== swipeDrag.pointerId) return;
    const dx = event.clientX - swipeDrag.startX;
    const dy = event.clientY - swipeDrag.startY;
    if (!swipeDrag.axis) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      swipeDrag.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      if (swipeDrag.axis === "y") {
        swipeDrag = null;
        return;
      }
      swipeDrag.row.classList.add("dragging");
      if (swipeDrag.front.setPointerCapture) {
        try { swipeDrag.front.setPointerCapture(event.pointerId); } catch (error) { /* pointer already gone */ }
      }
    }
    const next = Math.min(220, Math.max(-220, swipeDrag.base + dx));
    swipeDrag.dx = next;
    swipeDrag.front.style.transform = "translateX(" + next + "px)";
  });
  const finishSwipe = (event) => {
    if (!swipeDrag || event.pointerId !== swipeDrag.pointerId) return;
    const drag = swipeDrag;
    swipeDrag = null;
    drag.row.classList.remove("dragging");
    if (drag.axis !== "x") return;
    drag.row.dataset.suppress = "1";
    const id = drag.row.dataset.swipe;
    if (drag.dx <= -170) return askDelete(id);
    if (drag.dx >= 170) return pinNote(id).catch(() => showGate("Кабинет не смог открыть базу."));
    if (drag.dx <= -SWIPE_REVEAL / 2) return setSwipeOpen(drag.row, "left");
    if (drag.dx >= SWIPE_REVEAL / 2) return setSwipeOpen(drag.row, "right");
    setSwipeOpen(drag.row, "");
  };
  root.addEventListener("pointerup", finishSwipe);
  root.addEventListener("pointercancel", finishSwipe);
}

function openNoteFromList(event, back) {
  const remove = event.target.closest("[data-delete]");
  if (remove) {
    event.preventDefault();
    askDelete(remove.dataset.delete);
    return;
  }
  const pin = event.target.closest("[data-pin]");
  if (pin) {
    event.preventDefault();
    pinNote(pin.dataset.pin).catch(() => showGate("Кабинет не смог открыть базу."));
    return;
  }
  const row = event.target.closest(".swipe");
  if (row && row.dataset.suppress === "1") {
    row.dataset.suppress = "";
    return;
  }
  if (row && (row.classList.contains("open-left") || row.classList.contains("open-right"))) {
    setSwipeOpen(row, "");
    return;
  }
  const card = event.target.closest("[data-note]");
  if (!card) return;
  openNote(card.dataset.note, back).catch(() => showGate("Кабинет не смог открыть базу."));
}

document.querySelectorAll("[data-back]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.back;
    if (target === "return") {
      if (!document.querySelector("#note-form").hidden) {
        cancelNoteEdit();
        return;
      }
      showScreen(state.noteReturn || "notes");
    }
    else if (target === "quiz-origin") showScreen(state.quizKind === "errors" ? "quiz-errors" : state.quizKind === "review" ? "sky" : "quiz-topic");
    else if (target === "quiz-return") showScreen(state.quizReturn || "home");
    else showScreen(target);
  });
});

tabs.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    const name = button.dataset.tab;
    if (name === "notes") openNotes();
    else if (name === "quiz") showScreen("quiz");
    else if (name === "sky") {
      showScreen("sky");
      loadSky().catch(() => showGate("Кабинет не смог открыть базу."));
    } else showScreen("home");
  });
});

document.querySelector("#open-profile").addEventListener("click", openProfile);
document.querySelector("#open-sky").addEventListener("click", () => {
  loadSky().then(() => showScreen("sky")).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#open-topic").addEventListener("click", () => openTopicQuiz("home"));
document.querySelector("#open-errors").addEventListener("click", () => openErrors("home").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#menu-topic").addEventListener("click", () => openTopicQuiz("quiz"));
document.querySelector("#menu-errors").addEventListener("click", () => openErrors("quiz").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#ask-chat").addEventListener("click", () => {
  const app = bridge();
  if (app && app.close) app.close();
});

bindSwipe(document.querySelector("#home-notes"));
bindSwipe(document.querySelector("#notes-list"));
document.querySelector("#show-all").addEventListener("click", openNotes);
document.querySelector("#home-notes").addEventListener("click", (event) => openNoteFromList(event, "home"));
document.querySelector("#notes-list").addEventListener("click", (event) => openNoteFromList(event, "notes"));
document.querySelector("#search-hits").addEventListener("click", (event) => {
  const card = event.target.closest("[data-note]");
  if (!card) return;
  openNote(card.dataset.note, "notes").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#search-input").addEventListener("input", () => {
  const ticket = ++searchTicket;
  const query = document.querySelector("#search-input").value.trim();
  const box = document.querySelector("#search-hits");
  if (!query) {
    box.innerHTML = "";
    return;
  }
  fetch("/api/search?q=" + encodeURIComponent(query)).then(async (response) => {
    if (ticket !== searchTicket) return;
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) return;
    const hits = await response.json();
    box.innerHTML = (hits || []).map((hit) => `
      <button type="button" class="card plain" data-note="${escapeHtml(hit.id)}">
        <span class="icon">${BOOK}</span>
        <span class="copy">
          <h3>${escapeHtml(hit.title || "Конспект")}</h3>
          <span class="preview hit">${highlight(hit.snippet || "", query)}</span>
        </span>
      </button>
    `).join("") || `<p class="empty">Ничего не нашлось.</p>`;
  }).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#topic-list").addEventListener("click", (event) => {
  const card = event.target.closest("[data-select]");
  if (!card) return;
  state.selectedNoteId = card.dataset.select;
  paintTopics();
});
document.querySelector("#start-topic").addEventListener("click", () => startQuiz("topic").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#start-errors").addEventListener("click", () => startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#ignite").addEventListener("click", () => startQuiz("review").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#quiz-options").addEventListener("click", (event) => {
  const button = event.target.closest("[data-option]");
  if (!button || !quiz || quiz.feedback || quiz.busy) return;
  quiz.picked = Number(button.dataset.option);
  document.querySelector("#quiz-status").textContent = "";
  haptic("tap");
  paintQuiz();
});
document.querySelector("#quiz-go").addEventListener("click", () => {
  if (!quiz || quiz.busy) return;
  if (quiz.feedback || !quiz.shown) {
    advanceQuiz();
    return;
  }
  submitAnswer().catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#quiz-again").addEventListener("click", () => {
  if (!quiz) return;
  if (quiz.kind === "topic") startQuiz("topic").catch(() => showGate("Кабинет не смог открыть базу."));
  else if (quiz.kind === "errors") startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу."));
  else startQuiz("review").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#quiz-retry").addEventListener("click", () => startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#quiz-sky").addEventListener("click", () => {
  const ids = quiz && quiz.skillIds ? quiz.skillIds : [];
  if (ids.length) sessionStorage.setItem("lumi-ignite", JSON.stringify(ids));
  loadSky().then(() => showScreen("sky")).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#sky-canvas").addEventListener("click", (event) => {
  const star = event.target.closest("[data-star]");
  if (!star) return;
  openStar(star.dataset.star).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#star-close").addEventListener("click", closeStar);
document.querySelector("#star-sheet").addEventListener("click", (event) => {
  if (event.target.id === "star-sheet") closeStar();
});
document.querySelector("#star-train").addEventListener("click", () => {
  const skillId = state.star ? state.star.id : null;
  closeStar();
  startQuiz("review", { skillId }).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#star-note").addEventListener("click", () => {
  const note = state.star && state.star.notes && state.star.notes[0];
  closeStar();
  if (note) openNote(note.id, "sky").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#share-sky").addEventListener("click", async () => {
  const summary = document.querySelector("#sky-summary").textContent || "Моё небо в Люми";
  const app = bridge();
  if (navigator.share) {
    try { await navigator.share({ title: "Небо Люми", text: summary }); } catch (error) { /* user closed share */ }
    return;
  }
  if (app && app.shareUrl) app.shareUrl(summary);
});
nameInput.addEventListener("input", () => {
  if (saveButton.textContent !== "Сохранить") saveButton.textContent = "Сохранить";
  previewFromForm();
});
gradeToggle.addEventListener("click", () => {
  const open = gradeMenu.hidden;
  gradeMenu.hidden = !open;
  gradeToggle.setAttribute("aria-expanded", open ? "true" : "false");
});
gradeMenu.addEventListener("click", (event) => {
  const button = event.target.closest("[data-grade]");
  if (!button) return;
  setGrade(button.dataset.grade, true);
  closeGradeMenu();
});
document.querySelectorAll("[data-style]").forEach((button) => {
  button.addEventListener("click", () => {
    paintStyle(button.dataset.style);
    state.profile.explain_style = button.dataset.style;
    if (saveButton.textContent !== "Сохранить") saveButton.textContent = "Сохранить";
  });
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".grade-picker")) closeGradeMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeGradeMenu();
    closeConfirm();
    closeStar();
  }
});
document.querySelector("#confirm-cancel").addEventListener("click", closeConfirm);
document.querySelector("#confirm-ok").addEventListener("click", () => {
  const id = pendingDelete;
  closeConfirm();
  if (id) deleteNote(id).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#confirm-delete").addEventListener("click", (event) => {
  if (event.target.id === "confirm-delete") closeConfirm();
});
function beginNoteEdit() {
  if (!currentNote) return;
  document.querySelector("#note-body-input").value = currentNote.body || "";
  document.querySelector("#note-status").textContent = "";
  document.querySelector("#note-status").className = "status";
  document.querySelector("#note-body").hidden = true;
  document.querySelector("#edit-note").hidden = true;
  document.querySelector("#note-form").hidden = false;
  document.querySelector("#note-body-input").focus();
}

function cancelNoteEdit() {
  document.querySelector("#note-form").hidden = true;
  document.querySelector("#note-body").hidden = false;
  document.querySelector("#edit-note").hidden = false;
  document.querySelector("#note-status").textContent = "";
}

document.querySelector("#edit-note").addEventListener("click", beginNoteEdit);
document.querySelector("#cancel-note").addEventListener("click", cancelNoteEdit);
document.querySelector("#note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentNote) return;
  const status = document.querySelector("#note-status");
  const payload = {
    title: currentNote.title || "",
    subject: currentNote.subject || "",
    body: document.querySelector("#note-body-input").value.trim()
  };
  if (!payload.body) {
    status.className = "status error";
    status.textContent = "Напиши текст конспекта.";
    return;
  }
  const response = await fetch("/api/notes/" + encodeURIComponent(currentNote.id), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) {
    status.className = "status error";
    status.textContent = detailText(data, "Не удалось сохранить.");
    return;
  }
  currentNote = data;
  const stored = state.notes.find((item) => String(item.id) === String(data.id));
  if (stored) {
    stored.title = data.title;
    stored.subject = data.subject;
    stored.preview = data.body || "";
  }
  paintHome();
  paintNotes();
  paintTopics();
  document.querySelector("#note-title").textContent = data.title || "Конспект";
  document.querySelector("#note-meta").textContent = metaLine(data.subject);
  document.querySelector("#note-body").innerHTML = renderNoteBody(data.body || "");
  document.querySelector("#note-form").hidden = true;
  document.querySelector("#note-body").hidden = false;
  document.querySelector("#edit-note").hidden = false;
});
document.querySelector("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = nameInput.value.trim();
  const grade = Number(gradeInput.value);
  const style = document.querySelector("[data-style].active");
  if (!name) {
    saveStatus.className = "status error";
    saveStatus.textContent = "Напиши имя.";
    return;
  }
  if (!gradeInput.value || grade < 5 || grade > 11) {
    saveStatus.className = "status error";
    saveStatus.textContent = "Выбери класс от 5 до 11.";
    return;
  }
  saveButton.disabled = true;
  saveStatus.textContent = "";
  saveStatus.className = "status";
  try {
    const payload = { first_name: name, grade, reminders: remindersInput.checked };
    if (style) payload.explain_style = style.dataset.style;
    const response = await fetch("/api/profile", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) {
      saveStatus.className = "status error";
      saveStatus.textContent = detailText(data, "Не удалось сохранить.");
      return;
    }
    state.profile = data;
    nameInput.value = data.first_name || "";
    setGrade(data.grade, false);
    paintStyle(data.explain_style || "");
    remindersInput.checked = data.reminders !== false;
    paintIdentity();
    paintHome();
    paintNotes();
    paintTopics();
    previewFromForm();
    saveButton.textContent = "Сохранено";
  } finally {
    saveButton.disabled = false;
  }
});

const app = bridge();
if (app && app.BackButton && app.BackButton.onClick) app.BackButton.onClick(goBack);

async function enter() {
  const webApp = bridge();
  const initData = webApp && typeof webApp.initData === "string" ? webApp.initData : "";
  if (webApp && typeof webApp.ready === "function") webApp.ready();
  if (initData) {
    const response = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: initData })
    });
    if (response.status === 401) {
      showGate("Не удалось подтвердить вход. Закройте кабинет и откройте его кнопкой в чате.");
      return;
    }
    if (!response.ok) return showGate("Кабинет не смог открыть базу.");
  }
  await loadCabinet();
}

enter().catch(() => showGate("Кабинет не смог открыть базу."));
