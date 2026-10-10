// Player e transporte da montagem (F3 e parte de F4): stream ao vivo, quadro de scrub e controles.
//
// Dois modos de mostrar a imagem, sobrepostos na mesma área:
// - `<video>`: o MP4 fragmentado de `GET /api/stream`, que o servidor produz do instante pedido em diante
//   (um stream por sessão; um pedido novo mata o anterior). Cursor = início do stream + tempo do vídeo;
// - `<img>` (overlay): o quadro composto de `GET /api/frame` (scrub, saltos e qualquer instante fora do
//   que já foi transmitido). Um pedido em voo por vez e só o último pendente é atendido.
//
// Regras do stream: `<video>` não lê cabeçalhos, então o instante inicial é calculado aqui com a mesma
// grade do servidor (`timecode.snap`) e o tempo do vídeo conta a partir dele (o MP4 do stream nasce em 0).
// Pausa de mais de 20 s não retoma (o servidor encerra o stream parado em 30 s): o play reinicia em T. Fim
// prematuro do stream (queda, encerramento do servidor) reinicia sozinho no ponto atual, até duas vezes.
//
// O stream não aceita `Range`, então o navegador não busca dentro dele (`seekable` fica vazio e uma busca
// reabre o stream do início): só se retoma de onde parou, sem mover o tempo do vídeo. Quadro a quadro, salto
// e scrub mostram o quadro composto (mesmo grafo) e o play seguinte reinicia o stream no novo cursor.
//
// ponytail: quadro a quadro custa uma extração de quadro (0,15 a 0,7 s medidos), adicionar `Range` ao
// servidor para buscar dentro do que já foi transmitido se o passo a passo precisar ser instantâneo.

import * as api from "./api.js";
import { duration, emit, fps, on, readPref, state, writePref } from "./store.js";
import {
  frameIndex,
  lastFrameIndex,
  snap,
  snapBoundary,
  timecode,
} from "./timecode.js";

const RESUME_MAX_PAUSE_MS = 20_000; // abaixo do corte de 30 s do servidor (STREAM_STALL_TIMEOUT_S)
const MAX_AUTO_RETRIES = 2;
const REVEAL_FALLBACK_MS = 250; // com a página oculta não há quadros apresentados: `playing` basta depois deste prazo

const dom = {
  player: document.getElementById("player"),
  video: document.getElementById("video"),
  scrub: document.getElementById("scrub"),
  proxyBadge: document.getElementById("proxy-badge"),
  spinner: document.getElementById("spinner"),
  error: document.getElementById("player-error"),
  errorText: document.getElementById("player-error-text"),
  retry: document.getElementById("player-retry"),
  empty: document.getElementById("player-empty"),
  tcCurrent: document.getElementById("tc-current"),
  tcTotal: document.getElementById("tc-total"),
  btnPlay: document.getElementById("btn-play"),
  playIcon: document.getElementById("play-icon"),
  btnFramePrev: document.getElementById("btn-frame-prev"),
  btnFrameNext: document.getElementById("btn-frame-next"),
  btnCutPrev: document.getElementById("btn-cut-prev"),
  btnCutNext: document.getElementById("btn-cut-next"),
  btnPlaySelection: document.getElementById("btn-play-selection"),
  btnVolume: document.getElementById("btn-volume"),
  volumeIcon: document.getElementById("volume-icon"),
  volume: document.getElementById("volume"),
};
const video = dom.video;

// --- Estado interno do stream e do quadro de scrub ---

let streamStart = null; // instante da montagem do primeiro quadro do stream carregado (null: sem stream aproveitável)
let generation = 0; // eventos e promessas de um stream antigo são ignorados quando este número muda
let streamLoading = false; // aguardando o primeiro quadro do stream
let buffering = false; // o vídeo parou por falta de dados com o stream já rodando
let pausedAt = 0;
let pausedFrame = null; // quadro em que a última pausa parou o `<video>` (só dele se retoma sem novo stream)
let stopAt = null; // fim da "Reproduzir seleção" (null: toca até o fim da montagem)
let retries = 0;
let raf = 0;

