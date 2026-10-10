// Painel do trecho (F4 passo 4, F6 passo 4 e F7): o que há na seleção e o botão "Copiar para IA".
//
// O conteúdo vem pronto do servidor, então a tela só desenha e copia: não recalcula nenhum intervalo. Na Montagem
// é `GET /api/selection` (segmentos, camadas e legendas com tempos da fonte, timecodes e o bloco de texto); no modo
// Fonte, `GET /api/source/selection` (situação do trecho da fonte: usado, descartado ou parcial, e cada parte usada
// com o ponto da montagem em que aparece). "Ver fonte" (num segmento) e "Ir para a montagem" (numa parte usada) são
// a ida e a volta entre os dois modos (`sources.js`). O painel também é recolhível (botão da barra superior ou
// tecla `\`) e, abaixo de 1280px, abre como sobreposição. Sem permissão da área de transferência, o texto aparece
// num campo selecionável dentro do painel.

import * as api from "./api.js";
import { goToMontage, openSource } from "./sources.js";
import { on, state } from "./store.js";
import { seconds3 } from "./timecode.js";
import { toast } from "./status.js";

const LAYER_LABELS = { text: "texto", image: "imagem", video: "vídeo" };
const STATUS_LABELS = { used: "Usado", partial: "Parcialmente usado", discarded: "Descartado" };

const dom = {
  app: document.getElementById("app"),
  toggle: document.getElementById("panel-toggle"),
  title: document.getElementById("panel-title"),
  body: document.getElementById("panel-body"),
  copy: document.getElementById("btn-copy"),
  manual: document.getElementById("manual-copy"),
  manualText: document.getElementById("manual-text"),
};

let data = null; // trecho carregado do servidor (null: sem seleção, carregando ou com erro)
let loadError = null;
let sequence = 0;
let controller = null;

// --- Abrir e fechar o painel ---

/** Abre ou fecha o painel (grade de `#app` e `aria-expanded` do botão). */
export function setPanelOpen(open) {
  state.panelOpen = open;
  dom.app.dataset.panel = open ? "open" : "closed";
  dom.toggle.setAttribute("aria-expanded", String(open));
}

export function togglePanel() {
  setPanelOpen(!state.panelOpen);
}

/** O painel abre aberto em tela larga (1280px ou mais) e recolhido nas menores, onde ele sobrepõe o palco. */
export function initPanelLayout() {
  setPanelOpen(window.matchMedia("(min-width: 1280px)").matches);
}

// --- Desenho do conteúdo ---

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Item de lista com título, detalhe e, opcionalmente, um botão de ação (textos sempre por `textContent`, nunca como HTML). */
function item(head, detail, action = null) {
  const node = element("li", "panel-item");
  node.append(element("div", "item-head", head));
  if (detail) node.append(element("div", "item-detail", detail));
  if (action !== null) {
    const button = element("button", "link-btn", action.label);
    button.type = "button";
    button.addEventListener("click", action.run);
    node.append(button);
  }
  return node;
}

function section(title, items) {
  const node = element("section", "panel-section");
  node.append(element("h3", "", title));
  if (items.length === 0) {
    node.append(element("p", "panel-none", "nenhum"));
  } else {
    const list = element("ul", "panel-list");
    list.append(...items);
    node.append(list);
  }
  return node;
}

/** "Ver fonte": abre a fonte do segmento no `in_s` dele, com o intervalo inteiro do segmento destacado. */
function viewSource(segment) {
  const planned = state.plan?.segments[segment.index - 1];
  if (!planned) return;
  openSource(planned.source_id, { at: planned.source_in_s, range: { start: planned.source_in_s, end: planned.source_out_s } });
}

function segmentItem(segment) {
  const head = `Segmento ${segment.index} · ${segment.source_id}`;
  const action = { label: "Ver fonte", run: () => viewSource(segment) };
  if (segment.video !== null) {
    const { source_in_s: from, source_out_s: to } = segment.video;
    return item(head, `Fonte: ${seconds3(from)} a ${seconds3(to)}`, action);
  }
  return item(head, `Só o áudio do corte ${segment.audio_cut.toUpperCase()} entra no trecho`, action);
}

function layerItem(layer) {
  const head = `Camada ${layer.index} · ${LAYER_LABELS[layer.type] ?? layer.type}`;
  if (layer.type === "text") return item(head, JSON.stringify(layer.text));
  if (layer.type === "video") {
    return item(head, `Fonte ${layer.source_id}: ${seconds3(layer.source_in_s)} a ${seconds3(layer.source_out_s)}`);
  }
  return item(head, `Fonte ${layer.source_id}`);
}

/** Legendas do trecho sem repetir o item quando ele aparece em mais de um lugar da montagem. */
function captionItems(captions) {
  const seen = new Set();
  const items = [];
  for (const caption of captions) {
    if (seen.has(caption.item)) continue;
    seen.add(caption.item);
    items.push(item(`Legenda ${caption.item}`, JSON.stringify(caption.text)));
  }
  return items;
}

