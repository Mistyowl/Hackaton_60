const LOCKED = "Открой кабинет кнопкой в чате MAX.";
const DENIED = "Кабинет пока открыт только для участников теста.";
const NO_CHAT = "Напиши Люми в чате MAX — там можно спросить задачу или прислать фото.";
const EMPTY_NOTES = "Конспекты появятся, когда в чате нажмёшь «В конспект».";
const EMPTY_ERRORS = "Банк ошибок пуст. Ошибки появятся после квиза или проверки решения в чате.";
const EMPTY_GRAPH = "Пока карта пустая. Сфоткай задачу в чате или пройди квиз.";
const EMPTY_CALENDAR = "Пока нечего ставить в календарь.";
const CALENDAR_OPENED = "В Календаре нажми «Найти», потом «Добавить подписку».";
const CALENDAR_READY = "Готово — нажми «В календарь» ещё раз.";
const GRAPH_FOCUS = "#7f6df2";
const CHEV = '<span class="chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg></span>';
const PIN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 4h6l-1 7 3 3v2H7v-2l3-3-1-7z"/><path d="M12 16v5"/></svg>';
const MARK = '<span class="mark"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg></span>';
const CHECK_MINI = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg>';
const CROSS_MINI = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 6l12 12"/><path d="M18 6L6 18"/></svg>';
const SWIPE_REVEAL = 118;
const DEMO_TOUR_KEY = "lumi-demo-tour";

const gate = document.querySelector("#gate");
const cabinet = document.querySelector("#cabinet");
const tabs = document.querySelector("#tabs");
const nameInput = document.querySelector("#name-input");
const gradeInput = document.querySelector("#grade-input");
const goalInput = document.querySelector("#goal-input");
const saveButton = document.querySelector("#save-profile");
const saveStatus = document.querySelector("#save-status");
const styleInput = document.querySelector("#style-input");
const remindersInput = document.querySelector("#reminders-input");
let savedProfile = "";

const state = {
  profile: {
    first_name: "", grade: null, explain_style: null, exam_goal: null, reminders: true,
    quizzes_done: 0, closed_errors: 0, accuracy: 0, streak_days: 0, cabinet_tour: false
  },
  home: { open_errors: 0, streak_days: 0, week_done: 0, week_goal: 5 },
  notes: [],
  errors: [],
  errorCounts: { new: 0, active: 0, closed: 0 },
  notesFilter: "all",
  errorsFilter: "all",
  selectedNoteId: null,
  quizPick: { subject: "algebra", noteId: null, count: 5 },
  noteReturn: "notes",
  quizReturn: "home",
  quizKind: "topic",
  loadingFrom: "quiz",
  screen: "home",
  calendarUrl: null,
  calendarHttpsUrl: null,
  graph: { nodes: [], edges: [], due_count: 0 },
  graphFocus: null,
  graphReturn: "quiz-errors",
  graphSkillId: null
};
let quiz = null;
let currentNote = null;
let quizToken = 0;
let noteTicket = 0;
let errorsTicket = 0;
let searchTicket = 0;
let graphTicket = 0;
let graphAnim = 0;
let graphSim = null;
const deletingNotes = new Set();
const pinningNotes = new Set();
let swipeDrag = null;
let pendingDelete = null;
let openErrorId = null;

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
  document.querySelector("#gate-text").textContent = text;
  document.querySelector(".gate-hint").hidden = text !== LOCKED;
  document.querySelector("#gate-title").textContent = text === LOCKED
    ? "Кабинет открывается из чата"
    : text === DENIED ? "Доступ пока закрыт" : "Что-то пошло не так";
}

function showCabinet() {
  gate.hidden = true;
  cabinet.hidden = false;
  tabs.hidden = false;
}

function locked(response) {
  if (response.status === 401) {
    showGate(LOCKED);
    return true;
  }
  if (response.status === 403) {
    showGate(DENIED);
    return true;
  }
  return false;
}

let toastTimer = null;

function showToast(text) {
  const toast = document.querySelector("#toast");
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
}

function backToChat(text) {
  const app = bridge();
  if (!app || !app.initData || !app.close) {
    showToast(NO_CHAT);
    return;
  }
  showToast(text);
  setTimeout(() => app.close(), 700);
}

function tabFor(screen) {
  if (screen === "notes" || screen === "note" || screen === "note-new") return "notes";
  if (screen === "profile") return "profile";
  if (
    screen === "quiz-errors"
    || screen === "skills-graph"
    || ((screen === "quiz-play" || screen === "quiz-loading") && (state.quizKind === "errors" || state.quizKind === "skill" || state.quizKind === "due"))
  ) return "errors";
  if (screen === "quiz" || screen.startsWith("quiz-")) return "quiz";
  return "home";
}

function closeConfirm() {
  pendingDelete = null;
  document.querySelector("#confirm-delete").hidden = true;
}

function closeSwipes() {
  document.querySelectorAll(".swipe").forEach((row) => setSwipeOpen(row, ""));
}

function showScreen(name) {
  closeSwipes();
  closeConfirm();
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
  document.body.classList.toggle("graph-open", name === "skills-graph");
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
  if (name === "quiz-loading") startLoadingTips();
  else clearInterval(loadingTimer);
  if (name === "home" || name === "quiz") refreshHome().catch(() => {});
  if (name === "quiz") paintQuizSetup();
  if (name === "skills-graph") {
    startGraphView();
  } else {
    stopGraphView();
  }
}

const LOADING_TIPS = [
  "Обычно это занимает до минуты — не закрывай экран.",
  "Если модель занята, Люми пробует запасной вариант.",
  "Подбираю вопросы под твой класс.",
  "Неверный ответ не страшен — вопрос вернётся в конце квиза.",
  "Ошибки из квиза попадут в банк, их можно закрепить позже."
];
let loadingTimer = null;

function startLoadingTips() {
  let index = 0;
  const tip = document.querySelector("#loading-tip");
  tip.textContent = LOADING_TIPS[0];
  clearInterval(loadingTimer);
  loadingTimer = setInterval(() => {
    index = (index + 1) % LOADING_TIPS.length;
    tip.textContent = LOADING_TIPS[index];
  }, 4000);
}

function cancelQuizLoading() {
  if (state.screen !== "quiz-loading") return;
  quizToken += 1;
  document.querySelector("#start-setup").disabled = false;
  document.querySelector("#start-topic").disabled = state.selectedNoteId == null;
  document.querySelector("#start-errors").disabled = !state.errors.some((row) => row.status !== "closed");
  const back = state.loadingFrom || "quiz";
  showScreen(back);
  if (back === "skills-graph") paintGraphChrome();
}

function quizOrigin() {
  if (quiz && quiz.origin && quiz.origin !== "quiz-loading" && quiz.origin !== "quiz-play") return quiz.origin;
  if (state.quizKind === "skill" || state.quizKind === "due") return "skills-graph";
  return state.quizKind === "errors" ? "quiz-errors" : "quiz";
}

function goBack() {
  const screen = state.screen;
  if (screen === "note" && !document.querySelector("#note-form").hidden) {
    cancelNoteEdit();
    return;
  }
  if (screen === "skills-graph") {
    if (!document.querySelector("#graph-sheet").hidden) {
      closeGraphSheet();
      return;
    }
    showScreen(state.graphReturn || "quiz-errors");
    return;
  }
  if (screen === "note") showScreen(state.noteReturn || "notes");
  else if (screen === "quiz-loading") cancelQuizLoading();
  else if (screen === "quiz-play") showScreen(quizOrigin());
  else if (screen === "quiz-topic" || screen === "quiz-errors") showScreen(state.quizReturn || "home");
  else if (screen === "note-new") showScreen("notes");
  else showScreen("home");
}

function closeGraphSheet() {
  state.graphFocus = null;
  document.querySelector("#graph-sheet").hidden = true;
  if (graphSim) graphSim.needsDraw = true;
}

function openSkillsGraph(from) {
  state.graphReturn = from || "quiz-errors";
  state.graphFocus = null;
  document.querySelector("#graph-sheet").hidden = true;
  document.querySelector("#graph-status").hidden = true;
  showScreen("skills-graph");
  refreshSkillsGraph().catch(() => showGate("Кабинет не смог открыть базу."));
}