let frameBusy = false;
let framePending = null; // último instante pedido enquanto um quadro está em voo
let frameAbort = null;
let frameUrl = null;
let errorKind = null; // "playback" ou "frame"

// --- Indicadores: carregamento, erro, selo de proxy, mensagem de estado vazio ---

function refreshSpinner() {
  dom.spinner.hidden = !(streamLoading || buffering || frameBusy);
}

function showError(kind, message) {
  errorKind = kind;
  dom.errorText.textContent = message;
  dom.error.hidden = false;
}

function clearError() {
  errorKind = null;
  dom.error.hidden = true;
}

/** Selo "Proxy": aparece nas frações quando alguma fonte de vídeo tem proxy válido (a full nunca lê proxy). */
function renderProxyBadge() {
  const sources = state.plan?.sources ?? [];
  dom.proxyBadge.hidden = !(state.res !== "full" && sources.some((source) => source.kind === "video" && source.proxy));
}

const EMPTY_MESSAGES = {
  loading: "Carregando o plano…",
  missing: "Nenhum plano neste trabalho. Peça à IA para montar o plano.",
  invalid: "Nenhum plano válido para exibir. Peça à IA para corrigir o plano.",
};

/** Mensagem central quando não há plano; some assim que a primeira versão válida chega. */
function renderEmpty() {
  const message = state.plan ? null : (state.connectionLost ? EMPTY_MESSAGES.invalid : EMPTY_MESSAGES[state.status]);
  dom.empty.hidden = message == null;
  dom.empty.textContent = message ?? "";
}

// --- Transporte: timecode, ícones e disponibilidade dos botões ---

/** Botão que continua focável quando indisponível (`aria-disabled`), para o foco não se perder ao chegar ao limite. */
function setAvailable(button, available) {
  button.setAttribute("aria-disabled", String(!available));
}

function renderTransport() {
  const plan = state.plan;
  const rate = fps();
  dom.tcCurrent.textContent = timecode(state.cursor, rate);
  dom.tcTotal.textContent = timecode(duration(), rate);
  dom.playIcon.setAttribute("href", state.playing ? "#i-pause" : "#i-play");
  const label = state.playing ? "Pausar (Espaço)" : "Reproduzir (Espaço)";
  dom.btnPlay.setAttribute("aria-label", label);
  dom.btnPlay.title = label;
  const frame = frameIndex(state.cursor, rate);
  const starts = plan ? plan.segments.map((segment) => segment.start_s) : [];
  const tolerance = 0.5 / rate;
  setAvailable(dom.btnPlay, plan !== null);
  setAvailable(dom.btnFramePrev, plan !== null && frame > 0);
  setAvailable(dom.btnFrameNext, plan !== null && frame < lastFrameIndex(duration(), rate));
  setAvailable(dom.btnCutPrev, starts.some((start) => start < state.cursor - tolerance));
  setAvailable(dom.btnCutNext, starts.some((start) => start > state.cursor + tolerance));
  dom.btnPlaySelection.hidden = state.selection === null;
}

function setCursor(seconds) {
  if (seconds !== state.cursor) {
    state.cursor = seconds;
    emit("cursor");
  }
}

// --- Quadro de scrub (overlay) ---

/** Mostra o quadro composto de `t`: um pedido em voo, o último pendente vence (F4, passo 2). */
export function showFrame(t) {
  if (!state.plan) return;
  clearError();
  framePending = t;
  if (!frameBusy) void pumpFrames();
}

async function pumpFrames() {
  frameBusy = true;
  refreshSpinner();
  while (framePending !== null) {
    const t = framePending;
    framePending = null;
    const controller = new AbortController();
    frameAbort = controller;
    try {
      const blob = await api.getFrame(t, state.res, fps(), controller.signal);
      if (!controller.signal.aborted) applyFrame(blob);
    } catch (error) {
      if (!controller.signal.aborted && framePending === null) {
        showError("frame", error.code === "FRAME_FAILED" ? error.message : "Não foi possível extrair este quadro.");
      }
    }
  }
  frameAbort = null;
  frameBusy = false;
  refreshSpinner();
}

