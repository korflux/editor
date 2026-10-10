// Timeline em canvas (F4): régua, faixas, blocos com J/L hachurado, zoom, playhead, scrub e seleção.
//
// Estrutura: o `#tl-scroller` dá a rolagem nativa (barra horizontal e vertical) e o canvas fica colado
// (`position: sticky`) no canto visível, desenhando só o que cabe na janela; o rótulo das faixas (à esquerda)
// e a régua (no topo) não rolam. Faixas, de cima para baixo: Legendas, Texto, Camadas (imagem e vídeo), V1
// (segmentos), A1 (áudio dos segmentos, com a parte J/L hachurada além do vídeo) e A2+ (demais trilhas).
// Blocos que se sobrepõem na mesma faixa vão para linhas separadas dentro dela.
//
// Gestos: clique/arraste = scrub (quadro composto); Shift+arraste = seleção; Alt+clique num bloco = intervalo
// exato do bloco; arrastar uma alça na régua ajusta a seleção; Ctrl+roda = zoom; roda = rolagem horizontal.
//
// ponytail: desenho completo a cada quadro de reprodução, sem índice espacial nem camada separada para o
// playhead; separar uma camada de cursor/seleção se planos de milhares de blocos pesarem na reprodução.

import * as player from "./player.js";
import * as selection from "./selection.js";
import { duration, emit, fps, on, state } from "./store.js";
import { timecode } from "./timecode.js";

const RULER_H = 26;
const ROW_H = 18;
const ROW_GAP = 2;
const LANE_PAD = 3;
const LANE_GAP = 2;
const RIGHT_PAD = 24;
const HANDLE_HIT_PX = 6;
const MIN_PPS = 0.5;
const MAX_FRAME_PX = 48; // zoom máximo: 48 px por quadro
const HATCH_PX = 8;

const dom = {
  zone: document.getElementById("timeline"),
  scroller: document.getElementById("tl-scroller"),
  canvas: document.getElementById("tl-canvas"),
  spacer: document.getElementById("tl-spacer"),
  zoom: document.getElementById("zoom"),
  zoomFit: document.getElementById("zoom-fit"),
  tip: document.getElementById("tl-tip"),
};
const ctx = dom.canvas.getContext("2d");
const narrowQuery = window.matchMedia("(max-width: 1023px)");

let lanes = []; // faixas calculadas do plano (blocos, linhas e posição vertical)
let contentHeight = 0;
let pps = 50; // pixels por segundo
let zoomFit = true; // enquanto verdadeiro a montagem inteira cabe na largura (também ao redimensionar)
let viewW = 0;
let viewH = 0;
let dpr = 1;
let colors = {};
let hatch = null;
let hover = null; // bloco sob o ponteiro
let drag = null; // gesto em andamento: { type: "scrub" | "select" | "handle", ... }
let drawQueued = false;

// --- Cores (lidas dos tokens do CSS; texto do bloco escolhido pelo maior contraste) ---