const PART_LABELS = {
  segment: (part) => `Segmento ${part.index}`,
  audio: (part) => `Áudio do segmento ${part.index}`,
  layer: (part) => `Camada ${part.index} (vídeo)`,
};

/** Parte usada da fonte: de onde (tempos da fonte) para onde (timecode da montagem), com o atalho para lá. */
function sourcePartItem(part) {
  const where = part.loop
    ? `${part.start_timecode} a ${part.end_timecode} (em loop)`
    : `${part.start_timecode} (${seconds3(part.start_s)})`;
  return item(
    PART_LABELS[part.kind](part),
    `Fonte ${seconds3(part.source_in_s)} a ${seconds3(part.source_out_s)} · Montagem ${where}`,
    { label: "Ir para a montagem", run: () => goToMontage(part.start_s) },
  );
}

/** Situação do trecho da fonte como texto com marcador de cor (a cor nunca é a única pista). */
function statusLine(data) {
  const line = element("p", "source-status");
  const dot = element("span", `status-dot dot-${data.status === "used" ? `used-${data.source_kind}` : data.status}`);
  dot.setAttribute("aria-hidden", "true");
  line.append(dot, element("span", "", STATUS_LABELS[data.status]));
  return line;
}

function rangeBox(selection) {
  const box = element("dl", "range-box");
  const rows = [
    ["Início", selection.start_timecode, selection.start_s],
    ["Fim", selection.end_timecode, selection.end_s],
    ["Duração", selection.duration_timecode, selection.duration_s],
  ];
  for (const [label, code, seconds] of rows) {
    box.append(element("dt", "", label));
    const value = element("dd", "mono", `${code} (${seconds3(seconds)})`);
    box.append(value);
  }
  return box;
}

function renderPanel() {
  const source = state.mode === "source";
  dom.title.textContent = source ? "Trecho da fonte" : "Trecho";
  dom.body.replaceChildren();
  if (state.selection === null) {
    dom.body.append(
      element(
        "p",
        "panel-empty",
        source
          ? "Selecione um trecho na barra de uso: arraste com Shift ou use I e O."
          : "Selecione um trecho na timeline: arraste com Shift ou use I e O.",
      ),
    );
  } else if (loadError !== null) {
    dom.body.append(element("p", "panel-empty", loadError));
  } else if (data === null) {
    dom.body.append(element("p", "panel-empty", "Carregando o trecho…"));
  } else if (source) {
    dom.body.append(rangeBox(data), statusLine(data), section("Partes usadas", data.parts.map(sourcePartItem)));
  } else {
    dom.body.append(
      rangeBox(data),
      section("Segmentos", data.segments.map(segmentItem)),
      section("Camadas", data.layers.map(layerItem)),
      section("Legendas", captionItems(data.captions)),
    );
  }
  dom.copy.disabled = data === null;
  if (data === null) hideManual();
}

// --- Carga da seleção no servidor ---

/** Busca o trecho da seleção atual; um gesto mais novo cancela e invalida a busca anterior. */
function refresh() {
  controller?.abort();
  data = null;
  loadError = null;
  hideManual();
  const selection = state.selection;
  if (selection === null) {
    renderPanel();
    return;
  }
  const mine = ++sequence;
  controller = new AbortController();
  renderPanel();
  const request =
    state.mode === "source"
      ? api.getSourceSelection(state.sourceId, selection.start, selection.end, controller.signal)
      : api.getSelection(selection.start, selection.end, controller.signal);
  request.then(
    (result) => {
      if (mine !== sequence) return;
      data = result;
      renderPanel();
    },
    (error) => {
      if (mine !== sequence || error.name === "AbortError") return;
      loadError = error.message || "Não foi possível carregar o trecho.";
      renderPanel();
    },
  );
}

// --- Copiar para IA ---

function hideManual() {
  dom.manual.hidden = true;
  dom.manualText.value = "";
}

/** Sem a área de transferência: mostra o texto num campo, já selecionado, para o operador copiar. */
function showManual(text) {
  setPanelOpen(true);
  dom.manual.hidden = false;
  dom.manualText.value = text;
  dom.manualText.focus();
  dom.manualText.select();
}

/** Copia o bloco de texto do trecho para a área de transferência (toast) ou, sem permissão, para o campo manual. */
export async function copySelection() {
  if (state.selection === null) {
    toast("Selecione um trecho para copiar.");
    return;
  }
  if (data === null) {
    toast("O trecho ainda está carregando.");
    return;
  }
  const text = data.text;
  try {
    await navigator.clipboard.writeText(text);
    hideManual();
    toast("Trecho copiado");
  } catch {
    showManual(text);
  }
}

dom.toggle.addEventListener("click", togglePanel);
dom.copy.addEventListener("click", copySelection);
on("selection", ({ commit }) => {
  if (commit) refresh();
});
// Outro modo ou outra fonte: o trecho carregado é do contexto anterior; relê o da visão que passou a estar na tela.
on("context", refresh);
on("plan", renderPanel);
renderPanel();
