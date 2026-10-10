// Seleção de trecho da montagem (F4, passos 3 e 5): marcas de início e fim e o trecho que elas formam.
//
// As marcas vêm de três gestos: Shift+arraste na timeline, as teclas I e O no cursor e Alt+clique num bloco
// (intervalo exato do bloco). Uma marca sozinha (só I ou só O) fica pela metade; o trecho só existe com as
// duas e com ao menos um quadro de duração. O mapeamento do trecho para segmentos, camadas e legendas é do
// servidor (`GET /api/selection`), lido pelo painel quando o gesto termina (`commit`).

import { duration, emit, fps, state } from "./store.js";

/** Menor trecho válido: um quadro (`end - start` com a folga do arredondamento a 6 casas). */
function minLength() {
  return 1 / fps() - 1e-6;
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

/** Quadro mais próximo de `seconds` (fronteiras de quadro, pois a seleção é um intervalo e não um quadro). */
function nearestFrame(seconds) {
  return Math.round(seconds * fps()) / fps();
}

/** Recalcula o trecho a partir das marcas e avisa os ouvintes (`commit`: o gesto terminou). */
function publish(commit) {
  const { start, end } = state.marks;
  const complete = start !== null && end !== null && end - start >= minLength();
  state.selection = complete ? { start, end } : null;
  emit("selection", { commit });
}

/**
 * Define o trecho entre `a` e `b` (em qualquer ordem). `exact` mantém os tempos como vieram (Alt+clique num
 * bloco); sem ele as pontas são coladas na fronteira de quadro mais próxima. Um gesto terminado (`commit`)
 * com menos de um quadro descarta a seleção.
 */
export function selectRange(a, b, { commit = true, exact = false } = {}) {
  const limit = duration();
  const low = clamp(Math.min(a, b), 0, limit);
  const high = clamp(Math.max(a, b), 0, limit);
  state.marks = exact ? { start: low, end: high } : { start: nearestFrame(low), end: nearestFrame(high) };
  if (commit && (state.marks.end - state.marks.start < minLength())) state.marks = { start: null, end: null };
  publish(commit);
}

/** Tecla I: marca o início no cursor; um fim que ficou antes dele é descartado. */
export function markIn(cursor) {
  const end = state.marks.end !== null && state.marks.end - cursor >= minLength() ? state.marks.end : null;
  state.marks = { start: cursor, end };
  publish(true);
}

/** Tecla O: marca o fim no cursor; um início que ficou depois dele é descartado. */
export function markOut(cursor) {
  const start = state.marks.start !== null && cursor - state.marks.start >= minLength() ? state.marks.start : null;
  state.marks = { start, end: cursor };
  publish(true);
}

/** Arrasto de uma alça da régua: move só a ponta escolhida, sem cruzar a outra (mantém ao menos um quadro). */
export function moveMark(which, seconds, { commit = true } = {}) {
  const snapped = clamp(nearestFrame(seconds), 0, duration());
  const { start, end } = state.marks;
  state.marks =
    which === "start"
      ? { start: end === null ? snapped : Math.min(snapped, end - 1 / fps()), end }
      : { start, end: start === null ? snapped : Math.max(snapped, start + 1 / fps()) };
  publish(commit);
}

/** Esc (ou plano novo): limpa as marcas. */
export function clearSelection({ commit = true } = {}) {
  if (state.marks.start === null && state.marks.end === null) return;
  state.marks = { start: null, end: null };
  publish(commit);
}
