// Atalhos de teclado no padrão do Premiere (spec, suposição 12; design, "Acessibilidade").
//
// Espaço = play/pausa; ← → = quadro; ↑ ↓ = corte anterior/seguinte; I e O = início e fim da seleção; Esc =
// limpa a seleção; `\` = painel; Ctrl+Shift+C = copiar o trecho; Home/End = início e fim; M = mudo.
//
// Convivência com os controles nativos: quem chegou a um botão, slider ou rádio por teclado (Tab) mantém as
// teclas dele (Espaço no botão, setas no slider). Quem o focou com o mouse volta a usar as teclas da edição,
// para que Espaço continue a ser play/pausa e as setas continuem a mudar de quadro depois de um clique no
// seletor de resolução, por exemplo. (`:focus-visible` não serve: o navegador o liga na primeira tecla.)
// Campo de texto e `<select>` sempre ficam com a tecla.

import { copySelection, setPanelOpen, togglePanel } from "./panel.js";
import * as player from "./player.js";
import * as selection from "./selection.js";
import { duration, fps, state } from "./store.js";
import { lastFrameIndex } from "./timecode.js";

const SLIDER_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);
const RADIO_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", " "]);

let pointerControl = null; // controle que recebeu o foco por clique, até o próximo Tab

document.addEventListener(
  "pointerdown",
  (event) => {
    const origin = event.target instanceof Element ? event.target : null;
    pointerControl = origin?.closest("button, input, a") ?? origin?.closest("label")?.querySelector("input") ?? null;
  },
  true,
);

/** O elemento focado é dono desta tecla (campo de texto, ou controle nativo focado por teclado)? */
function targetOwnsKey(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return true;
  if (target === pointerControl) return false;
  if (target.tagName === "INPUT") {
    if (target.type === "range") return SLIDER_KEYS.has(event.key);
    // Espaço num rádio já marcado não faz nada nativamente, então segue para a edição.
    if (target.type === "radio") return RADIO_KEYS.has(event.key) && !(event.key === " " && target.checked);
    return true;
  }
  if (target.tagName === "BUTTON" || target.tagName === "A") return event.key === " ";
  return false;
}

/** Esc: limpa a seleção; sem seleção, fecha o painel quando ele está sobreposto ao palco (telas estreitas). */
function escape() {
  if (state.marks.start !== null || state.marks.end !== null) {
    selection.clearSelection();
  } else if (state.panelOpen && window.matchMedia("(max-width: 1279px)").matches) {
    setPanelOpen(false);
  }
}

const ACTIONS = {
  " ": () => player.togglePlay(),
  ArrowLeft: () => player.stepFrame(-1),
  ArrowRight: () => player.stepFrame(1),
  ArrowUp: () => player.jumpCut(-1),
  ArrowDown: () => player.jumpCut(1),
  Home: () => player.scrubTo(0),
  End: () => player.scrubTo(lastFrameIndex(duration(), fps()) / fps()),
  i: () => state.plan && selection.markIn(state.cursor),
  o: () => state.plan && selection.markOut(state.cursor),
  m: () => player.toggleMute(),
  Escape: escape,
  "\\": togglePanel,
};

let spaceHandled = false;

document.addEventListener("keydown", (event) => {
  if (event.key === "Tab") pointerControl = null;
  if (event.defaultPrevented) return;
  if (event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey && event.key.toLowerCase() === "c") {
    if (state.selection !== null) {
      event.preventDefault();
      void copySelection();
    }
    return;
  }
  if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const action = ACTIONS[key];
  if (!action || targetOwnsKey(event)) return;
  if (!state.plan && key !== "\\" && key !== "Escape") return;
  event.preventDefault();
  if (key === " ") spaceHandled = true;
  action();
});

// O clique de um botão acontece no `keyup` do Espaço: impedi-lo evita acionar duas vezes (play e o botão focado).
document.addEventListener("keyup", (event) => {
  if (event.key === " " && spaceHandled) {
    spaceHandled = false;
    event.preventDefault();
  }
});