async function refreshSkillsGraph(focusSkillId) {
  const ticket = ++graphTicket;
  const response = await fetch("/api/skills/graph");
  if (ticket !== graphTicket) return;
  if (locked(response)) return;
  if (!response.ok) {
    document.querySelector("#graph-status").hidden = false;
    document.querySelector("#graph-status").textContent = "Не удалось загрузить карту.";
    return;
  }
  const data = await response.json();
  if (ticket !== graphTicket) return;
  state.graph = {
    nodes: Array.isArray(data.nodes) ? data.nodes : [],
    edges: Array.isArray(data.edges) ? data.edges : [],
    due_count: Number(data.due_count || 0)
  };
  if (focusSkillId != null) state.graphFocus = "skill:" + focusSkillId;
  else if (state.graphFocus && !state.graph.nodes.some((node) => node.id === state.graphFocus)) {
    state.graphFocus = null;
  }
  paintGraphChrome();
  if (state.screen === "skills-graph") startGraphView();
}

function paintGraphChrome() {
  const empty = !state.graph.nodes.length;
  document.querySelector("#graph-empty").hidden = !empty;
  document.querySelector("#graph-due").disabled = empty || !state.graph.due_count;
  document.querySelector("#graph-hint").textContent = empty
    ? "Карта появится после первой ошибки или квиза"
    : (state.graph.due_count
      ? ("Пора повторить: " + state.graph.due_count)
      : "Тапни приём, чтобы повторить");
  if (state.graphFocus) {
    const node = state.graph.nodes.find((item) => item.id === state.graphFocus && item.kind === "skill");
    if (node) openGraphSheet(node);
    else closeGraphSheet();
  } else {
    document.querySelector("#graph-sheet").hidden = true;
  }
}

function openGraphSheet(node) {
  state.graphFocus = node.id;
  state.graphSkillId = node.skill_id;
  document.querySelector("#graph-sheet-subject").textContent = node.subject || "";
  document.querySelector("#graph-sheet-title").textContent = node.label || "Приём";
  const errors = Number(node.open_errors || 0);
  const status = node.due ? "Пора повторить" : "Закрепляется";
  const errText = errors
    ? (errors + " " + ruCount(errors, "ошибка", "ошибки", "ошибок") + " в банке")
    : "Открытых ошибок нет";
  document.querySelector("#graph-sheet-status").textContent = status + " · " + errText;
  document.querySelector("#graph-sheet").hidden = false;
  if (graphSim) graphSim.needsDraw = true;
}

function nodeRadius(node) {
  if (node.kind === "subject") return 10 + Math.min(8, (node.links || 1));
  if (node.kind === "skill") return 7 + Math.min(6, Number(node.open_errors || 0)) + (node.due ? 1 : 0);
  return 4.5;
}

function startGraphView() {
  stopGraphView();
  const canvas = document.querySelector("#graph-canvas");
  if (!canvas || state.screen !== "skills-graph") return;
  const ctx = canvas.getContext("2d");
  const nodes = state.graph.nodes.map((raw, index) => {
    const angle = (index / Math.max(1, state.graph.nodes.length)) * Math.PI * 2;
    return {
      ...raw,
      x: Math.cos(angle) * (80 + (index % 5) * 18),
      y: Math.sin(angle) * (80 + (index % 7) * 14),
      vx: 0,
      vy: 0,
      links: 0
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const edges = state.graph.edges
    .map((edge) => ({ source: byId.get(edge.from), target: byId.get(edge.to) }))
    .filter((edge) => edge.source && edge.target);
  edges.forEach((edge) => {
    edge.source.links += 1;
    edge.target.links += 1;
  });
  let width = 0;
  let height = 0;
  let dpr = 1;
  let scale = 1;
  let offsetX = 0;
  let offsetY = 0;
  let dragging = null;
  let panning = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;
  let pinch = null;
  let pulse = 0;

  function resize() {
    dpr = Math.max(1, window.devicePixelRatio || 1);
    width = canvas.clientWidth || window.innerWidth;
    height = canvas.clientHeight || window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (graphSim) graphSim.needsDraw = true;
  }

  function screenToWorld(x, y) {
    return {
      x: (x - width / 2 - offsetX) / scale,
      y: (y - height / 2 - offsetY) / scale
    };
  }

  function hitTest(x, y) {
    const point = screenToWorld(x, y);
    let best = null;
    let bestDist = Infinity;
    nodes.forEach((node) => {
      const dx = node.x - point.x;
      const dy = node.y - point.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const hit = nodeRadius(node) + 8 / scale;
      if (dist <= hit && dist < bestDist) {
        best = node;
        bestDist = dist;
      }
    });
    return best;
  }

  function step() {
    const alpha = 0.08;
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const min = (nodeRadius(a) + nodeRadius(b)) * 3.2;
        const force = 420 / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
        if (dist < min) {
          const push = (min - dist) * 0.05;
          a.vx += (dx / dist) * push;
          a.vy += (dy / dist) * push;
          b.vx -= (dx / dist) * push;
          b.vy -= (dy / dist) * push;
        }
      }
    }
    edges.forEach((edge) => {
      const a = edge.source;
      const b = edge.target;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const rest = edge.target.kind === "error" ? 58 : 96;
      const pull = (dist - rest) * 0.012;
      const fx = (dx / dist) * pull;
      const fy = (dy / dist) * pull;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    });
    nodes.forEach((node) => {
      node.vx += -node.x * 0.003;
      node.vy += -node.y * 0.003;
      node.vx *= 0.86;
      node.vy *= 0.86;
      if (!dragging || dragging.id !== node.id) {
        node.x += node.vx * alpha * 12;
        node.y += node.vy * alpha * 12;
      }
    });
  }

  function draw() {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#1e1e1e";
    ctx.fillRect(0, 0, width, height);
    ctx.save();
    ctx.translate(width / 2 + offsetX, height / 2 + offsetY);
    ctx.scale(scale, scale);
    const focus = state.graphFocus;
    const linked = new Set();
    if (focus) {
      linked.add(focus);
      edges.forEach((edge) => {
        if (edge.source.id === focus || edge.target.id === focus) {
          linked.add(edge.source.id);
          linked.add(edge.target.id);
        }
      });
    }
    edges.forEach((edge) => {
      const active = focus && (edge.source.id === focus || edge.target.id === focus);
      ctx.beginPath();
      ctx.moveTo(edge.source.x, edge.source.y);
      ctx.lineTo(edge.target.x, edge.target.y);
      ctx.strokeStyle = active ? GRAPH_FOCUS : "rgba(255,255,255,0.12)";
      ctx.lineWidth = (active ? 1.6 : 1) / scale;
      ctx.globalAlpha = focus && !active ? 0.25 : 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
    });
    nodes.forEach((node) => {
      const active = focus === node.id;
      const dimmed = focus && !linked.has(node.id);
      const radius = nodeRadius(node);
      let fill = "#d8d8d8";
      let alpha = 1;
      if (node.kind === "subject") fill = "#f5f5f5";
      if (node.kind === "skill") {
        const bright = Math.max(0.25, Math.min(1, Number(node.brightness || 0.5)));
        alpha = bright;
        fill = active ? GRAPH_FOCUS : "#eaeaea";
      }
      if (node.kind === "error") {
        fill = active ? GRAPH_FOCUS : "#9a9a9a";
        alpha = 0.85;
      }
      if (dimmed) alpha *= 0.28;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.globalAlpha = alpha;
      ctx.fill();
      if (node.kind === "skill" && node.due) {
        ctx.globalAlpha = dimmed ? 0.25 : (0.55 + 0.35 * Math.sin(pulse));
        ctx.strokeStyle = GRAPH_FOCUS;
        ctx.lineWidth = 1.8 / scale;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 3.5, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (active) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = GRAPH_FOCUS;
        ctx.lineWidth = 2.2 / scale;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 2, 0, Math.PI * 2);
        ctx.stroke();
      }
      const showLabel = !focus || linked.has(node.id) || node.kind === "subject";
      if (showLabel && scale > 0.55) {
        ctx.globalAlpha = dimmed ? 0.25 : (active || node.kind === "subject" ? 0.95 : 0.7);
        ctx.fillStyle = "#f0f0f0";
        ctx.font = `${Math.max(10, 12 / scale)}px -apple-system, BlinkMacSystemFont, Roboto, sans-serif`;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        const label = String(node.label || "").slice(0, 28);
        ctx.fillText(label, node.x + radius + 6, node.y);
      }
      ctx.globalAlpha = 1;
    });
    ctx.restore();
  }

  function frame() {
    if (!graphSim) return;
    pulse += 0.05;
    for (let i = 0; i < 2; i += 1) step();
    draw();
    graphAnim = requestAnimationFrame(frame);
  }

  function pointerPos(event) {
    const rect = canvas.getBoundingClientRect();
    const touch = event.touches && event.touches[0];
    const clientX = touch ? touch.clientX : event.clientX;
    const clientY = touch ? touch.clientY : event.clientY;
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  function onPointerDown(event) {
    if (event.touches && event.touches.length === 2) {
      const a = event.touches[0];
      const b = event.touches[1];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      pinch = { dist, scale };
      dragging = null;
      panning = false;
      return;
    }
    const pos = pointerPos(event);
    const hit = hitTest(pos.x, pos.y);
    moved = false;
    lastX = pos.x;
    lastY = pos.y;
    if (hit && hit.kind === "skill") {
      dragging = hit;
      hit.vx = 0;
      hit.vy = 0;
    } else {
      panning = true;
      dragging = null;
    }
  }

  function onPointerMove(event) {
    if (event.touches && event.touches.length === 2 && pinch) {
      event.preventDefault();
      const a = event.touches[0];
      const b = event.touches[1];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      scale = Math.max(0.45, Math.min(2.4, pinch.scale * (dist / Math.max(1, pinch.dist))));
      return;
    }
    if (!dragging && !panning) return;
    event.preventDefault();
    const pos = pointerPos(event);
    const dx = pos.x - lastX;
    const dy = pos.y - lastY;
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    if (dragging) {
      const world = screenToWorld(pos.x, pos.y);
      dragging.x = world.x;
      dragging.y = world.y;
      dragging.vx = 0;
      dragging.vy = 0;
    } else if (panning) {
      offsetX += dx;
      offsetY += dy;
    }
    lastX = pos.x;
    lastY = pos.y;
  }

  function onPointerUp(event) {
    if (event.touches && event.touches.length >= 2) return;
    if (!moved) {
      const pos = pointerPos(event.changedTouches ? event.changedTouches[0] : event);
      const rect = canvas.getBoundingClientRect();
      const local = event.changedTouches
        ? { x: event.changedTouches[0].clientX - rect.left, y: event.changedTouches[0].clientY - rect.top }
        : pos;
      const hit = hitTest(local.x, local.y);
      if (hit && hit.kind === "skill") openGraphSheet(hit);
      else if (hit && hit.kind === "error" && hit.skill_id) {
        const skill = nodes.find((node) => node.kind === "skill" && Number(node.skill_id) === Number(hit.skill_id));
        if (skill) openGraphSheet(skill);
      } else closeGraphSheet();
    }
    dragging = null;
    panning = false;
    pinch = null;
  }

  function onWheel(event) {
    event.preventDefault();
    const next = scale * (event.deltaY < 0 ? 1.08 : 0.92);
    scale = Math.max(0.45, Math.min(2.4, next));
  }

  canvas.addEventListener("mousedown", onPointerDown);
  window.addEventListener("mousemove", onPointerMove);
  window.addEventListener("mouseup", onPointerUp);
  canvas.addEventListener("touchstart", onPointerDown, { passive: false });
  canvas.addEventListener("touchmove", onPointerMove, { passive: false });
  canvas.addEventListener("touchend", onPointerUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });

  const onResize = () => resize();
  window.addEventListener("resize", onResize);
  graphSim = {
    needsDraw: true,
    onResize,
    destroy() {
      canvas.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("mousemove", onPointerMove);
      window.removeEventListener("mouseup", onPointerUp);
      canvas.removeEventListener("touchstart", onPointerDown);
      canvas.removeEventListener("touchmove", onPointerMove);
      canvas.removeEventListener("touchend", onPointerUp);
      canvas.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", onResize);
    }
  };
  resize();
  frame();
}

