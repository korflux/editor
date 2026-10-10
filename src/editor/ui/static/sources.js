// Modo Fonte (F6): alternância Montagem/Fonte, lista de fontes e a ida e volta entre fonte e montagem.
//
// O modo Fonte mostra cada fonte de vídeo ou áudio do plano inteira, fora da montagem, com a barra de uso (o que a
// montagem aproveita e o que descarta, desenhada por `timeline.js` a partir de `source.uses`). Este módulo cuida
// do que é só do modo:
//   - a alternância no topo e a lista de fontes (vira `<select>` abaixo de 1280px, por CSS);
//   - `openSource` ("Ver fonte" no painel de um segmento): abre a fonte no `in_s` do segmento, com o intervalo dele
//     destacado como seleção;
//   - `goToMontage` ("Ir para a montagem" e Alt+clique num trecho usado): volta à Montagem no tempo correspondente.
// O mapeamento fonte para montagem (`montageTime`) repete a regra de `ui/usage.py`: linear, e a trilha em `loop` aponta
// o começo da janela de áudio dela. Quem decide o que é uso é o servidor; aqui só se lê `source.uses`.

import { enterContext, on, sourceById, sourceList, state } from "./store.js";
import * as selection from "./selection.js";
import { toast } from "./status.js";
import { snap, snapBoundary, timecode } from "./timecode.js";

const KIND_LABELS = { video: "vídeo", audio: "áudio" };
const USE_PRIORITY = { segment: 0, layer: 1, audio: 2 }; // qual uso leva o Alt+clique quando vários cobrem o ponto

const dom = {
  modeInputs: document.querySelectorAll('#mode-group input[name="mode"]'),
  list: document.getElementById("source-list"),
  empty: document.getElementById("source-list-empty"),
  select: document.getElementById("source-select"),
};

// --- Mapeamento entre a fonte e a montagem ---

/** Usos da fonte que cobrem o instante `seconds` da fonte, na ordem que o servidor entregou. */
export function usesAt(source, seconds) {
  const slack = 1e-6;
  return source.uses.filter((use) => seconds >= use.source_in_s - slack && seconds <= use.source_out_s + slack);
}

/** Instante da montagem em que o instante `seconds` da fonte aparece neste uso (o começo da janela, se for `loop`). */
export function montageTime(use, seconds) {
  if (use.loop) return use.start_s;
  return Math.min(use.end_s, Math.max(use.start_s, use.start_s + (seconds - use.source_in_s)));
}

// --- Troca de contexto ---

/** Alterna entre Montagem e Fonte; o modo Fonte só existe com um plano carregado. */
export function setMode(mode) {
  if (mode === state.mode || (mode === "source" && state.plan === null)) {
    renderModeGroup();
    return;
  }
  enterContext(mode);
}

/** Abre uma fonte no modo Fonte, no começo dela e sem seleção. */
export function chooseSource(sourceId) {
  enterContext("source", sourceById(sourceId) === null ? null : sourceId);
}

/**
 * "Ver fonte": abre a fonte `sourceId` em `at` (o `in_s` do segmento) e, com `range`, destaca esse intervalo da
 * fonte como seleção (o painel passa a mostrar a situação e onde ele aparece na montagem).
 */
export function openSource(sourceId, { at = 0, range = null } = {}) {
  const source = sourceById(sourceId);
  if (source === null) return;
  enterContext("source", sourceId, snapBoundary(at, source.frame_rate, source.duration_s));
  if (range !== null) selection.selectRange(range.start, range.end, { exact: true });
}

/** "Ir para a montagem": volta ao modo Montagem com o cursor em `time` (segundos da montagem). */
export function goToMontage(time) {
  const plan = state.plan;
  if (plan === null) return;
  enterContext("assembly", state.sourceId, snap(time, plan.frame_rate, plan.duration_s));
}

