// Recarga automática (F5): consulta o estado do servidor e aplica o plano novo mantendo o cursor.
//
// O servidor revalida `plan.json` e as fontes sozinho (até ~1 s de atraso); aqui só se consulta `GET /api/state`
// a cada segundo. Plano com hash ou impressão de fontes novos: recarrega timeline, painel e player, mantém o
// cursor no mesmo tempo (limitado à nova duração), descarta a seleção e avisa por toast. Plano inválido: a
// faixa de estado mostra o erro e a última versão válida continua na tela, marcada como desatualizada.

import * as api from "./api.js";
import * as player from "./player.js";
import * as selection from "./selection.js";
import { emit, state } from "./store.js";
import { toast } from "./status.js";
import { snap } from "./timecode.js";

const POLL_MS = 1000;
const LOADING_POLL_MS = 400; // primeira carga: o servidor ainda valida o plano
const LOST_POLL_MS = 2000;

/** Instala a versão nova do plano: cursor mantido e limitado, seleção descartada, player refeito. */
function loadPlan(plan) {
  const first = state.plan === null;
  state.plan = plan;
  state.cursor = first ? 0 : snap(state.cursor, plan.frame_rate, plan.duration_s);
  if (!first) selection.clearSelection();
  emit("plan");
  player.reloadForPlan();
  if (!first) toast("Plano atualizado");
}

function sameVersion(plan) {
  return (
    state.plan !== null &&
    state.plan.plan_sha256 === plan.plan_sha256 &&
    state.plan.sources_fingerprint === plan.sources_fingerprint
  );
}

/** Aplica uma resposta de `GET /api/state` ao estado da interface. */
function apply(data) {
  const previous = state.status;
  const firstRead = state.workdirName === "";
  state.connectionLost = false;
  state.workdirName = data.workdir_name;
  state.resolutions = data.resolutions;
  if (firstRead) emit("workdir");
  state.status = data.state;
  state.error = data.error;
  const plan = data.plan;
  if (plan !== null) {
    if (!sameVersion(plan)) {
      loadPlan(plan);
    } else {
      // Mesma versão: só os indicadores vivos (selo de proxy) podem ter mudado.
      state.plan = plan;
      if (previous === "outdated" && data.state === "ready") toast("Plano atualizado");
    }
  }
  emit("status");
}

/** Falha da consulta: sem rede o servidor caiu; com resposta de erro (sessão descartada) vale a mensagem segura dele. */
function fail(error) {
  if (error instanceof api.ApiError) {
    state.connectionLost = false;
    state.status = state.plan ? "outdated" : "invalid";
    state.error = { code: error.code, message: error.message };
  } else {
    state.connectionLost = true;
  }
  emit("status");
}

async function cycle() {
  let delay = POLL_MS;
  try {
    apply(await api.getState());
    if (state.status === "loading") delay = LOADING_POLL_MS;
  } catch (error) {
    fail(error);
    delay = LOST_POLL_MS;
  }
  setTimeout(cycle, delay);
}

/** Inicia a consulta periódica (a primeira acontece na hora). */
export function startReload() {
  void cycle();
}
