// Estado compartilhado da interface e barramento de eventos entre os módulos.
//
// Cada módulo (player, timeline, seleção, painel, recarga) lê e altera `state` e se avisa por eventos, de
// modo que nenhum importe o outro em círculo. Eventos usados: `plan` (plano novo carregado), `status`
// (estado do servidor mudou), `workdir` (nome do trabalho conhecido pela primeira vez), `cursor`, `play`, `res`,
// `selection` (marcas mudaram; `commit` indica o fim do gesto) e `context` (mudou o modo ou a fonte aberta).
// Também guarda as preferências por trabalho no navegador (`localStorage`, sempre com try/catch).
//
// Dois contextos de revisão (F3 e F6): o modo Montagem (tempo da montagem, taxa de saída) e o modo Fonte (tempo da
// fonte aberta, taxa dela). Cada um tem a sua visão (cursor, marcas e trecho), guardada em `views`, e
// `state.cursor`, `state.marks` e `state.selection` leem e gravam sempre a do modo que está na tela, então os
// módulos que só conhecem "o cursor" e "a seleção" funcionam nos dois modos sem saber qual deles é. `duration()` e
// `fps()` seguem o mesmo princípio.

import { snap } from "./timecode.js";

const EMPTY_MARKS = () => ({ start: null, end: null });
const freshView = () => ({ cursor: 0, marks: EMPTY_MARKS(), selection: null });

const views = { assembly: freshView(), source: freshView() };

export const state = {
  workdirName: "",
  status: "loading", // loading | ready | outdated | missing | invalid (os rótulos de `GET /api/state`)
  error: null, // { code, message } do plano atual, quando inválido
  connectionLost: false,
  resolutions: [],
  plan: null, // última versão válida do plano (visão pública de `ui/state.py::build_view`)
  mode: "assembly", // "assembly" (Montagem) ou "source" (Fonte)
  sourceId: null, // fonte aberta no modo Fonte (null: nenhuma escolhida)
  playing: false, // intenção de reprodução (inclui o carregamento do primeiro quadro)
  res: "full",
  panelOpen: true,
  // Visão do modo atual: início do quadro sob o cursor (segundos do modo), marcas de seleção (podem estar
  // pela metade, só I ou só O) e o trecho que elas formam ({ start, end } com ao menos um quadro, ou null).
  get cursor() {
    return views[this.mode].cursor;
  },
  set cursor(value) {
    views[this.mode].cursor = value;
  },
  get marks() {
    return views[this.mode].marks;
  },
  set marks(value) {
    views[this.mode].marks = value;
  },
  get selection() {
    return views[this.mode].selection;
  },
  set selection(value) {
    views[this.mode].selection = value;
  },
};

const listeners = new Map();

/** Registra `handler` para o evento `name`. */
export function on(name, handler) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(handler);
}

/** Dispara o evento `name`; um handler com erro não impede os demais. */
export function emit(name, detail) {
  for (const handler of listeners.get(name) ?? []) {
    try {
      handler(detail);
    } catch (error) {
      console.error(error);
    }
  }
}

// --- Contexto: montagem ou fonte aberta ---

/** Fontes de vídeo e áudio do plano (a imagem estática não tem tempo nem som e fica fora do modo Fonte). */
export function sourceList() {
  return (state.plan?.sources ?? []).filter((source) => source.kind !== "image");
}

/** A fonte com este id no plano atual, ou `null`. */
export function sourceById(id) {
  return id === null ? null : (sourceList().find((source) => source.id === id) ?? null);
}

/** Fonte aberta no modo Fonte, ou `null` na Montagem ou sem fonte escolhida. */
export function activeSource() {
  return state.mode === "source" ? sourceById(state.sourceId) : null;
}

/** Há o que mostrar no modo atual: o plano na Montagem, uma fonte escolhida no modo Fonte. */
export function hasContext() {
  return state.mode === "source" ? activeSource() !== null : state.plan !== null;
}

/** Taxa de quadros do contexto atual (25 sem plano ou fonte, para o timecode não quebrar). */
export function fps() {
  return (state.mode === "source" ? activeSource()?.frame_rate : state.plan?.frame_rate) ?? 25;
}

/** Duração do contexto atual, em segundos (0 sem plano ou fonte). */
export function duration() {
  return (state.mode === "source" ? activeSource()?.duration_s : state.plan?.duration_s) ?? 0;
}

/**
 * Instantes de salto de corte do contexto: o início de cada segmento na Montagem e, no modo Fonte, o início e o
 * fim de cada trecho usado (as bordas entre o que a montagem aproveita e o que descarta).
 */
export function cutPoints() {
  if (state.mode === "assembly") return state.plan ? state.plan.segments.map((segment) => segment.start_s) : [];
  const source = activeSource();
  if (source === null) return [];
  const limit = source.duration_s - 1 / fps();
  const points = new Set();
  for (const use of source.uses) {
    points.add(use.source_in_s);
    points.add(use.source_out_s);
  }
  return [...points].filter((point) => point < limit).sort((a, b) => a - b);
}

/**
 * Troca o contexto exibido e avisa (`context`). Abrir outra fonte começa a visão dela do zero (cursor 0, sem
 * seleção); voltar à mesma fonte ou alternar o modo mantém a visão de cada um. `cursor` posiciona o modo que
 * passa a estar na tela.
 */
export function enterContext(mode, sourceId = state.sourceId, cursor = null) {
  if (sourceId !== state.sourceId) views.source = freshView();
  state.mode = mode;
  state.sourceId = sourceId;
  if (cursor !== null) state.cursor = cursor;
  document.documentElement.dataset.mode = mode;
  emit("context");
}

/**
 * Concilia as duas visões com um plano recém-carregado: cada cursor é limitado ao tempo novo do seu contexto, a
 * fonte que saiu do plano é fechada e todas as seleções são descartadas (não há reassociação automática).
 */
export function reconcileViews() {
  const plan = state.plan;
  views.assembly.cursor = snap(views.assembly.cursor, plan.frame_rate, plan.duration_s);
  const source = sourceById(state.sourceId);
  if (source === null) {
    state.sourceId = null;
    views.source.cursor = 0;
  } else {
    views.source.cursor = snap(views.source.cursor, source.frame_rate, source.duration_s);
  }
  for (const view of Object.values(views)) {
    view.marks = EMPTY_MARKS();
    view.selection = null;
  }
}

// --- Preferências por trabalho (nada sensível: resolução, volume e mudo) ---

function storageKey(name) {
  return `editor-ui:${state.workdirName}:${name}`;
}

/** Lê uma preferência; devolve `null` sem armazenamento (janela privada, bloqueio) ou sem valor. */
export function readPref(name) {
  try {
    return window.localStorage.getItem(storageKey(name));
  } catch {
    return null;
  }
}

/** Grava uma preferência; a falha de armazenamento é ignorada (a tela funciona sem ele). */
export function writePref(name, value) {
  try {
    window.localStorage.setItem(storageKey(name), String(value));
  } catch {
    // Sem armazenamento a preferência só vale para esta sessão da página.
  }
}
