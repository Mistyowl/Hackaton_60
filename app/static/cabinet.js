const LOCKED = "Откройте кабинет кнопкой в чате MAX.";
const EMPTY_NOTES = "Конспекты появятся, когда в чате нажмёшь «В конспект».";
const EMPTY_ERRORS = "Банк ошибок пуст. Пришли решение с ошибкой — по этому пробелу я соберу отдельный квиз.";
const BOOK = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';
const CHEV = '<span class="chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg></span>';
const PIN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 4h6l-1 7 3 3v2H7v-2l3-3-1-7z"/><path d="M12 16v5"/></svg>';
const MARK = '<span class="mark"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg></span>';
const CHECK_MINI = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg>';
const CROSS_MINI = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 6l12 12"/><path d="M18 6L6 18"/></svg>';
const SWIPE_REVEAL = 118;

const gate = document.querySelector("#gate");
const cabinet = document.querySelector("#cabinet");
const tabs = document.querySelector("#tabs");
const nameInput = document.querySelector("#name-input");
const gradeInput = document.querySelector("#grade-input");
const goalInput = document.querySelector("#goal-input");
const saveButton = document.querySelector("#save-profile");
const saveStatus = document.querySelector("#save-status");
const remindersInput = document.querySelector("#reminders-input");
const styleSlider = document.querySelector("#style-slider");

const state = {
  profile: {
    first_name: "", grade: null, explain_style: null, exam_goal: null, reminders: true,
    quizzes_done: 0, closed_errors: 0, accuracy: 0, streak_days: 0
  },
  home: { open_errors: 0, streak_days: 0, week_done: 0, week_goal: 5 },
  notes: [],
  errors: [],
  errorCounts: { new: 0, active: 0, closed: 0 },
  notesFilter: "all",
  errorsFilter: "all",
  sky: null,
  selectedNoteId: null,
  quizPick: { subject: "algebra", noteId: null, count: 5 },
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
  if (screen === "profile") return "profile";
  if (screen === "quiz-errors" || ((screen === "quiz-play" || screen === "quiz-loading") && state.quizKind === "errors")) return "errors";
  if (screen === "quiz" || screen.startsWith("quiz-")) return "quiz";
  return "home";
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
  document.body.classList.toggle("home-open", name === "home");
  document.body.classList.toggle("quiz-open", name === "quiz");
  const roots = ["home", "notes", "quiz", "quiz-errors", "profile"];
  const app = bridge();
  if (app && app.BackButton) {
    if (roots.includes(name)) {
      if (app.BackButton.hide) app.BackButton.hide();
    } else if (app.BackButton.show) {
      app.BackButton.show();
    }
  }
  confirmClose(name === "quiz-play" || name === "quiz-loading");
  if (name === "home" || name === "quiz") refreshHome().catch(() => {});
  if (name === "quiz") paintQuizSetup();
}

function goBack() {
  const screen = state.screen;
  if (screen === "note" && !document.querySelector("#note-form").hidden) {
    cancelNoteEdit();
    return;
  }
  if (screen === "note") showScreen(state.noteReturn || "notes");
  else if (screen === "quiz-play" || screen === "quiz-loading") showScreen(state.quizKind === "errors" ? "quiz-errors" : state.quizKind === "review" ? "sky" : "quiz");
  else if (screen === "quiz-topic" || screen === "quiz-errors") showScreen(state.quizReturn || "home");
  else if (screen === "profile") showScreen("home");
  else showScreen("home");
}

function ruCount(count, one, few, many) {
  const abs = Math.abs(count) % 100;
  const tail = abs % 10;
  if (abs > 10 && abs < 15) return many;
  if (tail === 1) return one;
  if (tail >= 2 && tail <= 4) return few;
  return many;
}

function shortDate(value) {
  const months = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."];
  const moment = new Date(value || "");
  if (Number.isNaN(moment.getTime())) return "";
  return moment.getDate() + " " + months[moment.getMonth()];
}

function defaultGoal(grade) {
  const num = Number(grade);
  if (num === 9) return "oge";
  if (num === 10 || num === 11) return "ege";
  if (num >= 5 && num <= 11) return "self";
  return "";
}