function applyFrame(blob) {
  const previous = frameUrl;
  frameUrl = URL.createObjectURL(blob);
  dom.scrub.src = frameUrl;
  dom.scrub.hidden = false;
  if (previous) URL.revokeObjectURL(previous);
}

/** Cancela o quadro em voo e o pendente (o plano ou a resolução mudou, ou um stream vai cobrir a imagem). */
function cancelFrames() {
  framePending = null;
  frameAbort?.abort();
}

// --- Stream: iniciar, retomar, pausar e abandonar ---

/** Tempo da montagem do quadro que o `<video>` mostra agora. */
function mediaTime() {
  return streamStart + video.currentTime;
}

/** Descarta o stream carregado (o plano ou a resolução mudou, ou ele falhou): o próximo play reinicia em T. */
function abandonStream() {
  generation += 1;
  streamStart = null;
  streamLoading = false;
  buffering = false;
  cancelAnimationFrame(raf);
  raf = 0;
  video.pause();
  video.removeAttribute("src");
  video.load();
  refreshSpinner();
}

/** Pede ao servidor o stream a partir de `t` e toca; o cursor fica em `t` até o primeiro quadro chegar. */
function startStream(t) {
  const mine = ++generation;
  clearError();
  cancelFrames();
  streamStart = snap(t, fps(), duration());
  streamLoading = true;
  buffering = false;
  setCursor(streamStart);
  state.playing = true;
  emit("play");
  refreshSpinner();
  video.src = api.streamUrl(streamStart, state.res, fps());
  video.play().catch((error) => {
    // AbortError acontece quando um stream novo troca o `src`: o evento é de um stream antigo.
    if (mine === generation && error?.name !== "AbortError") failPlayback();
  });
  // O primeiro quadro apresentado encerra o carregamento e tira o overlay; `playing` cobre a página oculta,
  // onde o navegador não apresenta quadros.
  let revealed = false;
  const reveal = () => {
    if (mine !== generation || revealed) return;
    revealed = true;
    streamLoading = false;
    dom.scrub.hidden = true;
    refreshSpinner();
  };
  if ("requestVideoFrameCallback" in video) video.requestVideoFrameCallback(reveal);
  video.addEventListener("playing", () => setTimeout(reveal, REVEAL_FALLBACK_MS), { once: true });
  scheduleTick();
}

/** Retoma sem novo stream só no quadro em que pausou, com o stream ainda vivo (menos de 20 s parado). */
function canResume(t) {
  return (
    streamStart !== null &&
    !streamLoading &&
    !video.error &&
    pausedFrame === frameIndex(t, fps()) &&
    performance.now() - pausedAt < RESUME_MAX_PAUSE_MS
  );
}

function resume() {
  clearError();
  state.playing = true;
  emit("play");
  dom.scrub.hidden = true;
  video.play().catch((error) => {
    if (error?.name !== "AbortError") failPlayback();
  });
  scheduleTick();
}

/** Para a reprodução por falha: mantém o cursor e mostra o erro inline com "Tentar de novo". */
function failPlayback() {
  const wasPlaying = state.playing;
  state.playing = false;
  stopAt = null;
  abandonStream();
  showError("playback", "Não foi possível reproduzir este trecho.");
  if (wasPlaying) emit("play");
}

/** Laço leve de quadros (rAF): lê o tempo do vídeo e move o cursor; também aplica o fim da "Reproduzir seleção". */
function scheduleTick() {
  if (raf === 0) raf = requestAnimationFrame(tick);
}

function tick() {
  raf = 0;
  if (!state.playing || streamStart === null) return;
  if (!streamLoading) {
    const t = mediaTime();
    setCursor(snap(t, fps(), duration()));
    if (stopAt !== null && t >= stopAt - 0.5 / fps()) {
      finishSelectionPlayback();
      return;
    }
  }
  scheduleTick();
}

function finishSelectionPlayback() {
  const end = stopAt;
  pause();
  pausedFrame = null; // o vídeo parou perto do fim, mas o cursor fica nele: o próximo play reinicia o stream ali
  goTo(snap(end, fps(), duration()));
}