function luminance(hex) {
  const channel = (offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** Branco ou o tom escuro do kit, o que der mais contraste sobre `background` (mínimo de 4,5:1 nas faixas do design). */
function readableOn(background, dark) {
  const contrast = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  const bg = luminance(background);
  return contrast(bg, luminance("#ffffff")) >= contrast(bg, luminance(dark)) ? "#ffffff" : dark;
}

function readColors() {
  const style = getComputedStyle(document.documentElement);
  const token = (name) => style.getPropertyValue(name).trim();
  const dark = token("--accent-text");
  const lane = (name) => ({ fill: token(name), text: readableOn(token(name), dark) });
  colors = {
    base: token("--bg-base"),
    panel: token("--bg-panel"),
    raised: token("--bg-raised"),
    border: token("--border"),
    text: token("--text"),
    muted: token("--text-muted"),
    accent: token("--accent"),
    playhead: token("--playhead"),
    selection: token("--selection"),
    video: lane("--lane-video"),
    audio: lane("--lane-audio"),
    textLane: lane("--lane-text"),
    layer: lane("--lane-layer"),
    caption: lane("--lane-caption"),
  };
}

// --- Faixas e blocos a partir do plano ---

function labelWidth() {
  return narrowQuery.matches ? 40 : 72;
}

/** Distribui os blocos da faixa em linhas sem sobreposição e devolve quantas linhas foram necessárias. */
function packRows(blocks) {
  const rowEnds = [];
  for (const block of [...blocks].sort((a, b) => a.start - b.start || a.end - b.end)) {
    let row = rowEnds.findIndex((end) => end <= block.start + 1e-6);
    if (row === -1) {
      row = rowEnds.length;
      rowEnds.push(0);
    }
    rowEnds[row] = block.end;
    block.row = row;
  }
  return Math.max(1, rowEnds.length);
}

function oneLine(text) {
  return String(text).replace(/\s+/g, " ").trim();
}

function fixed(value) {
  return Number(value).toFixed(3);
}

/** Bloco de áudio de um segmento: a janela inteira, com a parte além do vídeo (J e L) marcada como hachurada. */
function audioBlock(segment, label, tip) {
  return {
    start: segment.audio_start_s,
    end: segment.audio_end_s,
    solidStart: Math.max(segment.audio_start_s, segment.start_s),
    solidEnd: Math.min(segment.audio_end_s, segment.end_s),
    label,
    tip,
    select: [segment.audio_start_s, segment.audio_end_s],
  };
}

function buildLanes(plan) {
  const captions = (plan.captions?.occurrences ?? []).map((occurrence) => ({
    start: occurrence.start_s,
    end: occurrence.end_s,
    label: oneLine(occurrence.text),
    tip: `Legenda ${occurrence.item}: ${occurrence.text}`,
    select: [occurrence.start_s, occurrence.end_s],
  }));
  const texts = [];
  const layers = [];
  for (const layer of plan.layers) {
    const range = [layer.start_s, layer.end_s];
    if (layer.type === "text") {
      texts.push({
        start: layer.start_s,
        end: layer.end_s,
        label: oneLine(layer.text),
        tip: `Camada ${layer.index} (texto): ${layer.text}`,
        select: range,
      });
    } else {
      const kind = layer.type === "video" ? "vídeo" : "imagem";
      const origin = layer.type === "video" ? ` (fonte ${fixed(layer.source_in_s)} s a ${fixed(layer.source_out_s)} s)` : "";
      layers.push({
        start: layer.start_s,
        end: layer.end_s,
        label: layer.source_id,
        tip: `Camada ${layer.index} (${kind}): ${layer.source_id}${origin}`,
        select: range,
      });
    }
  }
  const video = plan.segments.map((segment) => ({
    start: segment.start_s,
    end: segment.end_s,
    label: `${segment.index} · ${segment.source_id}`,
    tip: `Segmento ${segment.index}: ${segment.source_id}, fonte ${fixed(segment.source_in_s)} s a ${fixed(segment.source_out_s)} s`,
    select: [segment.start_s, segment.end_s],
  }));
  const audio = [];
  const trackLanes = Math.max(1, ...plan.segments.map((segment) => segment.tracks.length));
  for (let index = 0; index < trackLanes; index += 1) audio.push([]);
  for (const segment of plan.segments) {
    if (segment.audio_mode === "mute") {
      audio[0].push({
        start: segment.start_s,
        end: segment.end_s,
        solidStart: segment.start_s,
        solidEnd: segment.end_s,
        muted: true,
        label: "mudo",
        tip: `Segmento ${segment.index}: sem áudio`,
        select: [segment.start_s, segment.end_s],
      });
      continue;
    }
    segment.tracks.forEach((track, index) => {
      const jl = segment.audio_start_s < segment.start_s - 1e-6 || segment.audio_end_s > segment.end_s + 1e-6;
      const tip =
        `Áudio do segmento ${segment.index}: ${track.source_id}, fonte ${fixed(track.in_s)} s a ${fixed(track.out_s)} s` +
        (track.gain_db ? `, ganho ${track.gain_db} dB` : "") +
        (jl ? "\nA parte hachurada passa do limite do vídeo (corte J ou L)." : "");
      audio[index].push(audioBlock(segment, track.source_id, tip));
    });
  }
  const specs = [
    { key: "caption", label: "Legendas", short: "L", color: colors.caption, blocks: captions },
    { key: "text", label: "Texto", short: "T", color: colors.textLane, blocks: texts },
    { key: "layer", label: "Camadas", short: "C", color: colors.layer, blocks: layers },
    { key: "video", label: "V1", short: "V1", color: colors.video, blocks: video },
    ...audio.map((blocks, index) => ({
      key: `audio${index + 1}`,
      label: `A${index + 1}`,
      short: `A${index + 1}`,
      color: colors.audio,
      blocks,
    })),
  ];
  let y = 0;
  return specs.map((spec) => {
    const rows = packRows(spec.blocks);
    const height = LANE_PAD * 2 + rows * ROW_H + (rows - 1) * ROW_GAP;
    const lane = { ...spec, rows, y, h: height };
    y += height + LANE_GAP;
    contentHeight = y;
    return lane;
  });
}

// --- Zoom e rolagem ---

function fitPps() {
  return Math.max(MIN_PPS, (viewW - labelWidth() - RIGHT_PAD) / Math.max(duration(), 0.001));
}

function maxPps() {
  return Math.max(fitPps(), fps() * MAX_FRAME_PX);
}

function clampPps(value) {
  return Math.min(maxPps(), Math.max(Math.min(fitPps(), maxPps()), value));
}

function sliderFromPps() {
  const fit = fitPps();
  const max = maxPps();
  return max <= fit ? 0 : (100 * Math.log(pps / fit)) / Math.log(max / fit);
}

function ppsFromSlider(value) {
  const fit = fitPps();
  return fit * (maxPps() / fit) ** (value / 100);
}

/** Aplica o zoom `next` mantendo o instante `anchorTime` na mesma posição da tela (`anchorX`). */
function applyZoom(next, anchorTime = null, anchorX = null) {
  const scroller = dom.scroller;
  const left = labelWidth();
  const anchor = anchorTime ?? (scroller.scrollLeft + (viewW - left) / 2) / pps;
  const x = anchorX ?? left + (viewW - left) / 2;
  pps = clampPps(next);
  syncSpacer();
  scroller.scrollLeft = Math.max(0, anchor * pps - (x - left));
  dom.zoom.value = String(Math.round(sliderFromPps()));
  requestDraw();
}

function syncSpacer() {
  dom.spacer.style.width = `${labelWidth() + duration() * pps + RIGHT_PAD}px`;
  dom.spacer.style.height = `${RULER_H + contentHeight + 8}px`;
}

/** Mantém o cursor à vista: rola a timeline quando ele sai da janela (reprodução, quadro a quadro, saltos). */
function keepCursorVisible() {
  const left = labelWidth();
  const x = left + state.cursor * pps - dom.scroller.scrollLeft;
  if (x > viewW - 24 || x < left) {
    dom.scroller.scrollLeft = Math.max(0, state.cursor * pps - 0.2 * (viewW - left));
  }
}

// --- Canvas: tamanho, hachura e desenho ---

function layoutCanvas() {
  viewW = dom.scroller.clientWidth;
  viewH = dom.scroller.clientHeight;
  dpr = window.devicePixelRatio || 1;
  dom.canvas.style.width = `${viewW}px`;
  dom.canvas.style.height = `${viewH}px`;
  dom.canvas.width = Math.max(1, Math.round(viewW * dpr));
  dom.canvas.height = Math.max(1, Math.round(viewH * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const tile = document.createElement("canvas");
  tile.width = tile.height = Math.round(HATCH_PX * dpr);
  const tileCtx = tile.getContext("2d");
  tileCtx.strokeStyle = "rgba(11, 18, 32, 0.55)";
  tileCtx.lineWidth = 2 * dpr;
  tileCtx.beginPath();
  tileCtx.moveTo(0, tile.height);
  tileCtx.lineTo(tile.width, 0);
  tileCtx.stroke();
  hatch = ctx.createPattern(tile, "repeat");
  hatch.setTransform(new DOMMatrix().scale(1 / dpr));
  if (zoomFit && state.plan) pps = fitPps();
  syncSpacer();
  dom.zoom.value = String(Math.round(sliderFromPps()));
  requestDraw();
}

export function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(draw);
}

function xOf(seconds) {
  return labelWidth() + seconds * pps - dom.scroller.scrollLeft;
}

function fitText(text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let low = 0;
  let high = text.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (ctx.measureText(`${text.slice(0, middle)}…`).width <= maxWidth) low = middle;
    else high = middle - 1;
  }
  return low > 0 ? `${text.slice(0, low)}…` : "";
}

function drawBlock(lane, block, laneTop) {
  const left = labelWidth();
  const x1 = xOf(block.start);
  const x2 = xOf(block.end);
  const width = Math.max(1, x2 - x1);
  const y = laneTop + LANE_PAD + block.row * (ROW_H + ROW_GAP);
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x1, y, width, ROW_H, 4);
  if (block.muted) {
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = lane.color.fill;
    ctx.lineWidth = 1;
    ctx.stroke();
  } else {
    ctx.clip();
    ctx.fillStyle = lane.color.fill;
    ctx.fillRect(x1, y, width, ROW_H);
    for (const [from, to] of [[block.start, block.solidStart], [block.solidEnd, block.end]]) {
      if (to - from <= 1e-6) continue;
      const a = xOf(from);
      const b = xOf(to);
      ctx.fillStyle = "rgba(18, 18, 20, 0.5)";
      ctx.fillRect(a, y, b - a, ROW_H);
      ctx.fillStyle = hatch;
      ctx.fillRect(a, y, b - a, ROW_H);
    }
  }
  ctx.restore();
  if (hover === block) {
    ctx.save();
    ctx.strokeStyle = colors.text;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x1 + 0.5, y + 0.5, width - 1, ROW_H - 1, 4);
    ctx.stroke();
    ctx.restore();
  }
  // Texto no trecho sólido do bloco, que acompanha a borda esquerda da janela quando o bloco passa dela.
  const solidStart = block.solidStart ?? block.start;
  const solidEnd = block.solidEnd ?? block.end;
  const textLeft = Math.max(xOf(solidStart), left) + 6;
  const textRight = Math.min(xOf(solidEnd), viewW) - 4;
  if (textRight - textLeft > 18) {
    ctx.fillStyle = block.muted ? colors.muted : lane.color.text;
    const label = fitText(block.label, textRight - textLeft);
    if (label) ctx.fillText(label, textLeft, y + ROW_H / 2 + 0.5);
  }
}

