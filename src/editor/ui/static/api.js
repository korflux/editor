// Cliente da API local da interface (`ui/server.py`): estado, seleção, quadro de scrub, URL do stream, da
// montagem e (modo Fonte) de cada fonte inteira, e a aprovação da versão exibida.
//
// Todas as rotas exigem o cookie de sessão (`credentials: same-origin`) e respondem no envelope padrão
// `{ ok, data | error: { code, message } }`. O vídeo é consumido direto pelo `<video>` (URL do stream);
// aqui só se monta essa URL, porque o elemento não lê cabeçalhos nem corpo de erro.

import { frameRequestTime, selectionParam } from "./timecode.js";

/** Erro da API com o código público e a mensagem segura do servidor. */
export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/** Lê um erro de resposta: o envelope JSON quando existe, um código genérico senão. */
async function failure(response) {
  let envelope = null;
  try {
    envelope = await response.json();
  } catch {
    // Corpo que não é JSON (queda do servidor, proxy): o status basta.
  }
  const error = envelope?.error;
  return new ApiError(error?.code ?? "REQUEST_FAILED", error?.message ?? "A requisição falhou.", response.status);
}

async function getJson(path, signal) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok) throw await failure(response);
  return (await response.json()).data;
}

/** Estado do trabalho: `{ workdir_name, state, error, resolutions, plan, approval }`. */
export function getState() {
  return getJson("/api/state");
}

/** Trecho da montagem `[start, end]`: segmentos, camadas, legendas e o texto "Copiar para IA". */
export function getSelection(start, end, signal) {
  return getJson(`/api/selection?start=${selectionParam(start)}&end=${selectionParam(end)}`, signal);
}

/** URL do stream MP4 da montagem a partir de `t` (colado na grade pelo servidor) na resolução `res`. */
export function streamUrl(t, res, fps) {
  return `/api/stream?t=${frameRequestTime(t, fps)}&res=${encodeURIComponent(res)}`;
}

/** JPEG do quadro composto de `t` na resolução `res`, como Blob. */
export async function getFrame(t, res, fps, signal) {
  const response = await fetch(`/api/frame?t=${frameRequestTime(t, fps)}&res=${encodeURIComponent(res)}`, {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await failure(response);
  return response.blob();
}

/**
 * Aprova a versão que o operador viu: devolve o hash do plano e a impressão das fontes do carregamento, e o
 * servidor recusa com `APPROVAL_PLAN_CHANGED` se qualquer um deles já não for o do disco. A origem própria que a
 * rota exige é a que o navegador envia sozinho em todo POST do mesmo site. Devolve `{ approved_at, plan_sha256 }`.
 */
export async function approve(planSha256, sourcesFingerprint) {
  const response = await fetch("/api/approve", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_sha256: planSha256, sources_fingerprint: sourcesFingerprint }),
  });
  if (!response.ok) throw await failure(response);
  return (await response.json()).data;
}

// --- Modo Fonte: o mesmo conjunto de chamadas, sobre uma fonte inteira (`source_id` do plano) ---

/** Trecho `[start, end]` da fonte: situação (usado, descartado, parcial), partes usadas e o texto "Copiar para IA". */
export function getSourceSelection(sourceId, start, end, signal) {
  const id = encodeURIComponent(sourceId);
  return getJson(`/api/source/selection?source_id=${id}&start=${selectionParam(start)}&end=${selectionParam(end)}`, signal);
}

/** URL do stream MP4 da fonte inteira a partir de `t` (colado na grade da fonte pelo servidor) na resolução `res`. */
export function sourceStreamUrl(sourceId, t, res, fps) {
  const id = encodeURIComponent(sourceId);
  return `/api/source/stream?source_id=${id}&t=${frameRequestTime(t, fps)}&res=${encodeURIComponent(res)}`;
}

/** JPEG do quadro da fonte de vídeo em `t` na resolução `res`, como Blob. */
export async function getSourceFrame(sourceId, t, res, fps, signal) {
  const id = encodeURIComponent(sourceId);
  const response = await fetch(
    `/api/source/frame?source_id=${id}&t=${frameRequestTime(t, fps)}&res=${encodeURIComponent(res)}`,
    { credentials: "same-origin", cache: "no-store", signal },
  );
  if (!response.ok) throw await failure(response);
  return response.blob();
}