function stopGraphView() {
  if (graphAnim) {
    cancelAnimationFrame(graphAnim);
    graphAnim = 0;
  }
  if (graphSim && graphSim.destroy) graphSim.destroy();
  else if (graphSim && graphSim.onResize) window.removeEventListener("resize", graphSim.onResize);
  graphSim = null;
}

async function startGraphQuiz(kind, skillId) {
  const token = ++quizToken;
  const status = document.querySelector("#graph-status");
  let url = "/api/quizzes/due";
  let fallback = "Повторить сегодня";
  if (kind === "skill") {
    if (skillId == null) return;
    url = "/api/quizzes/skill/" + encodeURIComponent(skillId);
    const node = state.graph.nodes.find((item) => Number(item.skill_id) === Number(skillId));
    fallback = node && node.label ? ("Повторить: " + node.label) : "Повторить приём";
  }
  status.hidden = true;
  status.textContent = "";
  state.loadingFrom = "skills-graph";
  showScreen("quiz-loading");
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const data = await response.json().catch(() => ({}));
    if (token !== quizToken) return;
    if (locked(response)) return;
    if (!response.ok) {
      showScreen("skills-graph");
      const message = detailText(data, "Квиз не собрался.");
      status.hidden = false;
      status.textContent = message;
      showToast(message);
      return;
    }
    beginQuiz(kind, data, fallback, token, {
      origin: "skills-graph",
      skillId: skillId != null ? Number(skillId) : null
    });
  } catch (error) {
    if (token !== quizToken) return;
    showScreen("skills-graph");
    status.hidden = false;
    status.textContent = "Не удалось дождаться квиза.";
    showToast("Не удалось дождаться квиза.");
    throw error;
  }
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
  const text = String(subject || "").trim().toLowerCase();
  if (text.includes("геометр")) return "Δ";
  if (text.includes("физик")) return "⚛";
  if (text.includes("русск")) return "Аа";
  if (text.includes("хими")) return "Cl";
  if (text.includes("англ")) return "En";
  if (text.includes("математ") || text.includes("алгебр")) return "Σ";
  if (!text || text === "другое") return "✎";
  return text.slice(0, 2).replace(/^./, (char) => char.toUpperCase());
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
  if (locked(response)) return;
  if (!response.ok) return;
  state.home = await response.json();
  if (state.screen === "home") paintHome();
  paintQuizSetup();
}

let profileTicket = 0;
async function refreshProfile() {
  const ticket = ++profileTicket;
  const response = await fetch("/api/profile");
  if (ticket !== profileTicket) return;
  if (locked(response)) return;
  if (!response.ok) return;
  const fresh = await response.json();
  ["quizzes_done", "closed_errors", "accuracy", "streak_days"].forEach((key) => {
    state.profile[key] = fresh[key];
  });
  paintProfileStats();
}

function filteredNotes() {
  const filter = state.notesFilter || "all";
  if (filter === "all") return state.notes.slice();
  if (filter === "chat") return state.notes.filter((note) => fromChat(note));
  return state.notes.filter((note) => noteSubject(note) === filter.slice(2));
}

function noteSubject(note) {
  return String(note.subject || "").trim() || "Другое";
}

function paintNotesFilters() {
  const box = document.querySelector("#notes-filters");
  if (!box) return;
  const subjects = Array.from(new Set(state.notes.map(noteSubject))).sort((left, right) => left.localeCompare(right, "ru"));
  const chips = [{ id: "all", label: "Все" }];
  if (state.notes.some(fromChat)) chips.push({ id: "chat", label: "Из чата" });
  subjects.forEach((name) => chips.push({ id: "s:" + name, label: name }));
  if (!chips.some((chip) => chip.id === state.notesFilter)) state.notesFilter = "all";
  box.hidden = state.notes.length === 0 || document.querySelector("#search-input").value.trim() !== "";
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
    const key = noteSubject(note);
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
  { id: "algebra", label: "Алгебра" },
  { id: "geometry", label: "Геометрия" },
  { id: "physics", label: "Физика" },
  { id: "russian", label: "Русский язык" },
  { id: "chemistry", label: "Химия" },
  { id: "english", label: "Английский" }
];
const NOTE_SUBJECTS = SUBJECTS.map((item) => item.label).concat("Другое");
const CUSTOM_TOPIC = "custom";