// --- API pública do player (transporte, scrub, resolução, recarga) ---

/** Início ou retomada da reprodução no cursor (no início se o cursor está no último quadro). */
export function play() {
  if (!state.plan || state.playing) return;
  retries = 0;
  stopAt = null;
  clearError();
  const rate = fps();
  const at = frameIndex(state.cursor, rate) >= lastFrameIndex(duration(), rate) ? 0 : state.cursor;
  if (canResume(at)) resume();
  else startStream(at);
}

/** Pausa e fixa o cursor no quadro que estava na tela. */
export function pause() {
  if (!state.playing) return;
  const hadFirstFrame = streamStart !== null && !streamLoading;
  video.pause();
  if (hadFirstFrame) setCursor(snap(mediaTime(), fps(), duration()));
  state.playing = false;
  stopAt = null;
  pausedAt = performance.now();
  pausedFrame = hadFirstFrame ? frameIndex(state.cursor, fps()) : null;
  buffering = false;
  if (!hadFirstFrame) {
    // Pausou antes do primeiro quadro: o stream não serve para retomar.
    abandonStream();
    showFrame(state.cursor);
  }
  refreshSpinner();
  emit("play");
}

export function togglePlay() {
  if (state.playing) pause();
  else play();
}

/** Move o cursor para `t` sem tocar: quadro composto do instante (scrub). Pausa se estava tocando. */
export function scrubTo(t) {
  if (!state.plan) return;
  if (state.playing) pause();
  stopAt = null;
  setCursor(snap(t, fps(), duration()));
  showFrame(state.cursor);
}

/** Move o cursor para `t` (já na grade) e mostra o quadro composto desse instante. */
function goTo(t) {
  setCursor(t);
  showFrame(t);
}

/** Quadro a quadro: pausa e mostra o quadro composto do quadro vizinho. */
export function stepFrame(direction) {
  if (!state.plan) return;
  pause();
  const rate = fps();
  const frame = Math.min(lastFrameIndex(duration(), rate), Math.max(0, frameIndex(state.cursor, rate) + direction));
  goTo(frame / rate);
}

/** Salto de corte: início do segmento anterior ou seguinte; reinicia o stream ali se estava tocando. */
export function jumpCut(direction) {
  if (!state.plan) return;
  const rate = fps();
  const tolerance = 0.5 / rate;
  const starts = state.plan.segments.map((segment) => segment.start_s);
  const target =
    direction < 0
      ? [...starts].reverse().find((start) => start < state.cursor - tolerance)
      : starts.find((start) => start > state.cursor + tolerance);
  if (target === undefined) return;
  const snapped = snapBoundary(target, rate, duration());
  if (state.playing) {
    startStream(snapped);
  } else {
    stopAt = null;
    goTo(snapped);
  }
}

/** "Reproduzir seleção": toca do início ao fim do trecho selecionado e para. */
export function playSelection() {
  const selection = state.selection;
  if (!selection || !state.plan) return;
  if (state.playing) pause();
  retries = 0;
  stopAt = selection.end;
  startStream(snap(selection.start, fps(), duration()));
}

/** Troca de resolução: reinicia no tempo atual na nova resolução, mantendo play ou pausa. */
export function setResolution(res) {
  if (res === state.res || !state.resolutions.includes(res)) return;
  state.res = res;
  writePref("res", res);
  emit("res");
  restartAfterChange();
}

/** O stream ou o quadro carregado deixou de valer (plano ou resolução mudou): refaz a imagem no cursor. */
export function restartAfterChange() {
  if (!state.plan) return;
  cancelFrames();
  if (state.playing) {
    // Reinicia onde o vídeo está agora (o cursor só anda a cada quadro do laço de reprodução).
    startStream(streamStart !== null && !streamLoading ? snap(mediaTime(), fps(), duration()) : state.cursor);
  } else {
    abandonStream();
    showFrame(state.cursor);
  }
}

/** Plano novo (ou o primeiro): o cursor já foi limitado à nova duração; descarta o stream antigo. */
export function reloadForPlan() {
  restartAfterChange();
}

// --- Volume (preferência por trabalho) ---

