// Estado compartilhado da interface e barramento de eventos entre os módulos.
//
// Cada módulo (player, timeline, seleção, painel, recarga) lê e altera `state` e se avisa por eventos, de
// modo que nenhum importe o outro em círculo. Eventos usados: `plan` (plano novo carregado), `status`
// (estado do servidor mudou), `workdir` (nome do trabalho conhecido pela primeira vez), `cursor`, `play`, `res` e
// `selection` (marcas mudaram; `commit` indica o fim do gesto).
// Também guarda as preferências por trabalho no navegador (`localStorage`, sempre com try/catch).

export const state = {
  workdirName: "",
  status: "loading", // loading | ready | outdated | missing | invalid (os rótulos de `GET /api/state`)
  error: null, // { code, message } do plano atual, quando inválido
  connectionLost: false,
  resolutions: [],
  plan: null, // última versão válida do plano (visão pública de `ui/state.py::build_view`)
  cursor: 0, // início do quadro sob o cursor, em segundos da montagem
  playing: false, // intenção de reprodução (inclui o carregamento do primeiro quadro)
  res: "full",
  marks: { start: null, end: null }, // marcas de seleção (podem estar pela metade, só I ou só O)
  selection: null, // { start, end } quando as duas marcas formam um trecho de ao menos um quadro
  panelOpen: true,
};

const listeners = new Map();

/** Registra `handler` para o evento `name`. */
export function on(name, handler) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(handler);
}

/** Dispara o evento `name`; um handler com erro não impede os demais. */
export function emit(name, detail) {
  for (const handler of listeners.get(name) ?? []) {
    try {
      handler(detail);
    } catch (error) {
      console.error(error);
    }
  }
}

/** Taxa de quadros da saída da montagem carregada (25 sem plano, para o timecode não quebrar). */
export function fps() {
  return state.plan?.frame_rate ?? 25;
}

/** Duração da montagem carregada, em segundos (0 sem plano). */
export function duration() {
  return state.plan?.duration_s ?? 0;
}

// --- Preferências por trabalho (nada sensível: resolução, volume e mudo) ---

function storageKey(name) {
  return `editor-ui:${state.workdirName}:${name}`;
}

/** Lê uma preferência; devolve `null` sem armazenamento (janela privada, bloqueio) ou sem valor. */
export function readPref(name) {
  try {
    return window.localStorage.getItem(storageKey(name));
  } catch {
    return null;
  }
}

/** Grava uma preferência; a falha de armazenamento é ignorada (a tela funciona sem ele). */
export function writePref(name, value) {
  try {
    window.localStorage.setItem(storageKey(name), String(value));
  } catch {
    // Sem armazenamento a preferência só vale para esta sessão da página.
  }
}