function subjectById(id) {
  return SUBJECTS.find((item) => item.id === id) || SUBJECTS[0];
}

function notesForSubject(subject) {
  return state.notes.filter((note) => noteSubject(note) === subject.label);
}

function paintSubjectPicks(box, picked, extra = "") {
  const names = extra && !NOTE_SUBJECTS.includes(extra) ? [extra].concat(NOTE_SUBJECTS) : NOTE_SUBJECTS;
  const current = names.includes(picked) ? picked : "Другое";
  box.dataset.extra = extra;
  box.innerHTML = names.map((name) => `
    <button type="button" class="filter-chip${name === current ? " on" : ""}" data-pick-subject="${escapeHtml(name)}" aria-pressed="${name === current ? "true" : "false"}">${escapeHtml(name)}</button>
  `).join("");
  box.dataset.value = current;
}

function bindSubjectPicks(box) {
  box.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-pick-subject]");
    if (!chip) return;
    paintSubjectPicks(box, chip.dataset.pickSubject, box.dataset.extra || "");
  });
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
  const custom = state.quizPick.noteId === CUSTOM_TOPIC;
  if (state.quizPick.noteId != null && !custom && !topics.some((note) => String(note.id) === String(state.quizPick.noteId))) {
    state.quizPick.noteId = null;
  }
  const chips = [
    `<button type="button" class="pick${state.quizPick.noteId == null ? " on" : ""}" data-topic="">Все темы</button>`,
    `<button type="button" class="pick${custom ? " on" : ""}" data-topic="${CUSTOM_TOPIC}">✎ Своя тема</button>`
  ];
  topics.slice(0, 8).forEach((note) => {
    const on = String(note.id) === String(state.quizPick.noteId);
    chips.push(`<button type="button" class="pick${on ? " on" : ""}" data-topic="${escapeHtml(note.id)}">${escapeHtml(note.title || "Конспект")}</button>`);
  });
  document.querySelector("#topic-chips").innerHTML = chips.join("");
  document.querySelector("#topic-hint").hidden = topics.length > 0 || custom;
  document.querySelector("#custom-topic-wrap").hidden = !custom;
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
    if (!goalInput.value) setGoal(defaultGoal(grade), false);
    previewFromForm();
    markProfileDirty();
  }
}

function setGoal(value, notify) {
  const goal = value == null || value === "" ? "" : String(value);
  goalInput.value = goal;
  document.querySelectorAll("#goal-row [data-goal]").forEach((button) => {
    button.classList.toggle("on", button.dataset.goal === goal);
  });
  if (notify) {
    previewFromForm();
    markProfileDirty();
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

function setStyle(style, markDirty) {
  const value = style === "detail" ? "detail" : "short";
  styleInput.value = value;
  document.querySelectorAll("#style-row [data-style]").forEach((button) => {
    const on = button.dataset.style === value;
    button.classList.toggle("on", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const text = document.querySelector("#style-preview-text");
  if (text) {
    text.textContent = value === "detail"
      ? "Разберу шаг за шагом и покажу, на что смотреть."
      : "Объясню понятно, без лишнего.";
  }
  if (markDirty) markProfileDirty();
}

function paintStyle(style) {
  setStyle(style || "short", false);
}

function paintProfileStats() {
  const quizzesDone = state.profile.quizzes_done || 0;
  const closedErrors = state.profile.closed_errors || 0;
  document.querySelector("#stat-quizzes").textContent = String(quizzesDone);
  document.querySelector("#stat-quizzes-label").textContent = ruCount(quizzesDone, "квиз", "квиза", "квизов");
  document.querySelector("#stat-closed").textContent = String(closedErrors);
  document.querySelector("#stat-closed-label").textContent = ruCount(closedErrors, "ошибка закрыта", "ошибки закрыты", "ошибок закрыто");
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
  savedProfile = profileSnapshot();
  markProfileDirty();
}

function profileSnapshot() {
  return JSON.stringify([
    nameInput.value.trim(),
    String(gradeInput.value || ""),
    goalInput.value || "",
    styleInput.value === "detail" ? "detail" : "short",
    remindersInput.checked
  ]);
}

function markProfileDirty() {
  const dirty = profileSnapshot() !== savedProfile;
  saveButton.disabled = !dirty;
  if (dirty) saveButton.textContent = "Сохранить";
}

function renderInline(text) {
  return escapeHtml(text)
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`\n]+)`/g, '<span class="formula">$1</span>');
}

function splitLongParagraph(text) {
  const cleaned = String(text || "").trim();
  if (!cleaned || cleaned.includes("\n") || cleaned.length < 350) return [cleaned].filter(Boolean);
  const parts = cleaned.split(/(?<=[.!?…])\s+(?=[А-ЯA-ZЁ])/);
  const chunks = [];
  let bucket = [];
  parts.forEach((part) => {
    bucket.push(part);
    if (bucket.join(" ").length >= 180 || bucket.length >= 3) {
      chunks.push(bucket.join(" "));
      bucket = [];
    }
  });
  if (bucket.length) chunks.push(bucket.join(" "));
  return chunks;
}

function lineKind(line) {
  const trimmed = String(line || "").trim();
  if (!trimmed) return "blank";
  if (/^`[^`]+`\s*$/.test(trimmed)) return "formula";
  if (/^(Пример|Формула):\s*`[^`]+`\s*$/i.test(trimmed)) return "formula";
  if (/^(Пример|Шаг\s*\d+|Ответ|Правило|Формула|Запомни):\s*/i.test(trimmed)) return "label";
  if (/^(\d+[.)]|[-•])\s+/.test(trimmed)) return "list";
  return "text";
}

function renderFormulaLine(line) {
  const trimmed = String(line || "").trim();
  const labeled = trimmed.match(/^(Пример|Формула):\s*`([^`]+)`\s*$/i);
  if (labeled) {
    return `<div class="formula-block"><span class="formula-label">${escapeHtml(labeled[1])}:</span> ${escapeHtml(labeled[2])}</div>`;
  }
  const plainFormula = trimmed.match(/^`([^`]+)`\s*$/);
  if (plainFormula) {
    return `<div class="formula-block">${escapeHtml(plainFormula[1])}</div>`;
  }
  return `<div class="formula-block">${renderInline(trimmed)}</div>`;
}

function renderLabeledLine(line) {
  const trimmed = String(line || "").trim();
  const match = trimmed.match(/^(Пример|Шаг\s*\d+|Ответ|Правило|Формула|Запомни):\s*(.*)$/i);
  if (!match) return `<p>${renderInline(trimmed)}</p>`;
  const rest = match[2] ? " " + renderInline(match[2]) : "";
  return `<p><b>${escapeHtml(match[1])}:</b>${rest}</p>`;
}

function renderListGroup(lines) {
  const ordered = /^(\d+[.)])\s+/.test(String(lines[0] || "").trim());
  const tag = ordered ? "ol" : "ul";
  const items = lines.map((line) => {
    const text = String(line || "").trim().replace(/^(\d+[.)]|[-•])\s+/, "");
    return `<li>${renderInline(text)}</li>`;
  }).join("");
  return `<${tag} class="note-list">${items}</${tag}>`;
}

function renderBlockLines(lines) {
  const chunks = [];
  let list = [];
  const flushList = () => {
    if (!list.length) return;
    chunks.push(renderListGroup(list));
    list = [];
  };
  lines.forEach((line) => {
    const kind = lineKind(line);
    if (kind === "list") {
      list.push(line);
      return;
    }
    flushList();
    if (kind === "blank") return;
    if (kind === "formula") {
      chunks.push(renderFormulaLine(line));
      return;
    }
    if (kind === "label") {
      chunks.push(renderLabeledLine(line));
      return;
    }
    splitLongParagraph(line).forEach((part) => {
      chunks.push(`<p>${renderInline(part)}</p>`);
    });
  });
  flushList();
  return chunks.join("");
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
    if (block.title === "Приём") {
      const skill = plain(block.lines.join(" "));
      if (!skill) return "";
      return `<div class="note-skill"><span aria-hidden="true">💡</span><strong>${escapeHtml(skill)}</strong></div>`;
    }
    const content = renderBlockLines(block.lines);
    if (!content && !block.title) return "";
    const heading = block.title ? `<h3>${escapeHtml(block.title)}</h3>` : "";
    return `<div class="note-block">${heading}${content || "<p></p>"}</div>`;
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
  if (locked(response)) return;
  if (!response.ok) return;
  const note = await response.json();
  currentNote = note;
  document.querySelector("#note-form").hidden = true;
  document.querySelector("#note-body").hidden = false;
  document.querySelector("#edit-note").hidden = false;
  document.querySelector("#note-actions").hidden = false;
  paintNoteHead(note);
  document.querySelector("#note-body").innerHTML = renderNoteBody(note.body || "");
  showScreen("note");
}

function sourceLabel(source) {
  if (source === "photo") return "по фото";
  if (source === "chat") return "из чата";
  if (source === "manual") return "свой";
  return "";
}

function paintNoteHead(note) {
  document.querySelector("#note-title").textContent = note.title || "Конспект";
  const parts = [noteSubject(note), shortDate(note.created_at), sourceLabel(note.source)].filter(Boolean);
  document.querySelector("#note-meta").textContent = parts.join(" · ");
  const stored = state.notes.find((item) => String(item.id) === String(note.id));
  const pinned = stored ? Boolean(stored.pinned) : Boolean(note.pinned);
  document.querySelector("#note-pin").textContent = pinned ? "Открепить" : "Закрепить";
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
  refreshProfile().catch(() => {});
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
    const source = row.source === "quiz"
      ? `<span class="tag-source tag-quiz">Из квиза</span>`
      : `<span class="tag-source tag-chat">Из чата</span>`;
    const open = String(row.id) === String(openErrorId);
    const train = row.status === "closed"
      ? `<p class="error-closed-note">Закреплена: три верных ответа подряд.</p>`
      : `<button type="button" class="error-train" data-train>Потренировать</button>`;
    const mistake = row.mistake && row.mistake !== row.skill
      ? `<p class="error-detail"><b>Где споткнулся:</b> ${renderInline(row.mistake)}</p>`
      : "";
    return `<article class="error-card${open ? " open" : ""}" data-error="${escapeHtml(row.id)}" tabindex="0" role="button" aria-expanded="${open ? "true" : "false"}">
      <div class="error-card-top">
        <span class="error-card-meta">${escapeHtml(meta)}</span>
        <span class="error-status ${escapeHtml(row.status || "new")}">${escapeHtml(errorStatusLabel(row.status))}</span>
      </div>
      <p class="error-card-q">${renderInline(row.problem || row.mistake || "Ошибка")}</p>
      ${source}
      <div class="error-progress">
        <div class="error-bars" aria-hidden="true">${bars}</div>
        <span>${progress} из 3 верных</span>
      </div>
      <div class="error-more"${open ? "" : " hidden"}>
        ${mistake}
        ${train}
      </div>
    </article>`;
  }).join("");
}