function renderVolume() {
  const muted = video.muted || video.volume === 0;
  dom.volumeIcon.setAttribute("href", muted ? "#i-volume-off" : "#i-volume");
  const label = muted ? "Ativar som (M)" : "Silenciar (M)";
  dom.btnVolume.setAttribute("aria-label", label);
  dom.btnVolume.title = label;
  dom.volume.value = String(video.muted ? 0 : video.volume);
}

export function toggleMute() {
  video.muted = !video.muted;
  if (!video.muted && video.volume === 0) video.volume = 0.5;
  writePref("muted", video.muted);
  renderVolume();
}

/** Lê as preferências do trabalho (resolução, volume e mudo) assim que o nome do trabalho é conhecido. */
export function restorePreferences() {
  const saved = readPref("res");
  if (saved && state.resolutions.includes(saved)) state.res = saved;
  const volume = Number(readPref("volume"));
  if (readPref("volume") !== null && Number.isFinite(volume) && volume >= 0 && volume <= 1) video.volume = volume;
  video.muted = readPref("muted") === "true";
  renderVolume();
}

// --- Ligação com a página ---

function onClick(button, handler) {
  button.addEventListener("click", () => {
    if (button.getAttribute("aria-disabled") !== "true") handler();
  });
}

onClick(dom.btnPlay, togglePlay);
onClick(dom.btnFramePrev, () => stepFrame(-1));
onClick(dom.btnFrameNext, () => stepFrame(1));
onClick(dom.btnCutPrev, () => jumpCut(-1));
onClick(dom.btnCutNext, () => jumpCut(1));
dom.btnPlaySelection.addEventListener("click", playSelection);
dom.btnVolume.addEventListener("click", toggleMute);
dom.volume.addEventListener("input", () => {
  video.volume = Number(dom.volume.value);
  video.muted = video.volume === 0;
  writePref("volume", video.volume);
  writePref("muted", video.muted);
  renderVolume();
});
dom.retry.addEventListener("click", () => {
  const kind = errorKind;
  clearError();
  if (kind === "frame") showFrame(state.cursor);
  else play();
});
// Clique na imagem alterna play e pausa (como nos players comuns); os avisos sobre ela têm botões próprios.
dom.player.addEventListener("click", (event) => {
  if (event.target === dom.video || event.target === dom.scrub) togglePlay();
});

video.addEventListener("waiting", () => {
  if (state.playing && !streamLoading) {
    buffering = true;
    refreshSpinner();
  }
});
for (const name of ["playing", "canplay", "seeked"]) {
  video.addEventListener(name, () => {
    buffering = false;
    refreshSpinner();
  });
}
video.addEventListener("ended", () => {
  if (!state.playing || streamStart === null) return;
  const t = mediaTime();
  const prematurely = t < duration() - 2 / fps();
  if (prematurely && retries < MAX_AUTO_RETRIES) {
    retries += 1;
    startStream(snap(t, fps(), duration()));
  } else if (prematurely) {
    failPlayback();
  } else {
    // Fim da timeline: o player para no último quadro (F3, passo 6).
    state.playing = false;
    stopAt = null;
    pausedAt = performance.now();
    setCursor(snap(duration(), fps(), duration()));
    emit("play");
  }
});
video.addEventListener("error", () => {
  if (streamStart === null || !video.getAttribute("src")) return; // erro do `removeAttribute("src")` da troca de stream
  if (!state.playing) {
    // Parado: o stream caiu (ex.: encerrado pelo servidor), a imagem fica e o próximo play reinicia.
    generation += 1;
    streamStart = null;
  } else if (!streamLoading && retries < MAX_AUTO_RETRIES) {
    retries += 1;
    startStream(snap(mediaTime(), fps(), duration()));
  } else {
    failPlayback();
  }
});

on("cursor", renderTransport);
on("play", renderTransport);
on("selection", renderTransport);
on("plan", () => {
  renderTransport();
  renderProxyBadge();
  renderEmpty();
});
on("status", () => {
  renderProxyBadge();
  renderEmpty();
});
on("res", renderProxyBadge);
renderTransport();
renderEmpty();