function pickTickSteps() {
  const rate = fps();
  const candidates = [1, 2, 5, 10].map((frames) => frames / rate).filter((step) => step < 1);
  return [...candidates, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];
}

function drawRuler(visibleStart, visibleEnd) {
  const left = labelWidth();
  ctx.fillStyle = colors.raised;
  ctx.fillRect(left, 0, viewW - left, RULER_H);
  ctx.strokeStyle = colors.border;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(left, RULER_H - 0.5);
  ctx.lineTo(viewW, RULER_H - 0.5);
  ctx.stroke();
  const steps = pickTickSteps();
  const major = steps.find((step) => step * pps >= 90) ?? steps[steps.length - 1];
  const minorDivisions = [5, 2].find((divisions) => (major / divisions) * pps >= 10) ?? 1;
  const minor = major / minorDivisions;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, 0, viewW - left, RULER_H);
  ctx.clip();
  ctx.font = "11px ui-monospace, 'Cascadia Mono', Consolas, monospace";
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  for (let index = Math.floor(visibleStart / minor); index * minor <= visibleEnd; index += 1) {
    const seconds = index * minor;
    if (seconds < 0 || seconds > duration() + 1e-6) continue;
    const x = Math.round(xOf(seconds)) + 0.5;
    const isMajor = index % minorDivisions === 0;
    ctx.strokeStyle = colors.muted;
    ctx.beginPath();
    ctx.moveTo(x, RULER_H - 1);
    ctx.lineTo(x, RULER_H - (isMajor ? 10 : 5));
    ctx.stroke();
    if (isMajor) {
      ctx.fillStyle = colors.muted;
      ctx.fillText(timecode(seconds, fps()), x + 4, 12);
    }
  }
  ctx.restore();
}

