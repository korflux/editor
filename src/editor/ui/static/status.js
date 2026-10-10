// Avisos da tela: toast, faixa de estado e cabeçalho do trabalho (nome, hash do plano, selo de versão).
//
// A faixa de estado mostra o código e a mensagem segura de um plano inválido (a interface continua com a
// última versão válida, marcada como desatualizada) ou a perda de conexão com o servidor. O toast é um por
// vez, dura 4 s e é anunciado por `role="status"`.

import { on, state } from "./store.js";

const TOAST_MS = 4000;
const SHORT_HASH_CHARS = 8;

const dom = {
  toast: document.getElementById("toast"),
  statusbar: document.getElementById("statusbar"),
  staleBadge: document.getElementById("stale-badge"),
  planHash: document.getElementById("plan-hash"),
  workdirName: document.getElementById("workdir-name"),
};

let toastTimer = null;

/** Mostra `message` no toast por 4 s, substituindo o anterior. */
export function toast(message) {
  dom.toast.textContent = message;
  // Reinicia a transição quando um toast novo chega com o anterior ainda visível.
  dom.toast.classList.remove("show");
  void dom.toast.offsetWidth;
  dom.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => dom.toast.classList.remove("show"), TOAST_MS);
}

/** Código e texto da faixa de estado (sem código para o aviso de conexão, que não vem do servidor), ou `null` sem aviso. */
function statusBarContent() {
  if (state.connectionLost) {
    return { code: null, message: "A conexão com a interface foi perdida. Abra de novo pela CLI: editor ui start." };
  }
  if ((state.status === "outdated" || state.status === "invalid") && state.error) return state.error;
  return null;
}

/** Atualiza faixa de estado, selo "Versão desatualizada", hash e nome do trabalho a partir do estado. */
export function renderStatus() {
  const content = statusBarContent();
  dom.statusbar.hidden = content === null;
  dom.statusbar.replaceChildren();
  if (content !== null) {
    const text = document.createElement("span");
    text.className = "status-text";
    text.textContent = content.message;
    text.title = content.message;
    if (content.code !== null) {
      const code = document.createElement("span");
      code.className = "status-code";
      code.textContent = content.code;
      dom.statusbar.append(code);
    }
    dom.statusbar.append(text);
  }
  dom.staleBadge.hidden = state.status !== "outdated";
  dom.workdirName.textContent = state.workdirName || "Editor";
  dom.workdirName.title = state.workdirName;
  document.title = state.workdirName ? `${state.workdirName} · Editor` : "Editor, revisão da montagem";
  const hash = state.plan?.plan_sha256 ?? null;
  dom.planHash.hidden = hash === null;
  dom.planHash.textContent = hash ? hash.slice(0, SHORT_HASH_CHARS) : "";
  dom.planHash.title = hash ? `Plano (SHA-256): ${hash}` : "";
}

on("status", renderStatus);
on("plan", renderStatus);