function toggleErrorCard(card) {
  const id = card.dataset.error;
  openErrorId = String(openErrorId) === id ? null : id;
  paintErrorBank();
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
  if (locked(response)) return;
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
  prepareCalendarLink().catch(() => {
    state.calendarUrl = null;
    state.calendarHttpsUrl = null;
  });
}

async function prepareCalendarLink() {
  state.calendarUrl = null;
  state.calendarHttpsUrl = null;
  const response = await fetch("/api/calendar/link", { method: "POST" });
  if (locked(response)) return false;
  if (!response.ok) {
    return false;
  }
  const data = await response.json().catch(() => ({}));
  if (typeof data.token !== "string" || !data.token) {
    return false;
  }
  const httpsUrl = new URL(
    "/api/calendar.ics?t=" + encodeURIComponent(data.token),
    window.location.origin
  ).href;
  // webcal:// — родной экран подписки iOS; https оставляем запасным.
  state.calendarUrl = httpsUrl.replace(/^https:/i, "webcal:");
  state.calendarHttpsUrl = httpsUrl;
  return true;
}

function openCalendarLink(url) {
  const app = bridge();
  // В WebView MAX .ics молчит — нужен системный Календарь / Safari через Bridge.
  if (app && typeof app.openLink === "function") {
    try {
      app.openLink(url);
      return;
    } catch (error) {
      if (state.calendarHttpsUrl && url !== state.calendarHttpsUrl) {
        app.openLink(state.calendarHttpsUrl);
        return;
      }
      throw error;
    }
  }
  window.open(url, "_blank", "noopener");
}

async function downloadCalendar() {
  const button = document.querySelector("#export-calendar");
  const status = document.querySelector("#error-status");
  button.disabled = true;
  status.className = "status";
  status.textContent = "";
  try {
    // openLink работает только в том же клике — без await перед вызовом.
    const url = state.calendarUrl;
    if (url) {
      openCalendarLink(url);
      status.className = "status";
      status.textContent = CALENDAR_OPENED;
      showToast(CALENDAR_OPENED);
      return;
    }
    const ready = await prepareCalendarLink();
    if (!ready || !state.calendarUrl) {
      status.className = "status error";
      status.textContent = EMPTY_CALENDAR;
      showToast(EMPTY_CALENDAR);
      return;
    }
    status.className = "status";
    status.textContent = CALENDAR_READY;
    showToast(CALENDAR_READY);
  } catch (error) {
    status.className = "status error";
    status.textContent = "Не удалось открыть календарь.";
    showToast("Не удалось открыть календарь.");
    throw error;
  } finally {
    button.disabled = false;
  }
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
  return items.map((item) => renderInline(item)).join(", ");
}

function paintQuizResults() {
  const summary = quiz.summary || {};
  const correct = Number(summary.correct || 0);
  const total = Number(summary.total || quiz.total || 0);
  const review = summary.review || [];
  const perfect = !review.length && correct >= total && total > 0;
  document.querySelector("#result-mascot").src = "/static/lumi-happy.png";
  const time = document.querySelector("#result-time");
  time.hidden = !quiz.elapsed;
  time.textContent = quiz.elapsed ? "Время: " + quiz.elapsed : "";
  document.querySelector("#result-score").textContent = correct + "/" + total;
  document.querySelector("#result-title").textContent = perfect
    ? "Супер! Все ответы с первой попытки."
    : "Не страшно — ошибки уже в банке, разберём их вместе.";
  document.querySelector("#result-strong").innerHTML = topicList(summary.strong);
  document.querySelector("#result-review").innerHTML = topicList(review);
  document.querySelector("#result-advice").innerHTML = renderInline(summary.advice
    || (perfect
      ? "Отлично! Можно взять новую тему."
      : "Рекомендую пройти квиз по ошибкам через день."));
  document.querySelector("#quiz-retry").hidden = !review.length;
}

function quizKicker() {
  if (quiz && quiz.kind === "errors") return "Тренировка";
  if (quiz && quiz.noteId != null) return "Квиз по конспекту";
  return "Квиз по теме";
}

function paintQuiz() {
  const live = document.querySelector("#quiz-live");
  const results = document.querySelector("#quiz-results");
  const button = document.querySelector("#quiz-go");
  document.querySelector("#quiz-kicker").textContent = quizKicker();
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
  const number = Math.min(quiz.number || 1, total);
  const done = Number(quiz.done || 0);
  progress.hidden = false;
  bar.hidden = false;
  progress.textContent = quiz.shown.retry
    ? "Ещё раз · решено " + done + " из " + total
    : "Вопрос " + number + " из " + total;
  fill.style.width = Math.min(100, Math.round((done / total) * 100)) + "%";
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
    document.querySelector("#coach-text").innerHTML = renderInline(quiz.feedback.explanation || "");
    button.textContent = quiz.finished ? "Результаты →" : "Дальше →";
    button.disabled = Boolean(quiz.busy);
    return;
  }
  feedback.hidden = true;
  document.querySelector("#coach-title").textContent = "";
  document.querySelector("#coach-text").textContent = "";
  button.textContent = "Проверить ответ";
  button.disabled = quiz.picked == null || Boolean(quiz.busy);
}