function drawMarks() {
  const left = labelWidth();
  const { start, end } = state.marks;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, 0, viewW - left, viewH);
  ctx.clip();
  if (state.selection) {
    const x1 = xOf(start);
    const x2 = xOf(end);
    ctx.fillStyle = colors.selection;
    ctx.fillRect(x1, 0, x2 - x1, viewH);
  }
  for (const [mark, direction] of [[start, 1], [end, -1]]) {
    if (mark === null) continue;
    const x = Math.round(xOf(mark)) + 0.5;
    ctx.strokeStyle = colors.accent;
    ctx.fillStyle = colors.accent;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, viewH);
    ctx.stroke();
    // Alça na régua: bandeira que aponta para dentro do trecho.
    ctx.beginPath();
    ctx.moveTo(x, 2);
    ctx.lineTo(x + direction * 9, 2);
    ctx.lineTo(x + direction * 9, 9);
    ctx.lineTo(x, 16);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

function drawPlayhead() {
  const left = labelWidth();
  const x = Math.round(xOf(state.cursor)) + 0.5;
  if (x < left - 6 || x > viewW + 6) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, 0, viewW - left, viewH);
  ctx.clip();
  ctx.strokeStyle = colors.playhead;
  ctx.fillStyle = colors.playhead;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, viewH);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x - 6, 1);
  ctx.lineTo(x + 6, 1);
  ctx.lineTo(x + 6, 11);
  ctx.lineTo(x, 19);
  ctx.lineTo(x - 6, 11);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawLabels(scrollTop) {
  const left = labelWidth();
  ctx.fillStyle = colors.panel;
  ctx.fillRect(0, 0, left, viewH);
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, RULER_H, left, viewH - RULER_H);
  ctx.clip();
  ctx.font = "12px system-ui, 'Segoe UI', sans-serif";
  ctx.fillStyle = colors.muted;
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  const short = narrowQuery.matches;
  for (const lane of lanes) {
    const top = RULER_H + lane.y - scrollTop;
    ctx.fillText(short ? lane.short : lane.label, 8, top + LANE_PAD + ROW_H / 2);
  }
  ctx.restore();
  ctx.strokeStyle = colors.border;
  ctx.beginPath();
  ctx.moveTo(left - 0.5, 0);
  ctx.lineTo(left - 0.5, viewH);
  ctx.stroke();
}