/** Alt+clique na barra de uso: leva à montagem no ponto que corresponde ao instante clicado da fonte, se ele é usado. */
export function goToUsedPart(sourceSeconds) {
  const source = sourceById(state.sourceId);
  if (source === null) return;
  const hits = usesAt(source, sourceSeconds).sort((a, b) => USE_PRIORITY[a.kind] - USE_PRIORITY[b.kind]);
  if (hits.length === 0) {
    toast("Este trecho da fonte não é usado na montagem.");
    return;
  }
  goToMontage(montageTime(hits[0], sourceSeconds));
}

// --- Alternância no topo e lista de fontes ---

function renderModeGroup() {
  for (const input of dom.modeInputs) {
    input.checked = input.value === state.mode;
    if (input.value === "source") input.disabled = state.plan === null;
  }
}

let listSignature = "";

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Linha da lista: ícone, `source_id`, tipo, duração e quantas vezes a fonte é usada, ou "Indisponível". */
function sourceRow(source) {
  const available = source.available !== false;
  const button = element("button", "source-row");
  button.type = "button";
  button.dataset.source = source.id;
  if (!available) {
    button.classList.add("is-unavailable");
    button.title = `${source.error.code}: ${source.error.message}`;
  }
  const head = element("span", "source-head");
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("class", "icon icon-small");
  icon.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", source.kind === "video" ? "#i-video" : "#i-audio");
  icon.append(use);
  head.append(icon, element("span", "source-name", source.id));
  const usage = source.used_count === 0 ? "não usada" : `usada ${source.used_count}×`;
  const detail = [KIND_LABELS[source.kind], timecode(source.duration_s, source.frame_rate), available ? usage : "Indisponível"];
  button.append(head, element("span", "source-detail", detail.join(" · ")));
  button.addEventListener("click", () => chooseSource(source.id));
  return button;
}

/** `<select>` das telas estreitas: as mesmas fontes (a escolhida é marcada por `markCurrent`). */
function sourceOption(source) {
  const option = element("option", "", `${source.id} (${KIND_LABELS[source.kind]})${source.available === false ? ", indisponível" : ""}`);
  option.value = source.id;
  return option;
}

/**
 * Refaz a lista e o seletor só quando algo que eles mostram mudou. Escolher outra fonte não os refaz (só move a
 * marca, `markCurrent`): refazer trocaria o botão clicado por outro e o foco, e com ele a tecla Espaço de
 * play/pausa, ficariam com o botão novo; a consulta de estado a cada segundo também não pode roubar o foco.
 */
function renderSources() {
  const sources = sourceList();
  const signature = JSON.stringify(sources.map((s) => [s.id, s.kind, s.duration_s, s.used_count, s.available, s.frame_rate]));
  if (signature === listSignature) return;
  listSignature = signature;
  const focused = document.activeElement?.closest?.(".source-row")?.dataset.source ?? null;
  dom.list.replaceChildren(...sources.map((source) => {
    const item = element("li", "source-item");
    item.append(sourceRow(source));
    return item;
  }));
  dom.empty.hidden = sources.length > 0;
  const placeholder = element("option", "", "Escolha uma fonte…");
  placeholder.value = "";
  dom.select.replaceChildren(placeholder, ...sources.map(sourceOption));
  if (focused !== null) dom.list.querySelector(`[data-source="${CSS.escape(focused)}"]`)?.focus();
}

/** Marca a fonte aberta na lista (`aria-current`) e no seletor, sem refazer nenhum dos dois. */
function markCurrent() {
  const current = sourceById(state.sourceId)?.id ?? null;
  for (const row of dom.list.querySelectorAll(".source-row")) {
    if (row.dataset.source === current) row.setAttribute("aria-current", "true");
    else row.removeAttribute("aria-current");
  }
  dom.select.value = current ?? "";
}

for (const input of dom.modeInputs) {
  input.addEventListener("change", () => {
    if (input.checked) setMode(input.value);
  });
}
dom.select.addEventListener("change", () => chooseSource(dom.select.value));

function render() {
  renderModeGroup();
  renderSources();
  markCurrent();
}

on("plan", render);
on("status", render);
on("context", render);
render();