function beginQuiz(kind, data, fallback, token, extra = {}) {
  state.quizKind = kind;
  const question = data.question || null;
  quiz = {
    token,
    kind,
    title: data.title || fallback || "Квиз",
    total: data.total || 0,
    number: (data.asked || 0) + 1,
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
    skillIds: data.skill_ids || [],
    noteId: null,
    ...extra
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
  const custom = state.quizPick.noteId === CUSTOM_TOPIC ? document.querySelector("#custom-topic").value.trim() : "";
  if (state.quizPick.noteId === CUSTOM_TOPIC && custom.length < 2) {
    status.className = "status error";
    status.textContent = "Напиши тему, например: квадратные уравнения.";
    return;
  }
  const setup = { subject: subject.id, noteId: note ? note.id : null, count };
  let url = "/api/quizzes/topic";
  let body = JSON.stringify({ topic: custom ? subject.label + ": " + custom : subject.label, count });
  let fallback = custom || subject.label;
  if (note) {
    state.selectedNoteId = note.id;
    fallback = note.title || fallback;
    url = "/api/quizzes/note/" + encodeURIComponent(note.id);
    body = JSON.stringify({ count });
  }
  button.disabled = true;
  status.className = "status";
  status.textContent = "";
  state.loadingFrom = "quiz";
  showScreen("quiz-loading");
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body });
    const data = await response.json().catch(() => ({}));
    if (token !== quizToken) return;
    if (locked(response)) return;
    if (!response.ok) {
      showScreen("quiz");
      const message = detailText(data, "Квиз не собрался. Попробуй ещё раз через минуту.");
      status.className = "status error";
      status.textContent = message;
      showToast(message);
      return;
    }
    beginQuiz("topic", data, fallback, token, { setup, noteId: note ? note.id : null });
  } catch (error) {
    if (token !== quizToken) return;
    showScreen("quiz");
    const message = "Не удалось дождаться квиза. Попробуй ещё раз.";
    status.className = "status error";
    status.textContent = message;
    showToast(message);
    throw error;
  } finally {
    if (token === quizToken) button.disabled = false;
  }
}

async function startQuiz(kind) {
  const token = ++quizToken;
  const button = document.querySelector(kind === "topic" ? "#start-topic" : "#start-errors");
  const status = document.querySelector(kind === "topic" ? "#topic-status" : "#error-status");
  let fallback = kind === "errors" ? "Квиз по ошибкам" : "Квиз";
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
  if (button) button.disabled = true;
  if (status) {
    status.className = "status";
    status.textContent = "";
  }
  const origin = state.screen === "quiz-play" || state.screen === "quiz-loading" ? quizOrigin() : state.screen;
  state.loadingFrom = origin;
  showScreen("quiz-loading");
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body });
    const data = await response.json().catch(() => ({}));
    if (token !== quizToken) return;
    if (locked(response)) return;
    if (!response.ok) {
      showScreen(origin);
      const message = detailText(data, "Квиз не собрался.");
      if (status) {
        status.className = "status error";
        status.textContent = message;
      }
      showToast(message);
      return;
    }
    beginQuiz(kind, data, fallback, token, { noteId: kind === "topic" ? state.selectedNoteId : null, origin });
  } finally {
    if (token === quizToken && button) {
      if (kind === "topic") button.disabled = state.selectedNoteId == null;
      else button.disabled = !state.errors.filter((row) => row.status !== "closed").length;
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
  quiz.number = (next.asked || 0) + 1;
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
    if (locked(response)) return;
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
    quiz.misses = data.misses || [];
    quiz.summary = data.summary || quiz.summary || null;
    quiz.showResults = false;
    if (data.skill_id) quiz.skillIds = [data.skill_id].concat(quiz.skillIds || []);
    if (data.title) quiz.title = data.title;
    haptic(data.feedback && data.feedback.correct ? "ok" : "bad");
    if (quiz.finished) {
      refreshProfile().catch(() => {});
      refreshHome().catch(() => {});
      if (quiz.origin === "skills-graph" || quiz.kind === "skill" || quiz.kind === "due") {
        const focusId = data.skill_id || (quiz.skillIds && quiz.skillIds[0]) || quiz.skillId;
        refreshSkillsGraph(focusId).catch(() => {});
      }
    }
  } finally {
    if (quiz && quiz.token === token) {
      quiz.busy = false;
      paintQuiz();
    }
  }
}

async function loadCabinet() {
  const profileResponse = await fetch("/api/profile");
  if (locked(profileResponse)) return;
  if (!profileResponse.ok) return showGate("Кабинет не смог открыть базу.");
  state.profile = await profileResponse.json();
  const notesResponse = await fetch("/api/notes");
  if (locked(notesResponse)) return;
  if (!notesResponse.ok) return showGate("Кабинет не смог открыть базу.");
  const notesPayload = await notesResponse.json();
  state.notes = notesPayload.notes || [];
  const homeResponse = await fetch("/api/home");
  if (locked(homeResponse)) return;
  if (homeResponse.ok) state.home = await homeResponse.json();
  state.selectedNoteId = null;
  paintHome();
  paintNotes();
  paintTopics();
  showCabinet();
  showScreen("home");
  if (isDemo()) {
    if (!demoTourSeen()) startTour();
  } else if (!state.profile.cabinet_tour) {
    startTour();
  }
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
    if (locked(response)) return;
    if (!response.ok && response.status !== 404) return closeSwipes();
    state.notes = state.notes.filter((note) => String(note.id) !== key);
    if (String(state.selectedNoteId) === key) state.selectedNoteId = null;
    paintHome();
    paintNotes();
    paintTopics();
    refreshHome().catch(() => {});
    if (state.screen === "note" && currentNote && String(currentNote.id) === key) {
      currentNote = null;
      showScreen(state.noteReturn || "notes");
    }
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
    if (locked(response)) return;
    if (!response.ok) return closeSwipes();
    note.pinned = pinned;
    sortNotes();
    paintHome();
    paintNotes();
    paintTopics();
    if (currentNote && String(currentNote.id) === key) paintNoteHead(currentNote);
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
    else if (target === "quiz-origin") showScreen(quizOrigin());
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
  if (state.quizPick.noteId === CUSTOM_TOPIC) document.querySelector("#custom-topic").focus();
});
clearStatusOnInput("#custom-topic", "#setup-status");
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
    status.textContent = "Сначала пройди квиз или пришли решение на проверку.";
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
    else if (action === "note-new") openNewNote();
    else if (action === "ask") {
      backToChat("Открываю чат — напиши Люми задачу.");
    }
  });
});
function openNewNote() {
  document.querySelector("#new-note-title").value = "";
  document.querySelector("#new-note-input").value = "";
  document.querySelector("#new-note-status").textContent = "";
  document.querySelector("#new-note-status").className = "status";
  const filter = state.notesFilter || "";
  paintSubjectPicks(document.querySelector("#new-note-subject"), filter.startsWith("s:") ? filter.slice(2) : "Другое");
  showScreen("note-new");
}

bindSubjectPicks(document.querySelector("#new-note-subject"));
bindSubjectPicks(document.querySelector("#note-subject-picks"));
document.querySelector("#notes-new").addEventListener("click", openNewNote);
document.querySelector("#notes-filters").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-notes-filter]");
  if (!chip) return;
  state.notesFilter = chip.dataset.notesFilter || "all";
  paintNotes();
});
document.querySelector("#error-list").addEventListener("click", (event) => {
  if (event.target.closest("[data-train]")) {
    startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу."));
    return;
  }
  const card = event.target.closest("[data-error]");
  if (card) toggleErrorCard(card);
});
document.querySelector("#error-list").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest("[data-error]");
  if (!card || event.target !== card) return;
  event.preventDefault();
  toggleErrorCard(card);
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
      body: JSON.stringify({
        text,
        title: document.querySelector("#new-note-title").value.trim(),
        subject: document.querySelector("#new-note-subject").dataset.value || ""
      })
    });
    const data = await response.json().catch(() => ({}));
    if (locked(response)) return;
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
    if (state.notesFilter && state.notesFilter !== "all" && state.notesFilter !== "s:" + data.subject) {
      state.notesFilter = "s:" + data.subject;
    }
    paintNotes();
    paintTopics();
    refreshHome().catch(() => {});
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
const SEARCH_EMPTY = "Ничего не нашлось. Попробуй другое слово из конспекта.";
const SEARCH_FAILED = "Поиск сейчас не работает. Попробуй чуть позже.";
let searchTimer = null;