function goalLabel(goal, grade) {
  const value = goal || defaultGoal(grade);
  if (value === "oge") return "ОГЭ";
  if (value === "ege") return "ЕГЭ";
  if (value === "self") return "Для себя";
  return "";
}

function paintIdentity() {
  document.querySelector("#home-name").textContent = profileName();
  const chip = document.querySelector("#home-grade-text");
  const exam = document.querySelector("#home-exam");
  const grade = state.profile.grade == null ? null : Number(state.profile.grade);
  if (grade == null || Number.isNaN(grade)) {
    chip.hidden = true;
    exam.hidden = true;
    return;
  }
  chip.hidden = false;
  chip.textContent = grade + " класс";
  const label = goalLabel(state.profile.exam_goal, grade);
  if (label && label !== "Для себя") {
    exam.hidden = false;
    exam.textContent = label;
  } else exam.hidden = true;
}

function subjectMark(subject) {
  const text = String(subject || "").toLowerCase();
  if (text.includes("геометр")) return "Δ";
  if (text.includes("физик")) return "⚛";
  if (text.includes("русск")) return "Аа";
  if (text.includes("хими")) return "Cl";
  if (text.includes("англий")) return "En";
  return "Σ";
}

function fromChat(note) {
  const source = note && note.source;
  return source === "chat" || source === "photo";
}

function paintProgress() {
  const step = document.querySelector("#next-step");
  const count = Number(state.home.open_errors || 0);
  step.hidden = count <= 0;
  if (count > 0) {
    const word = ruCount(count, "незакреплённая ошибка", "незакреплённые ошибки", "незакреплённых ошибок");
    document.querySelector("#next-step-text").textContent = "У тебя " + count + " " + word + ". Пройдём квиз?";
  }
  const streak = Number(state.home.streak_days || 0);
  document.querySelector("#streak-count").textContent = String(streak);
  document.querySelector("#streak-unit").textContent = ruCount(streak, "день", "дня", "дней");
  document.querySelector("#streak-dots").innerHTML = Array.from({ length: 7 }, (_, index) => (
    `<i class="${index < Math.min(streak, 7) ? "on" : ""}"></i>`
  )).join("");
  const goal = Number(state.home.week_goal || 5) || 5;
  const done = Number(state.home.week_done || 0);
  document.querySelector("#week-done").textContent = String(done);
  document.querySelector("#week-goal").textContent = String(goal);
  document.querySelector("#week-fill").style.width = Math.max(0, Math.min(100, (done / goal) * 100)) + "%";
}

function emptyBlock(text) {
  return `<div class="empty-card"><img class="mascot" src="/assets/lumi-mark.png" alt=""><p>${escapeHtml(text)}</p></div>`;
}

