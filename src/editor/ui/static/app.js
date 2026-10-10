// Ponto de entrada da interface de revisão, modo Montagem (phase 7, T4).
//
// Cada módulo cuida de uma responsabilidade e conversa pelos eventos de `store.js`:
//   store      estado e preferências por trabalho      api        cliente da API local (`ui/server.py`)
//   timecode   grade de quadros e timecode              status     toast, faixa de estado e cabeçalho
//   player     stream ao vivo, scrub e transporte       timeline   canvas: faixas, zoom, scrub e seleção
//   selection  marcas I/O e trecho selecionado          panel      painel do trecho e "Copiar para IA"
//   reload     recarga quando o plano muda              shortcuts  atalhos do Premiere
// Aqui só se liga o que depende do nome do trabalho (resolução salva e seletor) e se inicia a recarga.
//
// Pontos de extensão: T5 habilita o modo Fonte (grupo `#mode-group`) e T6 ocupa `#approval-slot` e
// `#approve-slot` da barra superior.

import "./status.js";
import * as player from "./player.js";
import "./selection.js";
import { initPanelLayout } from "./panel.js";
import "./timeline.js";
import "./shortcuts.js";
import { startReload } from "./reload.js";
import { on, state } from "./store.js";

const resGroup = document.getElementById("res-group");

/** Seletor de resolução montado com a lista do servidor (fonte única da allowlist), no valor salvo do trabalho. */
function buildResolutionGroup() {
  resGroup.replaceChildren();
  for (const res of state.resolutions) {
    const label = document.createElement("label");
    label.className = "seg-item";
    const text = res === "full" ? "Full" : res;
    label.title = `Resolução ${text}`;
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "res";
    input.value = res;
    input.checked = res === state.res;
    input.addEventListener("change", () => player.setResolution(res));
    const span = document.createElement("span");
    span.textContent = text;
    label.append(input, span);
    resGroup.append(label);
  }
}

on("workdir", () => {
  player.restorePreferences();
  buildResolutionGroup();
});

on("res", () => {
  for (const input of resGroup.querySelectorAll("input")) input.checked = input.value === state.res;
});

initPanelLayout();
startReload();