function draw() {
  drawQueued = false;
  if (viewW === 0 || viewH === 0) return;
  ctx.clearRect(0, 0, viewW, viewH);
  ctx.fillStyle = colors.panel;
  ctx.fillRect(0, 0, viewW, viewH);
  if (!state.plan) {
    ctx.fillStyle = colors.muted;
    ctx.font = "13px system-ui, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("Sem plano para mostrar.", viewW / 2, viewH / 2);
    return;
  }
  const left = labelWidth();
  const scrollLeft = dom.scroller.scrollLeft;
  const scrollTop = dom.scroller.scrollTop;
  const visibleStart = scrollLeft / pps;
  const visibleEnd = (scrollLeft + viewW - left) / pps;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, RULER_H, viewW - left, viewH - RULER_H);
  ctx.clip();
  ctx.font = "12px system-ui, 'Segoe UI', sans-serif";
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  for (const lane of lanes) {
    const top = RULER_H + lane.y - scrollTop;
    if (top > viewH || top + lane.h < RULER_H) continue;
    ctx.fillStyle = colors.base;
    ctx.fillRect(left, top, viewW - left, lane.h);
    for (const block of lane.blocks) {
      if (block.end < visibleStart || block.start > visibleEnd) continue;
      drawBlock(lane, block, top);
    }
  }
  ctx.restore();
  drawRuler(visibleStart, visibleEnd);
  drawMarks();
  drawPlayhead();
  drawLabels(scrollTop);
}