function setSearching(on) {
  document.querySelector("#notes-list").hidden = on;
  document.querySelector("#notes-filters").hidden = on || state.notes.length === 0;
  document.querySelector("#search-hits").hidden = !on;
}

function searchHead(text, semantic) {
  const badge = semantic ? `<span class="search-badge">по смыслу</span>` : "";
  return `<div class="search-head"><span>${escapeHtml(text)}</span>${badge}</div>`;
}

function paintSearchHits(hits, query) {
  const box = document.querySelector("#search-hits");
  if (!hits.length) {
    box.innerHTML = searchHead("Поиск: «" + query + "»", false) + `<p class="empty">${escapeHtml(SEARCH_EMPTY)}</p>`;
    return;
  }
  const count = hits.length + " " + ruCount(hits.length, "конспект", "конспекта", "конспектов");
  box.innerHTML = searchHead("Нашлось " + count, hits.some((hit) => hit.semantic)) + hits.map((hit) => `
    <button type="button" class="card" data-note="${escapeHtml(hit.id)}">
      <span class="note-mark" aria-hidden="true">${escapeHtml(subjectMark(hit.subject))}</span>
      <span class="copy">
        <h3>${escapeHtml(hit.title || "Конспект")}</h3>
        <span class="meta">${escapeHtml(noteSubject(hit))}</span>
        <span class="preview hit">${highlight(hit.snippet || "", query)}</span>
      </span>
      ${CHEV}
    </button>
  `).join("");
}

async function runSearch(query, ticket) {
  const box = document.querySelector("#search-hits");
  const response = await fetch("/api/search?q=" + encodeURIComponent(query));
  if (ticket !== searchTicket) return;
  if (locked(response)) return;
  const data = await response.json().catch(() => null);
  if (ticket !== searchTicket) return;
  if (!response.ok) {
    box.innerHTML = searchHead("Поиск: «" + query + "»", false)
      + `<p class="empty">${escapeHtml(detailText(data, SEARCH_FAILED))}</p>`;
    return;
  }
  paintSearchHits(Array.isArray(data) ? data : [], query);
}

