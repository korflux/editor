// Aprovação da versão exibida (F8, passos 1 a 4): botão "Aprovar esta versão", popup de confirmação, selo e recusa.
//
// A situação da aprovação vem pronta do servidor (`state.approval`, calculada por `ui/approval.py` e entregue a
// cada consulta de estado): "Aprovado às HH:MM" (em dia), "Aprovação desatualizada" (o plano ou uma fonte mudou
// depois, ou o registro é obsoleto), "Verificando aprovação…" (as fontes estão sendo conferidas) ou nenhum selo.
//
// O popup é um `<dialog>` modal nativo (o resto da página fica inerte, e a pilha de foco e o Esc são do navegador),
// com o Tab preso por aqui, porque no navegador o último controle de um modal devolve o foco à barra de endereço.
// Ao abrir ele guarda o hash do plano e a impressão das fontes da versão que mostra, e é isso que envia ao
// confirmar: se o plano mudar com o popup aberto (a tela recarrega sozinha), a aprovação é da versão que o
// operador viu e o servidor a recusa com `APPROVAL_PLAN_CHANGED`, em vez de aprovar sem querer a versão nova.
// Qualquer recusa fecha o popup, recarrega o estado e mostra a mensagem segura do servidor na faixa de estado
// (`state.approvalNotice`), que fica até o operador abrir o popup de novo ou o plano ser substituído.
//
// O botão fica desabilitado sem plano válido e em dia (carregando, sem plano, plano atual inválido, sem conexão),
// com o motivo no tooltip. Enquanto a aprovação é gravada (o servidor lê as fontes por inteiro) o popup não fecha:
// Esc e Cancelar ficam desligados, porque o pedido já saiu e o resultado precisa ser mostrado.

import * as api from "./api.js";
import { refreshNow } from "./reload.js";
import { emit, on, state } from "./store.js";
import { toast } from "./status.js";
import { timecode } from "./timecode.js";

const SHORT_HASH_CHARS = 8;

const dom = {
  seal: document.getElementById("approval-seal"),
  button: document.getElementById("btn-approve"),
  dialog: document.getElementById("approve-dialog"),
  hash: document.getElementById("approve-hash"),
  duration: document.getElementById("approve-duration"),
  proxy: document.getElementById("approve-proxy"),
  cancel: document.getElementById("approve-cancel"),
  confirm: document.getElementById("approve-confirm"),
};

const CONFIRM_LABEL = "Aprovar";
const BUSY_LABEL = "Aprovando…";
const NETWORK_NOTICE = "Não foi possível aprovar: a interface não respondeu. Tente de novo.";

let captured = null; // { planSha256, fingerprint } da versão que o popup mostrou ao abrir
let busy = false;

// --- Botão e selo ---

/** Por que a aprovação está bloqueada agora (texto do tooltip), ou `null` quando há plano válido e em dia. */
function blockedReason() {
  if (state.connectionLost) return "Sem conexão com a interface.";
  if (state.plan === null) {
    return state.status === "loading" ? "Aguardando o plano ser carregado." : "Nenhum plano válido para aprovar.";
  }
  if (state.status !== "ready") return "O plano atual está inválido. Corrija o plano para aprovar esta versão.";
  return null;
}

/** "HH:MM" local do instante UTC da aprovação, ou `null` se o servidor mandou algo ilegível. */
function approvedTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? null
    : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

/** Habilita o botão com plano válido e em dia; desabilitado, o tooltip diz o motivo (habilitado ele se explica pelo texto). */
function renderButton() {
  const reason = blockedReason();
  dom.button.disabled = reason !== null;
  if (reason === null) dom.button.removeAttribute("title");
  else dom.button.title = reason;
}

/** Texto e tooltip do selo, trocados só quando mudam: o selo é uma região `role="status"` e reescrever o mesmo texto a cada consulta (1 s) o reanunciaria. */
function setSeal(text, title) {
  if (dom.seal.textContent !== text) dom.seal.textContent = text;
  if (dom.seal.title !== title) dom.seal.title = title;
}