// --- Acessibilidade do canvas (rótulo, valor do "slider" do cursor) ---

function updateAria() {
  dom.canvas.setAttribute("aria-valuemax", String(duration()));
  dom.canvas.setAttribute("aria-valuenow", state.cursor.toFixed(3));
  dom.canvas.setAttribute("aria-valuetext", timecode(state.cursor, fps()));
}

// --- Ponteiro: scrub, seleção, alças e dica dos blocos ---

function pointerPosition(event) {
  const rect = dom.canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function timeAt(x) {
  const seconds = (x - labelWidth() + dom.scroller.scrollLeft) / pps;
  return Math.min(duration(), Math.max(0, seconds));
}

function hitBlock(x, y) {
  if (y < RULER_H || x < labelWidth() || !state.plan) return null;
  const seconds = (x - labelWidth() + dom.scroller.scrollLeft) / pps;
  const inner = y - RULER_H + dom.scroller.scrollTop;
  for (const lane of lanes) {
    if (inner < lane.y || inner >= lane.y + lane.h) continue;
    const row = Math.floor((inner - lane.y - LANE_PAD) / (ROW_H + ROW_GAP));
    if (row < 0 || inner - lane.y - LANE_PAD - row * (ROW_H + ROW_GAP) > ROW_H) return null;
    return lane.blocks.find((block) => block.row === row && seconds >= block.start && seconds <= block.end) ?? null;
  }
  return null;
}

/** Alça de seleção sob o ponteiro na régua (`"start"` ou `"end"`), a mais próxima dentro do alcance. */
function hitHandle(x, y) {
  if (y >= RULER_H) return null;
  let best = null;
  for (const which of ["start", "end"]) {
    const mark = state.marks[which];
    if (mark === null) continue;
    const distance = Math.abs(x - xOf(mark));
    if (distance <= HANDLE_HIT_PX && (best === null || distance < best.distance)) best = { which, distance };
  }
  return best?.which ?? null;
}

function showTip(event, block) {
  if (block === null) {
    dom.tip.hidden = true;
    return;
  }
  dom.tip.textContent = block.tip;
  dom.tip.hidden = false;
  const zone = dom.zone.getBoundingClientRect();
  const left = Math.min(event.clientX - zone.left + 12, zone.width - dom.tip.offsetWidth - 8);
  const top = Math.min(event.clientY - zone.top + 16, zone.height - dom.tip.offsetHeight - 4);
  dom.tip.style.left = `${Math.max(4, left)}px`;
  dom.tip.style.top = `${Math.max(4, top)}px`;
}

function onPointerDown(event) {
  if (event.button !== 0 || !state.plan) return;
  const { x, y } = pointerPosition(event);
  dom.canvas.focus({ preventScroll: true });
  event.preventDefault();
  if (x < labelWidth()) return;
  showTip(event, null);
  const handle = hitHandle(x, y);
  if (handle) {
    drag = { type: "handle", which: handle };
  } else if (event.altKey) {
    const block = hitBlock(x, y);
    if (block) selection.selectRange(block.select[0], block.select[1], { exact: true });
    return;
  } else if (event.shiftKey) {
    drag = { type: "select", anchor: timeAt(x) };
  } else {
    drag = { type: "scrub" };
    player.scrubTo(timeAt(x));
  }
  try {
    dom.canvas.setPointerCapture(event.pointerId);
  } catch {
    // Ponteiro já encerrado ou sintético: o arrasto segue pelos eventos do próprio canvas.
  }
}

function onPointerMove(event) {
  const { x, y } = pointerPosition(event);
  if (drag) {
    const seconds = timeAt(x);
    if (drag.type === "scrub") player.scrubTo(seconds);
    else if (drag.type === "select") selection.selectRange(drag.anchor, seconds, { commit: false });
    else selection.moveMark(drag.which, seconds, { commit: false });
    return;
  }
  dom.canvas.style.cursor = hitHandle(x, y) ? "ew-resize" : "default";
  const block = hitBlock(x, y);
  if (block !== hover) {
    hover = block;
    requestDraw();
  }
  showTip(event, block);
}

function onPointerEnd(event) {
  if (!drag) return;
  const finished = drag;
  drag = null;
  if (dom.canvas.hasPointerCapture(event.pointerId)) dom.canvas.releasePointerCapture(event.pointerId);
  if (finished.type === "select") {
    const { x } = pointerPosition(event);
    selection.selectRange(finished.anchor, timeAt(x), { commit: true });
  } else if (finished.type === "handle") {
    emit("selection", { commit: true });
  }
}

dom.canvas.addEventListener("pointerdown", onPointerDown);
dom.canvas.addEventListener("pointermove", onPointerMove);
dom.canvas.addEventListener("pointerup", onPointerEnd);
dom.canvas.addEventListener("pointercancel", onPointerEnd);
dom.canvas.addEventListener("pointerleave", () => {
  showTip(null, null);
  if (hover !== null) {
    hover = null;
    requestDraw();
  }
});
dom.canvas.addEventListener(
  "wheel",
  (event) => {
    if (!state.plan) return;
    if (event.ctrlKey) {
      event.preventDefault();
      zoomFit = false;
      const { x } = pointerPosition(event);
      applyZoom(pps * (event.deltaY < 0 ? 1.25 : 0.8), timeAt(x), x);
      return;
    }
    // Sem rolagem vertical a roda vertical anda na horizontal, que é o eixo da timeline.
    const noVertical = dom.scroller.scrollHeight <= dom.scroller.clientHeight + 1;
    if (!event.shiftKey && noVertical && Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      event.preventDefault();
      dom.scroller.scrollLeft += event.deltaY;
    }
  },
  { passive: false },
);
dom.scroller.addEventListener("scroll", requestDraw, { passive: true });
dom.zoom.addEventListener("input", () => {
  zoomFit = false;
  applyZoom(ppsFromSlider(Number(dom.zoom.value)));
});
dom.zoomFit.addEventListener("click", () => {
  zoomFit = true;
  applyZoom(fitPps(), 0, labelWidth());
});

// --- Ligação com o resto da interface ---

/** Plano novo: refaz as faixas e o tamanho do conteúdo, mantendo o zoom escolhido (ou o ajuste à largura). */
function onPlan() {
  contentHeight = 0;
  lanes = state.plan ? buildLanes(state.plan) : [];
  hover = null;
  dom.canvas.setAttribute("aria-valuemax", String(duration()));
  if (zoomFit || !state.plan) pps = fitPps();
  else pps = clampPps(pps);
  syncSpacer();
  dom.zoom.value = String(Math.round(sliderFromPps()));
  updateAria();
  requestDraw();
}

on("plan", onPlan);
on("cursor", () => {
  updateAria();
  keepCursorVisible();
  requestDraw();
});
on("selection", requestDraw);
new ResizeObserver(layoutCanvas).observe(dom.scroller);
narrowQuery.addEventListener("change", () => {
  onPlan();
  layoutCanvas();
});
readColors();
layoutCanvas();