document.querySelector("#search-input").addEventListener("input", () => {
  const ticket = ++searchTicket;
  const query = document.querySelector("#search-input").value.trim();
  clearTimeout(searchTimer);
  if (!query) {
    document.querySelector("#search-hits").innerHTML = "";
    setSearching(false);
    return;
  }
  setSearching(true);
  document.querySelector("#search-hits").innerHTML = searchHead("Ищу…", false);
  searchTimer = setTimeout(() => {
    runSearch(query, ticket).catch(() => {
      if (ticket !== searchTicket) return;
      document.querySelector("#search-hits").innerHTML = `<p class="empty">${escapeHtml(SEARCH_FAILED)}</p>`;
    });
  }, 300);
});
document.querySelector("#topic-list").addEventListener("click", (event) => {
  const card = event.target.closest("[data-select]");
  if (!card) return;
  state.selectedNoteId = card.dataset.select;
  paintTopics();
});
document.querySelector("#start-topic").addEventListener("click", () => startQuiz("topic").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#start-errors").addEventListener("click", () => startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу.")));
document.querySelector("#open-graph").addEventListener("click", () => openSkillsGraph("quiz-errors"));
document.querySelector("#graph-back").addEventListener("click", () => showScreen(state.graphReturn || "quiz-errors"));
document.querySelector("#graph-empty-back").addEventListener("click", () => showScreen(state.graphReturn || "quiz-errors"));
document.querySelector("#graph-due").addEventListener("click", () => {
  startGraphQuiz("due").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#graph-sheet-go").addEventListener("click", () => {
  if (state.graphSkillId == null) return;
  startGraphQuiz("skill", state.graphSkillId).catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#export-calendar").addEventListener("click", () => downloadCalendar().catch(() => showGate("Кабинет не смог открыть базу.")));
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
  else if (quiz.kind === "skill") startGraphQuiz("skill", quiz.skillId).catch(() => showGate("Кабинет не смог открыть базу."));
  else if (quiz.kind === "due") startGraphQuiz("due").catch(() => showGate("Кабинет не смог открыть базу."));
  else if (quiz.kind === "topic") startQuiz("topic").catch(() => showGate("Кабинет не смог открыть базу."));
  else startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#quiz-cancel").addEventListener("click", cancelQuizLoading);
document.querySelector("#quiz-retry").addEventListener("click", () => {
  if (quiz && (quiz.kind === "skill" || quiz.kind === "due")) {
    showScreen("skills-graph");
    return;
  }
  startQuiz("errors").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#quiz-chat").addEventListener("click", () => {
  backToChat("Открываю чат — спроси Люми про трудный вопрос.");
});
nameInput.addEventListener("input", () => {
  previewFromForm();
  markProfileDirty();
});
remindersInput.addEventListener("change", markProfileDirty);
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
document.querySelector("#style-row").addEventListener("click", (event) => {
  const button = event.target.closest("[data-style]");
  if (!button) return;
  setStyle(button.dataset.style, true);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConfirm();
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
  document.querySelector("#note-title-input").value = currentNote.title || "";
  paintSubjectPicks(document.querySelector("#note-subject-picks"), noteSubject(currentNote), noteSubject(currentNote));
  document.querySelector("#note-body-input").value = currentNote.body || "";
  document.querySelector("#note-status").textContent = "";
  document.querySelector("#note-status").className = "status";
  document.querySelector("#note-body").hidden = true;
  document.querySelector("#edit-note").hidden = true;
  document.querySelector("#note-actions").hidden = true;
  document.querySelector("#note-form").hidden = false;
  document.querySelector("#note-body-input").focus();
}

function cancelNoteEdit() {
  document.querySelector("#note-form").hidden = true;
  document.querySelector("#note-body").hidden = false;
  document.querySelector("#edit-note").hidden = false;
  document.querySelector("#note-actions").hidden = false;
  document.querySelector("#note-status").textContent = "";
}

document.querySelector("#note-quiz").addEventListener("click", () => {
  if (!currentNote) return;
  state.selectedNoteId = currentNote.id;
  state.quizReturn = "notes";
  startQuiz("topic").catch(() => showGate("Кабинет не смог открыть базу."));
});
document.querySelector("#note-pin").addEventListener("click", () => {
  if (currentNote) pinNote(currentNote.id).catch(() => {});
});
document.querySelector("#note-delete").addEventListener("click", () => {
  if (currentNote) askDelete(currentNote.id);
});

function clearStatusOnInput(inputSelector, statusSelector) {
  document.querySelector(inputSelector).addEventListener("input", () => {
    const status = document.querySelector(statusSelector);
    if (!status.classList.contains("error")) return;
    status.textContent = "";
    status.className = "status";
  });
}

clearStatusOnInput("#new-note-input", "#new-note-status");
clearStatusOnInput("#note-body-input", "#note-status");
clearStatusOnInput("#note-title-input", "#note-status");
clearStatusOnInput("#name-input", "#save-status");
document.querySelector("#edit-note").addEventListener("click", beginNoteEdit);
document.querySelector("#cancel-note").addEventListener("click", cancelNoteEdit);
document.querySelector("#note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentNote) return;
  const status = document.querySelector("#note-status");
  const payload = {
    title: document.querySelector("#note-title-input").value.trim(),
    subject: document.querySelector("#note-subject-picks").dataset.value || "",
    body: document.querySelector("#note-body-input").value.trim()
  };
  if (!payload.title) {
    status.className = "status error";
    status.textContent = "Напиши заголовок.";
    return;
  }
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
  if (locked(response)) return;
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
  paintNoteHead(data);
  document.querySelector("#note-body").innerHTML = renderNoteBody(data.body || "");
  document.querySelector("#note-form").hidden = true;
  document.querySelector("#note-body").hidden = false;
  document.querySelector("#edit-note").hidden = false;
  document.querySelector("#note-actions").hidden = false;
});
document.querySelector("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = nameInput.value.trim();
  const grade = Number(gradeInput.value);
  const style = styleInput.value === "detail" ? "detail" : "short";
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
      explain_style: style,
      exam_goal: goal,
      reminders: remindersInput.checked
    };
    const response = await fetch("/api/profile", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (locked(response)) return;
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
    savedProfile = profileSnapshot();
    saveButton.textContent = "Сохранено";
  } finally {
    saveButton.disabled = profileSnapshot() === savedProfile;
  }
});

const TOUR_STEPS = [
  {
    screen: "home",
    anchor: ".quick-grid",
    text: "С этих кнопок можно сразу начать: квиз по теме, квиз по ошибкам, новый конспект или вопрос Люми в чате."
  },
  {
    screen: "home",
    anchor: "#tabs",
    text: "Внизу пять разделов. Сейчас ты на главной, дальше покажу остальные."
  },
  {
    screen: "notes",
    anchor: ".notes-head, label.search",
    text: "Здесь твои конспекты. Можно искать по тексту или добавить новый."
  },
  {
    screen: "quiz",
    anchor: "#subject-grid",
    text: "Выбери предмет и тему. Люми соберёт квиз по программе класса."
  },
  {
    screen: "quiz-errors",
    anchor: ".bank-head",
    text: "Здесь ошибки из проверок. «Тренировка» закрепляет их, а «Карта» собирает приёмы в схему."
  },
  {
    screen: "skills-graph",
    anchor: "#graph-canvas",
    text: "Это карта приёмов. Точка — один приём, линия — что с ним связано. Нажми точку, чтобы повторить, или «Повторить сегодня», если тему пора вернуть."
  },
  {
    screen: "profile",
    anchor: "#grade-row",
    text: "Люми подстраивается под тебя: по классу и цели подбирает задачи, по стилю — объясняет короче или подробнее. Напоминания про ошибки можно выключить."
  }
];
let tourIndex = -1;

function startTour() {
  tourIndex = 0;
  showTourStep();
}

function clearTourShift() {
  const main = document.querySelector("main");
  if (main) main.style.paddingTop = "";
}

function hideTourSystemBack() {
  const app = bridge();
  if (app && app.BackButton && app.BackButton.hide) app.BackButton.hide();
}

function showTourStep() {
  const index = tourIndex;
  const step = TOUR_STEPS[index];
  if (!step) return;
  clearTourShift();
  document.querySelector("#tour-step").textContent = (index + 1) + " из " + TOUR_STEPS.length;
  document.querySelector("#tour-text").textContent = step.text;
  document.querySelector("#tour-next").textContent = index === TOUR_STEPS.length - 1 ? "Понятно" : "Дальше";
  document.querySelector("#tour-back").hidden = index === 0;
  if (step.screen === "skills-graph") {
    if (state.screen !== "skills-graph") openSkillsGraph("quiz-errors");
  } else if (state.screen !== step.screen) {
    showScreen(step.screen);
  }
  hideTourSystemBack();
  const first = document.querySelector(step.anchor.split(",")[0].trim());
  if (first && step.anchor !== "#tabs" && step.screen !== "skills-graph") {
    first.scrollIntoView({ block: "nearest" });
  }
  requestAnimationFrame(() => {
    if (tourIndex !== index) return;
    const root = document.querySelector("#tour");
    root.hidden = false;
    document.body.classList.add("tour-open");
    placeTourHole(step);
  });
}

function anchorBox(nodes) {
  let top = Infinity;
  let left = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  nodes.forEach((node) => {
    const rect = node.getBoundingClientRect();
    if (!rect.width && !rect.height) return;
    top = Math.min(top, rect.top);
    left = Math.min(left, rect.left);
    right = Math.max(right, rect.right);
    bottom = Math.max(bottom, rect.bottom);
  });
  return { top, left, right, bottom };
}

function placeTourHole(step) {
  const nodes = Array.from(document.querySelectorAll(step.anchor));
  const hole = document.querySelector("#tour-hole");
  const card = document.querySelector("#tour-card");
  const pad = 8;
  const gap = 12;
  const inset = 16;
  const tabsShown = getComputedStyle(tabs).display !== "none" && tabs.getBoundingClientRect().height > 0;
  const floor = tabsShown ? tabs.getBoundingClientRect().top : window.innerHeight - 12;
  let cardTop = floor - gap - card.offsetHeight;
  if (step.screen === "skills-graph") {
    hole.hidden = true;
    card.style.top = Math.max(12, cardTop) + "px";
    card.style.bottom = "auto";
    return;
  }
  clearTourShift();
  let box = anchorBox(nodes);
  if (box.top !== Infinity && box.top - pad < inset) {
    const main = document.querySelector("main");
    const base = parseFloat(getComputedStyle(main).paddingTop) || 0;
    main.style.paddingTop = (base + (inset - (box.top - pad))) + "px";
    box = anchorBox(nodes);
  }
  if (box.top !== Infinity) {
    const holeTop = Math.max(inset, box.top - pad);
    const holeLeft = Math.max(8, box.left - pad);
    const holeRight = Math.min(window.innerWidth - 8, box.right + pad);
    const holeBottom = box.bottom + pad;
    hole.hidden = false;
    hole.style.top = holeTop + "px";
    hole.style.left = holeLeft + "px";
    hole.style.width = Math.max(24, holeRight - holeLeft) + "px";
    hole.style.height = Math.max(24, holeBottom - holeTop) + "px";
    const overlaps = cardTop + card.offsetHeight > holeTop && cardTop < holeBottom;
    if (overlaps) {
      const above = holeTop - gap - card.offsetHeight;
      cardTop = above >= inset ? above : Math.min(holeBottom + gap, window.innerHeight - card.offsetHeight - 12);
    }
  } else {
    hole.hidden = true;
  }
  card.style.top = Math.max(12, cardTop) + "px";
  card.style.bottom = "auto";
}

function finishTour() {
  if (tourIndex < 0) return;
  tourIndex = -1;
  clearTourShift();
  document.querySelector("#tour").hidden = true;
  document.body.classList.remove("tour-open");
  if (state.screen !== "home") showScreen("home");
  if (isDemo()) rememberDemoTour();
  fetch("/api/tour", { method: "POST" })
    .then((response) => {
      if (response.ok) state.profile.cabinet_tour = true;
    })
    .catch(() => {});
}

document.querySelector("#tour-skip").addEventListener("click", finishTour);
document.querySelector("#tour-next").addEventListener("click", () => {
  if (tourIndex < 0) return;
  if (tourIndex >= TOUR_STEPS.length - 1) finishTour();
  else {
    tourIndex += 1;
    showTourStep();
  }
});
document.querySelector("#tour-back").addEventListener("click", () => {
  if (tourIndex <= 0) return;
  tourIndex -= 1;
  showTourStep();
});
document.querySelector("#tour-replay").addEventListener("click", startTour);
window.addEventListener("resize", () => {
  if (tourIndex < 0) return;
  placeTourHole(TOUR_STEPS[tourIndex]);
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

function isDemo() {
  return Boolean(document.querySelector('meta[name="lumi-demo"]'));
}

function demoTourSeen() {
  try {
    return localStorage.getItem(DEMO_TOUR_KEY) === "1";
  } catch (error) {
    return false;
  }
}

function rememberDemoTour() {
  try {
    localStorage.setItem(DEMO_TOUR_KEY, "1");
  } catch (error) {}
}

async function enter() {
  if (await ensureFreshBuild()) return;
  const webApp = bridge();
  const initData = webApp && typeof webApp.initData === "string" ? webApp.initData : "";
  if (webApp && typeof webApp.ready === "function") webApp.ready();
  if (webApp && typeof webApp.setHeaderColor === "function") webApp.setHeaderColor("#f7f6fb");
  if (webApp && typeof webApp.setBackgroundColor === "function") webApp.setBackgroundColor("#f7f6fb");
  if (isDemo()) {
    const response = await fetch("/api/demo/session", { method: "POST" });
    if (!response.ok) return showGate("Демо-кабинет сейчас недоступен.");
  } else if (initData) {
    const response = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: initData })
    });
    if (response.status === 401) {
      showGate("Не удалось подтвердить вход. Закрой кабинет и открой его кнопкой в чате.");
      return;
    }
    if (response.status === 403) return showGate(DENIED);
    if (!response.ok) return showGate("Кабинет не смог открыть базу.");
  }
  await loadCabinet();
}

enter().catch(() => showGate("Кабинет не смог открыть базу."));
