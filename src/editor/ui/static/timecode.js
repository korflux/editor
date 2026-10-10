// Regras de tempo da interface: grade de quadros, timecode e formatação dos tempos enviados à API.
//
// Repete duas regras do servidor, que continua sendo a fonte da verdade:
// - `ui/selection.py::format_timecode`: conta quadros (`floor(t * fps)`) e os escreve na base `round(fps)`;
// - `render/live.py::snap_start`: o instante vira o quadro que o contém, limitado ao último quadro.
// O cursor da interface é sempre o início de um quadro (`k / fps`); os pedidos de quadro e de stream
// levam o centro do quadro para que o arredondamento a 6 casas nunca caia no quadro vizinho.

const FRAME_EPSILON = 1e-9; // mesma folga de `snap_start`
const TIMECODE_EPSILON = 1e-6; // mesma folga de `format_timecode`
const MICRO = 1e6;

/** Índice do quadro que está na tela em `seconds` (sem limite superior). */
export function frameIndex(seconds, fps) {
  return Math.floor(seconds * fps + FRAME_EPSILON);
}

/** Índice do último quadro da montagem: o maior `k` com `k / fps` antes da duração. */
export function lastFrameIndex(duration, fps) {
  return Math.max(0, Math.ceil(duration * fps - FRAME_EPSILON) - 1);
}

/** Cola `seconds` no início do quadro que o contém, dentro de `[0, último quadro]`. */
export function snap(seconds, fps, duration) {
  const frame = Math.min(lastFrameIndex(duration, fps), Math.max(0, frameIndex(seconds, fps)));
  return frame / fps;
}

/** Como `snap`, mas tolera o arredondamento a 6 casas dos tempos do plano (início de segmento a 29,97 fps). */
export function snapBoundary(seconds, fps, duration) {
  return snap(seconds + 1e-3 / fps, fps, duration);
}

/** Timecode `HH:MM:SS:FF` do quadro em `seconds`, na taxa de saída (a mesma regra do servidor). */
export function timecode(seconds, fps) {
  const base = Math.max(1, Math.round(fps));
  const frames = Math.max(0, Math.floor(seconds * fps + TIMECODE_EPSILON));
  const totalSeconds = Math.floor(frames / base);
  const frame = frames % base;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(Math.floor(totalMinutes / 60))}:${pad(totalMinutes % 60)}:${pad(totalSeconds % 60)}:${pad(frame)}`;
}

/** Segundos com 3 casas, o formato do texto copiado para a IA (`3.500 s`). */
export function seconds3(value) {
  return `${Number(value).toFixed(3)} s`;
}

/** Instante de pedido de quadro ou stream: o centro do quadro, em decimal aceito pela allowlist do servidor. */
export function frameRequestTime(seconds, fps) {
  return ((frameIndex(seconds, fps) + 0.5) / fps).toFixed(6);
}

/** Tempo de seleção em decimal (6 casas) sem passar da duração: arredonda para baixo, a regra segura para o fim. */
export function selectionParam(seconds) {
  return (Math.floor(Math.max(0, seconds) * MICRO + 1e-3) / MICRO).toFixed(6);
}