/** Selo da barra superior conforme `state.approval`: em dia (`--ok`), desatualizada (`--warn`), em verificação ou nenhum. */
function renderSeal() {
  const kind = state.approval?.state ?? "none";
  dom.seal.hidden = kind === "none";
  dom.seal.classList.toggle("badge-ok", kind === "current");
  dom.seal.classList.toggle("badge-warn", kind === "stale");
  dom.seal.classList.toggle("badge-muted", kind === "checking");
  if (kind === "current") {
    const time = approvedTime(state.approval.approved_at);
    setSeal(time === null ? "Aprovado" : `Aprovado às ${time}`, "A aprovação vale para o plano e as fontes exibidos.");
  } else if (kind === "stale") {
    setSeal("Aprovação desatualizada", "O plano ou uma fonte mudou depois da aprovação. Aprove de novo para liberar a full.");
  } else if (kind === "checking") {
    setSeal("Verificando aprovação…", "Conferindo as fontes com a aprovação gravada.");
  } else {
    setSeal("", "");
  }
}

function render() {
  renderButton();
  renderSeal();
}

// --- Popup ---

/** A nota "Visualizado com proxy": a resolução em uso lê o proxy de ao menos uma fonte de vídeo (a full nunca lê). */
function viewedWithProxy(plan) {
  return state.res !== "full" && plan.sources.some((source) => source.kind === "video" && source.proxy);
}

/** Liga ou desliga o estado "Aprovando…": botões desligados e o foco no próprio popup, para o Esc e o Tab seguirem presos. */
function setBusy(value) {
  busy = value;
  dom.confirm.disabled = value;
  dom.cancel.disabled = value;
  dom.confirm.classList.toggle("btn-busy", value);
  dom.confirm.textContent = value ? BUSY_LABEL : CONFIRM_LABEL;
  if (value) dom.dialog.setAttribute("aria-busy", "true");
  else dom.dialog.removeAttribute("aria-busy");
  if (value) dom.dialog.focus();
}

/** Abre o popup com a versão exibida agora; o foco inicial fica em "Cancelar" (aprovar exige um gesto deliberado). */
function openDialog() {
  if (blockedReason() !== null || dom.dialog.open) return;
  const plan = state.plan;
  if (state.approvalNotice !== null) {
    state.approvalNotice = null;
    emit("status");
  }
  captured = { planSha256: plan.plan_sha256, fingerprint: plan.sources_fingerprint };
  dom.hash.textContent = plan.plan_sha256.slice(0, SHORT_HASH_CHARS);
  dom.hash.title = `Plano (SHA-256): ${plan.plan_sha256}`;
  dom.duration.textContent = timecode(plan.duration_s, plan.frame_rate);
  dom.proxy.hidden = !viewedWithProxy(plan);
  setBusy(false);
  dom.dialog.showModal();
  dom.cancel.focus();
}

/** Tab e Shift+Tab giram dentro do popup (entre "Cancelar" e "Aprovar"); sem controle ativo o foco fica no popup. */
function trapFocus(event) {
  if (event.key !== "Tab") return;
  const controls = [...dom.dialog.querySelectorAll("button:not(:disabled)")];
  if (controls.length === 0) {
    event.preventDefault();
    dom.dialog.focus();
    return;
  }
  const first = controls[0];
  const last = controls[controls.length - 1];
  const active = document.activeElement;
  if (!dom.dialog.contains(active) || (active === dom.dialog && event.shiftKey)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Confirmar: envia a versão guardada ao abrir. Sucesso mostra o toast e o selo (do estado recarregado); recusa
 * (mudança, plano inválido, fonte ilegível) ou falha de rede fecha o popup, recarrega a versão atual e mostra a
 * mensagem na faixa de estado só depois da recarga, que de outro modo a apagaria como notícia velha.
 */
async function confirm() {
  if (busy || captured === null) return;
  const { planSha256, fingerprint } = captured;
  setBusy(true);
  let notice = null;
  try {
    await api.approve(planSha256, fingerprint);
  } catch (error) {
    notice = error instanceof api.ApiError ? { code: error.code, message: error.message } : { code: null, message: NETWORK_NOTICE };
  }
  setBusy(false);
  if (dom.dialog.open) dom.dialog.close();
  await refreshNow();
  if (notice === null) {
    toast("Versão aprovada");
  } else {
    state.approvalNotice = notice;
    emit("status");
  }
}

dom.button.addEventListener("click", openDialog);
dom.cancel.addEventListener("click", () => dom.dialog.close());
dom.confirm.addEventListener("click", () => void confirm());
dom.dialog.addEventListener("keydown", trapFocus);
// Esc não fecha enquanto a aprovação é gravada.
dom.dialog.addEventListener("cancel", (event) => {
  if (busy) event.preventDefault();
});
// Qualquer fechamento (Cancelar, Esc, sucesso, recusa) devolve o foco ao botão de origem.
dom.dialog.addEventListener("close", () => {
  captured = null;
  dom.button.focus();
});

on("status", render);
on("plan", render);
render();