function noteCard(note, mode) {
  const select = mode === "select";
  const selected = select && String(note.id) === String(state.selectedNoteId);
  const preview = mode === "read" && note.preview
    ? `<span class="preview">${escapeHtml(plain(note.preview))}</span>`
    : "";
  const chat = fromChat(note) ? `<span class="tag-chat">Из чата</span>` : "";
  const when = shortDate(note.created_at);
  const meta = when ? (note.subject || "Без предмета") + " · " + when : (note.subject || "Без предмета");
  return `<button type="button" class="card${selected ? " selected" : ""}" ${select ? "data-select" : "data-note"}="${escapeHtml(note.id)}"${select ? ` aria-pressed="${selected ? "true" : "false"}"` : ""}>
    <span class="note-mark" aria-hidden="true">${escapeHtml(subjectMark(note.subject))}</span>
    <span class="copy">
      <h3>${note.pinned ? `<span class="pin" title="Закреплено">${PIN}</span>` : ""}${escapeHtml(note.title || "Конспект")}</h3>
      <span class="meta">${escapeHtml(select ? metaLine(note.subject) : meta)}</span>
      ${chat}
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

function homeNoteCard(note) {
  const subject = note.subject || "Без предмета";
  const when = shortDate(note.created_at);
  const meta = when ? subject + " · " + when : subject;
  return `<button type="button" class="home-note" data-note="${escapeHtml(note.id)}">
    <span class="home-note-icon" aria-hidden="true">${escapeHtml(subjectMark(note.subject))}</span>
    <span class="copy">
      <h3>${escapeHtml(note.title || "Конспект")}</h3>
      <span class="meta">${escapeHtml(meta)}</span>
    </span>
  </button>`;
}

function paintHome() {
  paintIdentity();
  paintProgress();
  const box = document.querySelector("#home-notes");
  const all = document.querySelector("#show-all");
  if (!state.notes.length) {
    all.hidden = true;
    box.innerHTML = `<p class="home-empty">Сфоткай задачу в чате, и здесь появится первый конспект.</p>`;
    return;
  }
  all.hidden = false;
  box.innerHTML = state.notes.slice(0, 3).map((note) => homeNoteCard(note)).join("");
}

let homeTicket = 0;
async function refreshHome() {
  const ticket = ++homeTicket;
  const response = await fetch("/api/home");
  if (ticket !== homeTicket) return;
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) return;
  state.home = await response.json();
  if (state.screen === "home") paintHome();
  paintQuizSetup();
}

function filteredNotes() {
  const filter = state.notesFilter || "all";
  if (filter === "all") return state.notes.slice();
  if (filter === "chat") return state.notes.filter((note) => fromChat(note));
  const subject = subjectById(filter);
  return notesForSubject(subject);
}

function paintNotesFilters() {
  const box = document.querySelector("#notes-filters");
  if (!box) return;
  const chips = [
    { id: "all", label: "Все" },
    { id: "chat", label: "Из чата" },
    ...SUBJECTS.map((item) => ({ id: item.id, label: item.label }))
  ];
  box.innerHTML = chips.map((chip) => `
    <button type="button" class="filter-chip${state.notesFilter === chip.id ? " on" : ""}" data-notes-filter="${escapeHtml(chip.id)}">${escapeHtml(chip.label)}</button>
  `).join("");
}

function paintNotes() {
  paintNotesFilters();
  const box = document.querySelector("#notes-list");
  const rows = filteredNotes();
  if (!state.notes.length) {
    box.innerHTML = emptyBlock(EMPTY_NOTES);
    return;
  }
  if (!rows.length) {
    box.innerHTML = emptyBlock("В этом фильтре пока пусто.");
    return;
  }
  const groups = new Map();
  rows.forEach((note) => {
    const key = note.subject || "Без предмета";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(note);
  });
  box.innerHTML = Array.from(groups.entries()).map(([title, items]) => `
    <section class="note-group">
      <h2 class="note-group-title">${escapeHtml(title)} · ${items.length}</h2>
      ${items.map((note) => swipeCard(note, "read")).join("")}
    </section>
  `).join("");
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

const SUBJECTS = [
  { id: "algebra", label: "Алгебра", match: "алгебр" },
  { id: "geometry", label: "Геометрия", match: "геометр" },
  { id: "physics", label: "Физика", match: "физик" },
  { id: "russian", label: "Русский", match: "русск" },
  { id: "chemistry", label: "Химия", match: "хими" },
  { id: "english", label: "Английский", match: "англий" }
];

function subjectById(id) {
  return SUBJECTS.find((item) => item.id === id) || SUBJECTS[0];
}

function notesForSubject(subject) {
  const needle = subject.match;
  return state.notes.filter((note) => (note.subject || "").toLowerCase().includes(needle));
}

function paintQuizSetup() {
  const line = document.querySelector("#quiz-error-line");
  if (!line) return;
  const count = Number(state.home.open_errors || 0);
  if (count > 0) {
    const word = ruCount(count, "незакреплённая", "незакреплённые", "незакреплённых");
    line.textContent = count + " " + word + " · новые формулировки";
  } else line.textContent = "Пока нет незакреплённых ошибок";
  const subject = subjectById(state.quizPick.subject);
  document.querySelectorAll("#subject-grid [data-subject]").forEach((button) => {
    const on = button.dataset.subject === subject.id;
    button.classList.toggle("on", on);
    if (on) button.setAttribute("aria-pressed", "true");
    else button.setAttribute("aria-pressed", "false");
  });
  const topics = notesForSubject(subject);
  if (state.quizPick.noteId != null && !topics.some((note) => String(note.id) === String(state.quizPick.noteId))) {
    state.quizPick.noteId = null;
  }
  const chips = [`<button type="button" class="pick${state.quizPick.noteId == null ? " on" : ""}" data-topic="">Все темы</button>`];
  topics.slice(0, 8).forEach((note) => {
    const on = String(note.id) === String(state.quizPick.noteId);
    chips.push(`<button type="button" class="pick${on ? " on" : ""}" data-topic="${escapeHtml(note.id)}">${escapeHtml(note.title || "Конспект")}</button>`);
  });
  document.querySelector("#topic-chips").innerHTML = chips.join("");
  document.querySelectorAll("#count-chips [data-count]").forEach((button) => {
    const on = Number(button.dataset.count) === Number(state.quizPick.count);
    button.classList.toggle("on", on);
    if (on) button.setAttribute("aria-pressed", "true");
    else button.setAttribute("aria-pressed", "false");
  });
}

function setGrade(value, notify) {
  const grade = value == null || value === "" ? "" : String(value);
  gradeInput.value = grade;
  document.querySelectorAll("#grade-row [data-grade]").forEach((button) => {
    button.classList.toggle("on", button.dataset.grade === grade);
  });
  if (notify) {
    if (saveButton.textContent !== "Сохранить") saveButton.textContent = "Сохранить";
    if (!goalInput.value) setGoal(defaultGoal(grade), false);
    previewFromForm();
  }
}

function setGoal(value, notify) {
  const goal = value == null || value === "" ? "" : String(value);
  goalInput.value = goal;
  document.querySelectorAll("#goal-row [data-goal]").forEach((button) => {
    button.classList.toggle("on", button.dataset.goal === goal);
  });
  if (notify) {
    if (saveButton.textContent !== "Сохранить") saveButton.textContent = "Сохранить";
    previewFromForm();
  }
}

function previewFromForm() {
  const name = nameInput.value.trim() || "Ученик";
  const grade = gradeInput.value;
  const goal = goalLabel(goalInput.value, Number(grade));
  document.querySelector("#preview-name").textContent = name;
  if (grade && goal) document.querySelector("#preview-meta").textContent = grade + " класс · " + goal;
  else if (grade) document.querySelector("#preview-meta").textContent = grade + " класс";
  else document.querySelector("#preview-meta").textContent = "Ученик";
}

function paintStyle(style) {
  const value = style === "detail" ? "1" : "0";
  if (styleSlider) styleSlider.value = value;
  const text = document.querySelector("#style-preview-text");
  if (text) {
    text.textContent = style === "detail"
      ? "Разберу шаг за шагом и покажу, на что смотреть."
      : "Объясню понятно, без лишнего.";
  }
}

function paintProfileStats() {
  document.querySelector("#stat-quizzes").textContent = String(state.profile.quizzes_done || 0);
  document.querySelector("#stat-closed").textContent = String(state.profile.closed_errors || 0);
  document.querySelector("#stat-accuracy").textContent = String(state.profile.accuracy || 0) + "%";
  const box = document.querySelector("#achievements");
  if (!box) return;
  const quizzes = Number(state.profile.quizzes_done || 0);
  const closed = Number(state.profile.closed_errors || 0);
  const streak = Number(state.profile.streak_days || 0);
  const items = [
    { on: quizzes >= 1, icon: "⭐", title: "Первый квиз", hint: "Пройди любой квиз" },
    { on: streak >= 7, icon: "🔥", title: "Неделя без пропусков", hint: "7 дней подряд" },
    { on: quizzes >= 5, icon: "⚡", title: "5 квизов", hint: "Закрой 5 квизов" },
    { on: closed >= 1, icon: "🎯", title: "Ошибка закрыта", hint: "Закрепи первую ошибку" }
  ];
  box.innerHTML = items.map((item) => `
    <article class="achievement${item.on ? "" : " off"}">
      <div class="achievement-icon" aria-hidden="true">${item.icon}</div>
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.hint)}</span>
    </article>
  `).join("");
}

function fillProfileForm() {
  nameInput.value = state.profile.first_name || "";
  setGrade(state.profile.grade, false);
  setGoal(state.profile.exam_goal || defaultGoal(state.profile.grade), false);
  paintStyle(state.profile.explain_style || "short");
  remindersInput.checked = state.profile.reminders !== false;
  saveButton.textContent = "Сохранить";
  saveStatus.textContent = "";
  saveStatus.className = "status";
  paintProfileStats();
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

function errorStatusLabel(status) {
  if (status === "closed") return "Закреплена";
  if (status === "active") return "В работе";
  return "Новая";
}

function paintErrorFilters() {
  const box = document.querySelector("#error-filters");
  if (!box) return;
  const subjects = Array.from(new Set(state.errors.map((row) => row.subject).filter(Boolean)));
  const chips = [{ id: "all", label: "Все" }, ...subjects.map((name) => ({ id: name, label: name }))];
  box.innerHTML = chips.map((chip) => `
    <button type="button" class="filter-chip${state.errorsFilter === chip.id ? " on" : ""}" data-errors-filter="${escapeHtml(chip.id)}">${escapeHtml(chip.label)}</button>
  `).join("");
}

function paintErrorBank() {
  const box = document.querySelector("#error-list");
  const button = document.querySelector("#start-errors");
  const counts = state.errorCounts || { new: 0, active: 0, closed: 0 };
  document.querySelector("#bank-new").textContent = String(counts.new || 0);
  document.querySelector("#bank-active").textContent = String(counts.active || 0);
  document.querySelector("#bank-closed").textContent = String(counts.closed || 0);
  paintErrorFilters();
  const open = state.errors.filter((row) => row.status !== "closed");
  button.disabled = !open.length;
  let rows = state.errors;
  if (state.errorsFilter && state.errorsFilter !== "all") {
    rows = rows.filter((row) => row.subject === state.errorsFilter);
  }
  if (!state.errors.length) {
    box.innerHTML = emptyBlock(EMPTY_ERRORS);
    return;
  }
  if (!rows.length) {
    box.innerHTML = emptyBlock("В этом фильтре пока пусто.");
    return;
  }
  box.innerHTML = rows.map((row) => {
    const progress = Math.max(0, Math.min(3, Number(row.progress || 0)));
    const topic = row.skill || row.mistake || "";
    const meta = [row.subject || "Предмет", topic].filter(Boolean).join(" · ");
    const bars = [0, 1, 2].map((index) => `<i class="${index < progress ? "on" : ""}"></i>`).join("");
    return `<article class="error-card">
      <div class="error-card-top">
        <span class="error-card-meta">${escapeHtml(meta)}</span>
        <span class="error-status ${escapeHtml(row.status || "new")}">${escapeHtml(errorStatusLabel(row.status))}</span>
      </div>
      <p class="error-card-q">${escapeHtml(row.problem || row.mistake || "Ошибка")}</p>
      <div class="error-progress">
        <div class="error-bars" aria-hidden="true">${bars}</div>
        <span>${progress} из 3 верных</span>
      </div>
    </article>`;
  }).join("");
}

async function openErrors(from) {
  const ticket = ++errorsTicket;
  state.quizReturn = from === "quiz" ? "quiz" : from === "errors" ? "quiz-errors" : "home";
  showScreen("quiz-errors");
  const status = document.querySelector("#error-status");
  const button = document.querySelector("#start-errors");
  button.disabled = true;
  status.className = "status";
  status.textContent = "";
  const response = await fetch("/api/errors");
  if (ticket !== errorsTicket) return;
  if (response.status === 401) return showGate(LOCKED);
  if (!response.ok) {
    document.querySelector("#error-list").innerHTML = "";
    status.className = "status error";
    status.textContent = "Не удалось открыть банк ошибок.";
    return;
  }
  const data = await response.json();
  state.errors = Array.isArray(data.items) ? data.items : [];
  state.errorCounts = data.counts || { new: 0, active: 0, closed: 0 };
  if (state.errorsFilter !== "all" && !state.errors.some((row) => row.subject === state.errorsFilter)) {
    state.errorsFilter = "all";
  }
  paintErrorBank();
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

function topicList(items) {
  if (!items || !items.length) return "—";
  return items.map((item) => escapeHtml(item)).join(", ");
}

function paintQuizResults() {
  const summary = quiz.summary || {};
  const correct = Number(summary.correct || 0);
  const total = Number(summary.total || quiz.total || 0);
  const review = summary.review || [];
  const perfect = !review.length && correct >= total && total > 0;
  document.querySelector("#result-score").textContent = correct + "/" + total;
  document.querySelector("#result-title").textContent = perfect
    ? "Супер! Все ответы с первой попытки."
    : "Не страшно — ошибки уже в банке, разберём их вместе.";
  document.querySelector("#result-strong").innerHTML = topicList(summary.strong);
  document.querySelector("#result-review").innerHTML = topicList(review);
  document.querySelector("#result-advice").textContent = summary.advice
    || (perfect
      ? "Отлично! Можно взять новую тему."
      : "Рекомендую пройти квиз по ошибкам через день.");
  document.querySelector("#quiz-retry").hidden = !review.length;
}

function paintQuiz() {
  const live = document.querySelector("#quiz-live");
  const results = document.querySelector("#quiz-results");
  const button = document.querySelector("#quiz-go");
  document.querySelector("#quiz-kicker").textContent = quiz && quiz.kind === "errors" ? "Тренировка" : quiz && quiz.kind === "review" ? "Повторение" : "Квиз по конспектам";
  document.querySelector("#quiz-title").textContent = quiz && quiz.title ? quiz.title : "Квиз";
  if (quiz && quiz.showResults) {
    live.hidden = true;
    results.hidden = false;
    button.hidden = true;
    paintQuizResults();
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
  progress.textContent = number + "/" + total;
  fill.style.width = Math.min(100, Math.round((number / total) * 100)) + "%";
  heading.hidden = false;
  card.innerHTML = promptHtml(quiz.shown.prompt);
  const correctIndex = quiz.feedback && Number.isInteger(quiz.feedback.correct_index)
    ? quiz.feedback.correct_index
    : null;
  options.innerHTML = (quiz.shown.options || []).map((option, index) => {
    const picked = quiz.picked === index;
    let klass = "option";
    let mark = "";
    if (quiz.feedback && correctIndex != null) {
      if (index === correctIndex) {
        klass += " ok";
        mark = CHECK_MINI;
      } else if (picked) {
        klass += " bad";
        mark = CROSS_MINI;
      }
    } else if (picked) {
      klass += " picked";
      mark = CHECK_MINI;
    }
    return `<button type="button" class="${klass}" data-option="${index}" aria-pressed="${picked ? "true" : "false"}"${quiz.feedback || quiz.busy ? " disabled" : ""}>
      <span class="dot">${mark}</span><span>${escapeHtml(option)}</span>
    </button>`;
  }).join("");
  if (quiz.feedback) {
    feedback.hidden = false;
    feedback.className = "coach " + (quiz.feedback.correct ? "ok" : "bad");
    document.querySelector("#coach-title").textContent = quiz.feedback.correct
      ? "Верно! 🎉"
      : "Почти! Давай разберём";
    document.querySelector("#coach-text").textContent = quiz.feedback.explanation || "";
    button.textContent = quiz.finished ? "Результаты →" : "Дальше →";
    button.disabled = Boolean(quiz.busy);
    fill.style.width = "100%";
    return;
  }
  feedback.hidden = true;
  document.querySelector("#coach-title").textContent = "";
  document.querySelector("#coach-text").textContent = "";
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
    summary: null,
    showResults: false,
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

async function startSetupQuiz() {
  const token = ++quizToken;
  const button = document.querySelector("#start-setup");
  const status = document.querySelector("#setup-status");
  const subject = subjectById(state.quizPick.subject);
  const count = state.quizPick.count === 10 ? 10 : 5;
  const note = state.notes.find((item) => String(item.id) === String(state.quizPick.noteId));
  const setup = { subject: subject.id, noteId: note ? note.id : null, count };
  let url = "/api/quizzes/topic";
  let body = JSON.stringify({ topic: subject.label, count });
  let fallback = subject.label;
  if (note) {
    state.selectedNoteId = note.id;
    fallback = note.title || fallback;
    url = "/api/quizzes/note/" + encodeURIComponent(note.id);
    body = JSON.stringify({ count });
  }
  button.disabled = true;
  status.className = "status";
  status.textContent = "";
  showScreen("quiz-loading");
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body });
    const data = await response.json().catch(() => ({}));
    if (token !== quizToken) return;
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) {
      showScreen("quiz");
      status.className = "status error";
      status.textContent = detailText(data, "Квиз не собрался.");
      return;
    }
    beginQuiz("topic", data, fallback, token);
    quiz.setup = setup;
    quiz.noteId = note ? note.id : null;
  } finally {
    if (token === quizToken) button.disabled = false;
  }
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
      showScreen(kind === "review" ? "sky" : kind === "errors" ? "quiz-errors" : "quiz");
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
      quiz.showResults = true;
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
  quiz.showResults = false;
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
    quiz.summary = data.summary || quiz.summary || null;
    quiz.showResults = false;
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
  if (!line) return;
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
  const homeResponse = await fetch("/api/home");
  if (homeResponse.status === 401) return showGate(LOCKED);
  if (homeResponse.ok) state.home = await homeResponse.json();
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
    else if (target === "quiz-origin") showScreen(state.quizKind === "errors" ? "quiz-errors" : state.quizKind === "review" ? "sky" : "quiz");
    else if (target === "quiz-return") showScreen(state.quizReturn || "home");
    else if (target === "notes") openNotes();
    else showScreen(target);
  });
});

tabs.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    const name = button.dataset.tab;
    if (name === "notes") openNotes();
    else if (name === "quiz") showScreen("quiz");
    else if (name === "errors") openErrors("errors").catch(() => showGate("Кабинет не смог открыть базу."));
    else if (name === "profile") openProfile();
    else showScreen("home");
  });
});

document.querySelector("#subject-grid").addEventListener("click", (event) => {
  const button = event.target.closest("[data-subject]");
  if (!button) return;
  state.quizPick.subject = button.dataset.subject;
  state.quizPick.noteId = null;
  paintQuizSetup();
});
document.querySelector("#topic-chips").addEventListener("click", (event) => {
  const button = event.target.closest("[data-topic]");
  if (!button) return;
  state.quizPick.noteId = button.dataset.topic || null;
  paintQuizSetup();
});
document.querySelector("#count-chips").addEventListener("click", (event) => {
  const button = event.target.closest("[data-count]");
  if (!button) return;
  state.quizPick.count = Number(button.dataset.count) === 10 ? 10 : 5;
  paintQuizSetup();
});
document.querySelector("#quiz-error-card").addEventListener("click", () => {
  const status = document.querySelector("#setup-status");
  if (!Number(state.home.open_errors || 0)) {
    status.className = "status error";
    status.textContent = "Сначала пришли решение с ошибкой — тогда соберу квиз.";
    return;
  }
  status.textContent = "";
  status.className = "status";
  startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#start-setup").addEventListener("click", () => startSetupQuiz().catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#close-errors").addEventListener("click", () => openErrors("home").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelectorAll("[data-quick]").forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.quick;
    if (action === "quiz-topic") showScreen("quiz");
    else if (action === "quiz-errors") openErrors("home").catch(() => showGate("Кабинет не смог открыть базу."));
    else if (action === "note-new") {
      document.querySelector("#new-note-input").value = "";
      document.querySelector("#new-note-status").textContent = "";
      showScreen("note-new");
    } else if (action === "ask") {
      const app = bridge();
      if (app && app.close) app.close();
    }
  });
});
document.querySelector("#notes-new").addEventListener("click", () => {
  document.querySelector("#new-note-input").value = "";
  document.querySelector("#new-note-status").textContent = "";
  showScreen("note-new");
});
document.querySelector("#notes-filters").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-notes-filter]");
  if (!chip) return;
  state.notesFilter = chip.dataset.notesFilter || "all";
  paintNotes();
});
document.querySelector("#error-filters").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-errors-filter]");
  if (!chip) return;
  state.errorsFilter = chip.dataset.errorsFilter || "all";
  paintErrorBank();
});
document.querySelector("#new-note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#new-note-input");
  const status = document.querySelector("#new-note-status");
  const button = document.querySelector("#save-new-note");
  const text = input.value.trim();
  if (!text) {
    status.className = "status error";
    status.textContent = "Напиши текст конспекта.";
    return;
  }
  button.disabled = true;
  status.textContent = "";
  status.className = "status";
  try {
    const response = await fetch("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) return showGate(LOCKED);
    if (!response.ok) {
      status.className = "status error";
      status.textContent = detailText(data, "Не удалось сохранить.");
      return;
    }
    state.notes.push({
      id: data.id,
      title: data.title,
      subject: data.subject,
      preview: data.body || "",
      pinned: false,
      source: data.source || "manual",
      created_at: data.created_at
    });
    sortNotes();
    paintNotes();
    paintTopics();
    showScreen("notes");
  } finally {
    button.disabled = false;
  }
});

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
  if (quiz.setup) startSetupQuiz().catch(() => showGate("Кабинет не смог открыть базу."));
  else if (quiz.kind === "topic") startQuiz("topic").catch(() => showGate("Кабинет не смог открыть базу."));
  else if (quiz.kind === "errors") startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу."));
  else startQuiz("review").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#quiz-retry").addEventListener("click", () => startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#quiz-chat").addEventListener("click", () => {
  const app = webApp();
  if (app && app.close) app.close();
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
document.querySelector("#grade-row").addEventListener("click", (event) => {
  const button = event.target.closest("[data-grade]");
  if (!button) return;
  setGrade(button.dataset.grade, true);
});
document.querySelector("#goal-row").addEventListener("click", (event) => {
  const button = event.target.closest("[data-goal]");
  if (!button) return;
  setGoal(button.dataset.goal, true);
});
styleSlider.addEventListener("input", () => {
  const style = styleSlider.value === "1" ? "detail" : "short";
  paintStyle(style);
  state.profile.explain_style = style;
  if (saveButton.textContent !== "Сохранить") saveButton.textContent = "Сохранить";
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
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
  const style = styleSlider.value === "1" ? "detail" : "short";
  const goal = goalInput.value || null;
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
  if (!goal || !["oge", "ege", "self"].includes(goal)) {
    saveStatus.className = "status error";
    saveStatus.textContent = "Выбери цель.";
    return;
  }
  saveButton.disabled = true;
  saveStatus.textContent = "";
  saveStatus.className = "status";
  try {
    const payload = {
      first_name: name,
      grade,
      reminders: remindersInput.checked,
      explain_style: style,
      exam_goal: goal
    };
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
    setGoal(data.exam_goal || "", false);
    paintStyle(data.explain_style || "short");
    remindersInput.checked = data.reminders !== false;
    paintIdentity();
    paintHome();
    paintNotes();
    paintTopics();
    paintProfileStats();
    previewFromForm();
    saveButton.textContent = "Сохранено";
  } finally {
    saveButton.disabled = false;
  }
});

const app = bridge();
if (app && app.BackButton && app.BackButton.onClick) app.BackButton.onClick(goBack);

async function ensureFreshBuild() {
  const meta = document.querySelector('meta[name="lumi-build"]');
  const embedded = meta ? meta.content : "";
  let remote = "";
  try {
    const response = await fetch("/api/version", { cache: "no-store" });
    if (response.ok) {
      const data = await response.json();
      remote = String(data.version || "");
    }
  } catch (error) {
    return false;
  }
  if (!remote || !embedded || remote === embedded) return false;
  if (sessionStorage.getItem("lumi-bust") === remote) return false;
  sessionStorage.setItem("lumi-bust", remote);
  if ("caches" in window) {
    try {
      const names = await caches.keys();
      await Promise.all(names.map((name) => caches.delete(name)));
    } catch (error) { /* WebView может запретить Cache Storage */ }
  }
  const url = new URL(window.location.href);
  url.searchParams.set("v", remote);
  window.location.replace(url.toString());
  return true;
}

async function enter() {
  if (await ensureFreshBuild()) return;
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
