"""Constantes únicas do servidor da interface: segurança, sessões, processos, prazos e allowlists.

Fonte única lida por `instance` (CLI), `server`, `sessions`, `state`, `playback` e, na T7, por
`capabilities` (`operations.ui`). Nenhum valor daqui vem de entrada externa; os que o operador ou o
teste podem reduzir (prazo de inatividade, janela de sessão ativa) entram por parâmetro interno com
estes valores como padrão.
"""

from __future__ import annotations

import re


# --- Endereço, identidade e registro da instância ---
HOST = "127.0.0.1"
"""O servidor só escuta em loopback; escutar fora disso exige decisão do humano (spec, Ask first)."""

REGISTRY_NAME = "ui-instance.json"
"""Registro da instância em `.tmp` do trabalho: pid, porta, token e identidade; removido no encerramento."""

LIVE_FILTER_PREFIX = ".live-filter-"
"""Prefixo dos scripts de filtro do render ao vivo em `.tmp` (`render.preview._create_filter_script(..., "live")`).

Cada stream e cada quadro grava o seu e o apaga ao terminar; o `ui stop` apaga os que o encerramento à força deixou.
"""

INSTANCE_HEADER = "X-Editor-Instance"
"""Cabeçalho de toda resposta com a identidade (aleatória, não é o token) da instância.

Permite ao `ui start` e ao `ui stop` provar que a porta registrada é a nossa antes de reaproveitar ou
encerrar um processo, sem autenticação e sem revelar dado do trabalho.
"""

PID_HEADER = "X-Editor-Pid"
"""Cabeçalho de toda resposta com o pid do processo que atende a porta.

O `ui stop` só encerra o pid que o próprio servidor da porta informa e que o registro repete: um registro
forjado com o pid de outro programa e a porta de um servidor real não passa pela sondagem.
"""

START_TIMEOUT_S = 30.0
"""Prazo para o servidor registrar a instância e responder; passado isso o `start` falha com `UI_START_FAILED`."""

STOP_TIMEOUT_S = 10.0
"""Prazo para o servidor deixar de responder depois do encerramento forçado."""

PROBE_TIMEOUT_S = 2.0
"""Prazo da sondagem de identidade da instância registrada."""

# --- Sessão ---
TOKEN_BYTES = 32
"""Entropia do token da URL e do identificador de sessão (suposição 13 e A2)."""

TOKEN_MAX_CHARS = 128
"""Teto do `token` aceito na query de entrada (o token real tem 43 caracteres)."""

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
"""Alfabeto de `secrets.token_urlsafe`."""

COOKIE_PREFIX = "editor_ui_session_"
"""Prefixo do cookie de sessão; a porta é acrescentada (cookies não são isolados por porta)."""

SESSION_LIMIT = 2
"""Sessões de navegador simultâneas por instância."""

SESSION_ACTIVE_S = 120.0
"""Uma sessão é simultânea se fez algum pedido autenticado nesta janela; a ociosa mais antiga é descartada."""

IDLE_TIMEOUT_S = 2 * 60 * 60
"""Sem pedido autenticado por este prazo o servidor encerra sozinho (suposição 9)."""

# --- Processos ---
PROCESS_KINDS = ("stream", "source", "frame")
"""Tipos de FFmpeg por sessão: stream da montagem, stream da fonte inteira (modo Fonte) e extração de quadro.

Há um de cada por sessão; o quadro de scrub da montagem e o da fonte dividem o tipo `frame`, pois a tela só
mostra um modo por vez.
"""

MAX_PROCESSES_PER_SESSION = len(PROCESS_KINDS)
"""No máximo três FFmpeg por sessão; um pedido novo do mesmo tipo encerra o anterior."""

STREAM_FIRST_BYTE_TIMEOUT_S = 30.0
"""Prazo do primeiro byte do stream; sem ele o FFmpeg é encerrado e o pedido responde `PLAYBACK_FAILED`."""

STREAM_STALL_TIMEOUT_S = 30.0
"""Prazo sem o cliente consumir o stream: pausa de mais de 30 s encerra o FFmpeg (F3, passo 2).

É também o prazo de leitura do pedido de um cliente lento (slowloris).
"""

STREAM_CHUNK_BYTES = 64 * 1024
"""Tamanho máximo de cada leitura do FFmpeg repassada ao cliente."""

# --- Entrada HTTP (allowlist da spec) ---
QUERY_MAX_BYTES = 2048
QUERY_VALUE_MAX_CHARS = 32
SOURCE_ID_MAX_CHARS = 64
"""Teto do `source_id` da query do modo Fonte: o padrão de `workdir.validate_source_id` aceita até 64 caracteres."""
BODY_MAX_BYTES = 1024
BODY_DRAIN_MAX_BYTES = 64 * 1024
"""Corpo recusado é lido (até este teto) antes da resposta, para o fechamento da conexão não virar RST."""

DECIMAL_PATTERN = re.compile(r"^[0-9]{1,16}(\.[0-9]{1,12})?$")
"""Decimal finito, sem sinal nem expoente: o formato de `t`, `start` e `end`."""

BODY_VALUE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
"""Valor aceito em campo de corpo JSON: SHA-256 em hexadecimal minúsculo (`plan_sha256`, `sources_fingerprint`)."""

STATIC_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
"""Nome de estático empacotado: sem separador de caminho, então nada sai da pasta `static/`."""

# --- Estado do plano ---
STATE_POLL_INTERVAL_S = 0.25
"""Intervalo da vigia do plano; mudança detectada e estável por dois ciclos é revalidada (no máximo ~1 s)."""

# --- Cabeçalhos de segurança de toda resposta ---
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:; frame-ancestors 'none'"
)
"""CSP restritiva e sem script inline (spec, Headers); HSTS não se aplica a HTTP local."""

REFERRER_POLICY = "no-referrer"
