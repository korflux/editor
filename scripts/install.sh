#!/usr/bin/env bash
# Espelho Unix do provisionamento explícito: instala e verifica o editor sem rede implícita.
# Uso: scripts/install.sh --verify | --install | --install-ffmpeg | --install-models | --install-gitleaks | --add-to-path
set -euo pipefail

# Raiz do repositório: o script vive em scripts/, então o pai é a raiz versionada.
# pwd -W devolve forma Windows (A:/...) no Git Bash; sem ele usa pwd -P (Mac/Linux).
if REPO_WIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W 2>/dev/null)"; then
  REPO_ROOT="$REPO_WIN"
else
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
fi
# Normaliza barras para comparar Windows (A:\...) e MSYS (A:/...) sem divergir.
REPO_ROOT="$(printf '%s' "$REPO_ROOT" | tr '\\' '/')"
# Versão pinada do gitleaks exigida pelos gates, espelhando scripts/install.ps1.
GITLEAKS_VERSION="8.30.1"
# Modelos CTranslate2 aceitos, mesma allowlist do instalador Windows.
ALLOWED_MODELS="tiny base small"
# Falha acumulada da verificação, espelhando $script:HasFailures do ps1.
HAS_FAILURES=0
# Interpretador selecionado e runtime do venv, sem shell intermediário.
SELECTED_PYTHON=""
RUNTIME_PYTHON=""
# Destinos operacionais resolvidos, sempre dentro do repositório.
VENV_ABS=""
MODEL_ABS=""
SHIM_ABS=""
# Binário gitleaks validado, reutilizado pelos gates sem nova sonda no PATH.
FOUND_GITLEAKS=""

# Valores padrão das opções, espelhando os defaults do ps1 em sintaxe Unix.
MODE_VERIFY=0
MODE_INSTALL=0
MODE_INSTALL_FFMPEG=0
MODE_INSTALL_MODELS=0
MODE_INSTALL_GITLEAKS=0
MODE_ADD_TO_PATH=0
MODE_SECURITY_GATES=0
MODE_FORCE=0
OPT_VENV_PATH=".venv"
OPT_MODEL_DIR="models"
OPT_SHIM_DIR=".tools/bin"
OPT_PYTHON_PATH=""
OPT_FFMPEG_SOURCE=""
OPT_MODEL_SOURCE=""
OPT_GITLEAKS_SOURCE=""

# Imprime o uso sem executar nenhuma ação, para falha fechada em argumento inválido.
print_usage() {
  cat <<'USAGE'
Uso: scripts/install.sh --verify | --install | --install-ffmpeg | --install-models | --install-gitleaks | --add-to-path [opções]

Opções:
  --verify                 Somente leitura, não cria nem atualiza nada.
  --install                Cria o venv a partir do lock com hashes e instala a CLI editável.
  --install-ffmpeg         Copia ffmpeg/ffprobe de --ffmpeg-source para .tools/ffmpeg.
  --ffmpeg-source DIR      Diretório local com ffmpeg e ffprobe.
  --install-models         Copia tiny|base|small de --model-source para o model dir.
  --model-source DIR       Diretório local de modelos CTranslate2.
  --install-gitleaks       Copia o gitleaks local para .tools/gitleaks.
  --gitleaks-source FILE   Arquivo local do binário gitleaks.
  --add-to-path            Cria o shim Unix e registra no PATH do usuário.
  --venv-path PATH         Caminho do venv dentro do repo (padrão: .venv).
  --model-dir DIR          Diretório de modelos dentro do repo (padrão: models).
  --shim-dir DIR           Diretório do shim dentro do repo (padrão: .tools/bin).
  --python-path PATH       Python 3.11 x64 explícito ou nome no PATH.
  --security-gates         Executa gitleaks e pip-audit junto com --verify.
  --force                  Permite sobrescrever modelos já instalados.
  -h, --help               Mostra esta ajuda.
USAGE
}

# Falha com mensagem segura no stderr, sem stack trace no stdout.
fail() {
  echo "Erro: $1" >&2
  exit 1
}

# Normaliza um caminho para absoluto sem resolver links, usando python quando disponível.
# Justificativa: realpath/readlink -f não existem no Mac padrão; abspath é portátil.
# Normaliza barras para comparar Windows (A:\) e MSYS (A:/) e converte /c/... via cygpath quando existir.
abspath() {
  local input="$1"
  # Converte forma MSYS (/c/...) para Windows (C:/...) antes do python Windows.
  if command -v cygpath >/dev/null 2>&1; then
    case "$input" in
      /[a-zA-Z]/*) input="$(cygpath -m "$input" 2>/dev/null || printf '%s' "$input")" ;;
    esac
  fi
  local out=""
  # python3 pode ser shim da Store no Git Bash; só aceita saída absoluta válida.
  if command -v python3 >/dev/null 2>&1; then
    out="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$input" 2>/dev/null || true)"
    out="$(printf '%s' "$out" | tr -d '\r' | tr '\\' '/')"
    case "$out" in
      /*|[A-Za-z]:/*) printf '%s' "$out"; return 0 ;;
    esac
    out=""
  fi
  if command -v python >/dev/null 2>&1; then
    out="$(python -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$input" 2>/dev/null || true)"
    out="$(printf '%s' "$out" | tr -d '\r' | tr '\\' '/')"
    case "$out" in
      /*|[A-Za-z]:/*) printf '%s' "$out"; return 0 ;;
    esac
    out=""
  fi
  if command -v cygpath >/dev/null 2>&1; then
    out="$(cygpath -m "$input" 2>/dev/null || true)"
    out="$(printf '%s' "$out" | tr -d '\r')"
    case "$out" in
      /*|[A-Za-z]:/*) printf '%s' "$out"; return 0 ;;
    esac
  fi
  # Fallback sem python: prefixa a raiz quando relativo; não normaliza .., mas mantém falha fechada.
  case "$input" in
    /*|[A-Za-z]:/*|[A-Za-z]:\\*) printf '%s' "$input" | tr '\\' '/' ;;
    *) printf '%s' "$REPO_ROOT/$input" ;;
  esac
}

# Converte um destino operacional para absoluto dentro do repo e rejeita escape e links.
# Justificativa: impede que --venv-path/--model-dir/--shim-dir escapem do repo via .. ou symlink.
resolve_repo_path() {
  local input="$1"
  local candidate
  # Absoluto Unix (/...), Windows (A:/... ou A:\...) ou relativo ao repo.
  case "$input" in
    /*|[A-Za-z]:/*|[A-Za-z]:\\*) candidate="$(abspath "$input")" ;;
    *) candidate="$(abspath "$REPO_ROOT/$input")" ;;
  esac
  case "$candidate" in
    "$REPO_ROOT"|"$REPO_ROOT"/*) ;;
    *) echo "O caminho deve permanecer dentro do repositório: $input" >&2; return 1 ;;
  esac
  # Rejeita qualquer componente linkado até a raiz, como Test-RegularPathChain do ps1.
  local cur="$candidate"
  while :; do
    if [ -L "$cur" ]; then
      echo "O caminho contém um componente linkado ou inválido: $input" >&2
      return 1
    fi
    if [ "$cur" = "$REPO_ROOT" ]; then
      break
    fi
    # Para quando subir além da raiz (caminho já validado pelo prefixo acima).
    case "$cur" in
      "$REPO_ROOT"/*) cur="$(dirname "$cur")" ;;
      *) break ;;
    esac
  done
  if [ -L "$candidate" ]; then
    echo "Caminho linkado ou inválido: $input" >&2
    return 1
  fi
  printf '%s' "$candidate"
}

# Valida uma origem local existente sem permitir links, espelhando Resolve-LocalPath do ps1.
resolve_local_path() {
  local input="$1"
  if [ ! -e "$input" ]; then
    echo "Origem local não encontrada: $input" >&2
    return 1
  fi
  # Rejeita o próprio link e ancestrais linkados, sem exigir que esteja dentro do repo.
  local cur="$input"
  cur="$(abspath "$cur")"
  while :; do
    if [ -L "$cur" ]; then
      echo "Origem local linkada ou inválida: $input" >&2
      return 1
    fi
    local parent
    parent="$(dirname "$cur")"
    if [ "$parent" = "$cur" ]; then
      break
    fi
    cur="$parent"
  done
  if [ -L "$input" ]; then
    echo "Origem local linkada ou inválida: $input" >&2
    return 1
  fi
  abspath "$input"
}

# Resolve um executável pelo PATH ou por caminho literal, sem shell intermediário.
resolve_executable() {
  local name="$1"
  local found
  found="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$found" ]; then
    printf '%s' "$found"
    return 0
  fi
  if [ -f "$name" ] && [ ! -L "$name" ]; then
    abspath "$name"
    return 0
  fi
  return 1
}

# Registra o estado de uma verificação e acumula falha quando o requisito é obrigatório.
add_check() {
  local name="$1"
  local passed="$2"
  local optional="$3"
  local detail="$4"
  local status
  if [ "$passed" = "1" ]; then
    status="available"
  elif [ "$optional" = "1" ]; then
    status="missing (optional)"
  else
    status="missing"
  fi
  echo "[$status] $name: $detail"
  if [ "$passed" != "1" ] && [ "$optional" != "1" ]; then
    HAS_FAILURES=1
  fi
}

# Seleciona o lock por plataforma sem rede: Windows usa requirements.lock, demais usam o lock do Mac.
# ponytail: lock macOS resolvido para aarch64-apple-darwin, trocar por resolução universal se Intel x64 exigir prova real.
# EDITOR_FAKE_UNAME existe só para prova isolada em teste, sem mudar a detecção real.
get_platform_lock_name() {
  local kernel="${EDITOR_FAKE_UNAME:-}"
  if [ -z "$kernel" ]; then
    kernel="$(uname -s 2>/dev/null || echo unknown)"
  fi
  case "$kernel" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT) printf '%s' "requirements.lock" ;;
    *) printf '%s' "requirements.macos.lock" ;;
  esac
}

# Resolve o caminho absoluto do lock vigente a partir da raiz já validada.
get_platform_lock_path() {
  printf '%s' "$REPO_ROOT/$(get_platform_lock_name)"
}

# Extrai a versão pinada de um pacote do lockfile, espelhando Get-LockedVersion do ps1.
get_locked_version() {
  local lock_text_file="$1"
  local package="$2"
  grep -m 1 -E "^${package}==[^[:space:]\\\\]+" "$lock_text_file" 2>/dev/null | sed -E "s/^${package}==([^[:space:]\\\\]+).*/\\1/" || true
}

# Confirma que o lock da plataforma tem os pacotes críticos pinados e hashes SHA-256.
test_lockfile() {
  local lock_name
  lock_name="$(get_platform_lock_name)"
  local lock_path="$REPO_ROOT/$lock_name"
  if [ ! -f "$lock_path" ] || [ -L "$lock_path" ]; then
    add_check "$lock_name" "0" "0" "Arquivo ausente ou linkado."
    return 1
  fi
  local missing=""
  for pkg in faster-whisper ctranslate2 pip-audit setuptools wheel; do
    if [ -z "$(get_locked_version "$lock_path" "$pkg")" ]; then
      missing="$missing $pkg"
    fi
  done
  local hash_count
  hash_count="$(grep -c -E '^[[:space:]]*--hash=sha256:[0-9a-f]{64}' "$lock_path" 2>/dev/null || true)"
  # grep -c sai com status 1 quando não há match; normaliza para zero sem quebrar set -e.
  hash_count="$(printf '%s' "$hash_count" | tr -d '[:space:]')"
  if [ -z "$hash_count" ]; then
    hash_count=0
  fi
  if [ -z "$missing" ] && [ "$hash_count" -gt 0 ] 2>/dev/null; then
    local pkg_count
    pkg_count="$(grep -c -E '^[a-z0-9][a-z0-9_.-]*==[^[:space:]\\\\]+' "$lock_path" 2>/dev/null || true)"
    pkg_count="$(printf '%s' "$pkg_count" | tr -d '[:space:]')"
    add_check "$lock_name" "1" "0" "$pkg_count pacotes pinados, $hash_count hashes SHA-256"
    return 0
  fi
  add_check "$lock_name" "0" "0" "Pacotes sem pin ou hashes ausentes:$missing"
  return 1
}

# Escolhe o Python alvo: override explícito, venv Unix, venv Windows (Git Bash), ou python3/python do PATH.
select_python() {
  if [ -n "$OPT_PYTHON_PATH" ]; then
    if ! SELECTED_PYTHON="$(resolve_executable "$OPT_PYTHON_PATH")"; then
      SELECTED_PYTHON="$OPT_PYTHON_PATH"
    fi
    return 0
  fi
  if [ -x "$VENV_ABS/bin/python" ] && [ ! -L "$VENV_ABS/bin" ]; then
    SELECTED_PYTHON="$VENV_ABS/bin/python"
    return 0
  fi
  if [ -f "$VENV_ABS/bin/python" ]; then
    SELECTED_PYTHON="$VENV_ABS/bin/python"
    return 0
  fi
  if [ -f "$VENV_ABS/Scripts/python.exe" ]; then
    SELECTED_PYTHON="$VENV_ABS/Scripts/python.exe"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    SELECTED_PYTHON="$(command -v python3)"
    return 0
  fi
  SELECTED_PYTHON="$(command -v python 2>/dev/null || true)"
}

# Executa o Python alvo ou o runtime do venv com argumentos estruturados, sem shell.
invoke_python() {
  local py="$RUNTIME_PYTHON"
  if [ -z "$py" ]; then
    py="$SELECTED_PYTHON"
  fi
  if [ -z "$py" ]; then
    echo "Python não localizado." >&2
    return 1
  fi
  "$py" "$@"
}

# Retorna version, major, minor e bits do interpretador em JSON de linha única.
get_python_info() {
  invoke_python -c 'import json,struct,sys; print(json.dumps({"version": ".".join(map(str, sys.version_info[:3])), "major": sys.version_info.major, "minor": sys.version_info.minor, "bits": struct.calcsize("P") * 8}))' 2>/dev/null || true
}

# Valida o interpretador exigido (3.11 x64) antes de criar ambiente ou instalar dependências.
test_python_target() {
  local info
  info="$(get_python_info)"
  if [ -z "$info" ]; then
    add_check "Python 3.11 x64" "0" "0" "Python 3.11 não foi localizado pelo caminho informado ou PATH."
    return 1
  fi
  local major minor bits version
  major="$(printf '%s' "$info" | sed -E 's/.*"major": ([0-9]+).*/\1/')"
  minor="$(printf '%s' "$info" | sed -E 's/.*"minor": ([0-9]+).*/\1/')"
  bits="$(printf '%s' "$info" | sed -E 's/.*"bits": ([0-9]+).*/\1/')"
  version="$(printf '%s' "$info" | sed -E 's/.*"version": "([^"]+)".*/\1/')"
  if [ "$major" = "3" ] && [ "$minor" = "11" ] && [ "$bits" = "64" ]; then
    add_check "Python 3.11 x64" "1" "0" "$version, $bits bits"
    return 0
  fi
  add_check "Python 3.11 x64" "0" "0" "$version, $bits bits; esperado Python 3.11 x64"
  return 1
}

# Aponta o runtime para o venv existente, aceitando bin/python linkado (padrão Unix) sem aceitar shim dir linkado.
set_runtime_to_venv() {
  if [ -f "$VENV_ABS/bin/python" ]; then
    RUNTIME_PYTHON="$VENV_ABS/bin/python"
    return 0
  fi
  if [ -f "$VENV_ABS/Scripts/python.exe" ]; then
    RUNTIME_PYTHON="$VENV_ABS/Scripts/python.exe"
    return 0
  fi
  return 1
}

# Verifica o venv e confirma que ele usa Python 3.11 x64, sem criar estado.
test_virtualenv() {
  if ! set_runtime_to_venv; then
    add_check "ambiente virtual" "0" "0" "Python não encontrado em $VENV_ABS/bin. Execute --install."
    return 1
  fi
  local info
  info="$(get_python_info)"
  if [ -z "$info" ]; then
    add_check "ambiente virtual" "0" "0" "Não foi possível consultar o interpretador do ambiente."
    return 1
  fi
  local major minor bits version
  major="$(printf '%s' "$info" | sed -E 's/.*"major": ([0-9]+).*/\1/')"
  minor="$(printf '%s' "$info" | sed -E 's/.*"minor": ([0-9]+).*/\1/')"
  bits="$(printf '%s' "$info" | sed -E 's/.*"bits": ([0-9]+).*/\1/')"
  version="$(printf '%s' "$info" | sed -E 's/.*"version": "([^"]+)".*/\1/')"
  if [ "$major" = "3" ] && [ "$minor" = "11" ] && [ "$bits" = "64" ]; then
    add_check "ambiente virtual" "1" "0" "$version, $bits bits"
    return 0
  fi
  add_check "ambiente virtual" "0" "0" "$version, $bits"
  return 1
}

# Confirma ffmpeg e ffprobe executando -version, priorizando a cópia local do repo.
test_ffmpeg() {
  local ffmpeg="" ffprobe=""
  if [ -f "$REPO_ROOT/.tools/ffmpeg/ffmpeg" ] && [ ! -L "$REPO_ROOT/.tools/ffmpeg/ffmpeg" ]; then
    ffmpeg="$REPO_ROOT/.tools/ffmpeg/ffmpeg"
  elif [ -f "$REPO_ROOT/.tools/ffmpeg/ffmpeg.exe" ] && [ ! -L "$REPO_ROOT/.tools/ffmpeg/ffmpeg.exe" ]; then
    ffmpeg="$REPO_ROOT/.tools/ffmpeg/ffmpeg.exe"
  else
    ffmpeg="$(resolve_executable ffmpeg 2>/dev/null || true)"
  fi
  if [ -f "$REPO_ROOT/.tools/ffmpeg/ffprobe" ] && [ ! -L "$REPO_ROOT/.tools/ffmpeg/ffprobe" ]; then
    ffprobe="$REPO_ROOT/.tools/ffmpeg/ffprobe"
  elif [ -f "$REPO_ROOT/.tools/ffmpeg/ffprobe.exe" ] && [ ! -L "$REPO_ROOT/.tools/ffmpeg/ffprobe.exe" ]; then
    ffprobe="$REPO_ROOT/.tools/ffmpeg/ffprobe.exe"
  else
    ffprobe="$(resolve_executable ffprobe 2>/dev/null || true)"
  fi
  if [ -n "$ffmpeg" ] && [ -n "$ffprobe" ] && "$ffmpeg" -version >/dev/null 2>&1 && "$ffprobe" -version >/dev/null 2>&1; then
    add_check "FFmpeg" "1" "0" "ffmpeg e ffprobe executaram -version."
    return 0
  fi
  add_check "FFmpeg" "0" "0" "Instale ou forneça ambos os executáveis localmente."
  return 1
}

# Lista modelos locais válidos (tiny|base|small com arquivos mínimos CTranslate2).
list_valid_models() {
  if [ ! -d "$MODEL_ABS" ] || [ -L "$MODEL_ABS" ]; then
    return 0
  fi
  for name in $ALLOWED_MODELS; do
    local dir="$MODEL_ABS/$name"
    if [ ! -d "$dir" ] || [ -L "$dir" ]; then
      continue
    fi
    if [ ! -f "$dir/config.json" ] || [ ! -f "$dir/model.bin" ] || [ ! -f "$dir/tokenizer.json" ]; then
      continue
    fi
    # vocabulary.* é obrigatório; rejeita filhos linkados como o ps1 faz.
    local bad_link=0
    local vocab_found=0
    for child in "$dir"/vocabulary.*; do
      if [ -e "$child" ] || [ -L "$child" ]; then
        if [ -L "$child" ]; then
          bad_link=1
        else
          vocab_found=1
        fi
      fi
    done
    if [ "$bad_link" = "1" ] || [ "$vocab_found" = "0" ]; then
      continue
    fi
    echo "$name"
  done
}

# Exige ao menos um modelo local válido, sem baixar pesos.
test_models() {
  local names
  names="$(list_valid_models | tr '\n' ' ')"
  names="$(printf '%s' "$names" | sed -E 's/ +$//; s/ /, /g')"
  if [ -n "$names" ]; then
    add_check "modelos Whisper locais" "1" "0" "$names"
    return 0
  fi
  add_check "modelos Whisper locais" "0" "0" "nenhum modelo tiny, base ou small completo foi encontrado"
  return 1
}

# Confirma gitleaks na versão pinada, priorizando a cópia local do repo.
# Guarda o binário em FOUND_GITLEAKS para os gates, sem misturar caminho no stdout da verificação.
test_gitleaks() {
  local exe=""
  if [ -f "$REPO_ROOT/.tools/gitleaks/gitleaks" ] && [ ! -L "$REPO_ROOT/.tools/gitleaks/gitleaks" ]; then
    exe="$REPO_ROOT/.tools/gitleaks/gitleaks"
  elif [ -f "$REPO_ROOT/.tools/gitleaks/gitleaks.exe" ] && [ ! -L "$REPO_ROOT/.tools/gitleaks/gitleaks.exe" ]; then
    exe="$REPO_ROOT/.tools/gitleaks/gitleaks.exe"
  else
    exe="$(resolve_executable gitleaks 2>/dev/null || true)"
  fi
  if [ -n "$exe" ] && "$exe" version 2>/dev/null | grep -q -F "$GITLEAKS_VERSION"; then
    add_check "gitleaks" "1" "0" "gitleaks $GITLEAKS_VERSION está disponível."
    FOUND_GITLEAKS="$exe"
    return 0
  fi
  add_check "gitleaks" "0" "0" "Instale explicitamente gitleaks $GITLEAKS_VERSION ou use --install-gitleaks --gitleaks-source <binário>."
  FOUND_GITLEAKS=""
  return 1
}

# Sonda CUDA como opcional best effort: ausência nunca falha a jornada, CPU segue como baseline.
test_cuda() {
  local count=0
  if command -v nvidia-smi >/dev/null 2>&1 && [ -n "$RUNTIME_PYTHON" ]; then
    count="$("$RUNTIME_PYTHON" -c 'try:
 import ctranslate2
 print(int(ctranslate2.get_cuda_device_count()))
except Exception:
 print(0)' 2>/dev/null || echo 0)"
    count="$(printf '%s' "$count" | tr -d '[:space:]')"
  fi
  if [ "$count" -gt 0 ] 2>/dev/null; then
    add_check "CUDA" "1" "1" "GPU e runtime CTranslate2 validados."
    return 0
  fi
  add_check "CUDA" "0" "1" "CUDA não está completo; CPU continua disponível como baseline."
  return 0
}

# Executa pip check no venv para detectar inconsistência entre versões instaladas.
test_pip_check() {
  if invoke_python -m pip check >/dev/null 2>&1; then
    add_check "pip check" "1" "0" "pip check sem conflitos."
    return 0
  fi
  add_check "pip check" "0" "0" "pip check encontrou conflitos; corrija o ambiente antes dos gates."
  return 1
}

# Confirma pacotes Python pinados (backend e auditor) contra o lock vigente.
test_python_packages() {
  local lock_path="$1"
  local out
  out="$(invoke_python -c 'import importlib.metadata as m, importlib.util, json; specs=[("faster-whisper","faster_whisper"),("ctranslate2","ctranslate2"),("pip-audit","pip_audit")]; out={}; [out.update({d: {"version": m.version(d), "module": importlib.util.find_spec(mod) is not None}}) for d,mod in specs]; print(json.dumps(out))' 2>/dev/null || true)"
  if [ -z "$out" ]; then
    add_check "backend Whisper" "0" "0" "faster-whisper/CTranslate2 não puderam ser consultados no ambiente."
    add_check "pip-audit" "0" "0" "pip-audit não pôde ser consultado no ambiente."
    return 1
  fi
  local ok_backend=1
  for pkg in faster-whisper ctranslate2; do
    local expected
    expected="$(get_locked_version "$lock_path" "$pkg")"
    if ! printf '%s' "$out" | grep -q -F "\"$pkg\": {\"version\": \"$expected\""; then
      ok_backend=0
    fi
    # Exige módulo importável além da versão, como o ps1 faz.
    if ! printf '%s' "$out" | grep -q -F "\"$pkg\":"; then
      ok_backend=0
    fi
  done
  local lock_name
  lock_name="$(basename "$lock_path")"
  if [ "$ok_backend" = "1" ]; then
    add_check "backend Whisper" "1" "0" "faster-whisper e CTranslate2 estão pinados e importáveis."
  else
    add_check "backend Whisper" "0" "0" "Backend ausente ou divergente de $lock_name."
  fi
  local audit_expected
  audit_expected="$(get_locked_version "$lock_path" "pip-audit")"
  if printf '%s' "$out" | grep -q -F "\"pip-audit\": {\"version\": \"$audit_expected\""; then
    add_check "pip-audit" "1" "0" "pip-audit $audit_expected está disponível no venv."
  else
    add_check "pip-audit" "0" "0" "pip-audit ausente ou divergente de $lock_name; execute --install."
  fi
}

# Cria o venv em modo explícito e instala exatamente o lock da plataforma com hashes obrigatórios.
install_python_env() {
  if ! test_python_target; then
    fail "Python 3.11 x64 é obrigatório para --install."
  fi
  local lock_path
  lock_path="$(get_platform_lock_path)"
  if ! test_lockfile >/dev/null; then
    test_lockfile || true
    fail "$(get_platform_lock_name) inválido ou ausente."
  fi
  if [ -e "$VENV_ABS" ] && [ -L "$VENV_ABS" ]; then
    fail "O destino do ambiente virtual é um reparse point ou objeto inválido."
  fi
  if [ ! -d "$VENV_ABS" ]; then
    mkdir -p "$VENV_ABS"
  fi
  if ! VENV_ABS="$(resolve_repo_path "$VENV_ABS")" ; then
    fail "Destino do ambiente virtual fora do repositório."
  fi
  # Descobre o binário do venv após a criação, aceitando layout Unix e Windows.
  local venv_py=""
  if [ -f "$VENV_ABS/bin/python" ]; then
    venv_py="$VENV_ABS/bin/python"
  elif [ -f "$VENV_ABS/Scripts/python.exe" ]; then
    venv_py="$VENV_ABS/Scripts/python.exe"
  fi
  if [ -z "$venv_py" ]; then
    if ! invoke_python -m venv "$VENV_ABS"; then
      fail "Falha ao criar o ambiente virtual."
    fi
    if [ -f "$VENV_ABS/bin/python" ]; then
      venv_py="$VENV_ABS/bin/python"
    elif [ -f "$VENV_ABS/Scripts/python.exe" ]; then
      venv_py="$VENV_ABS/Scripts/python.exe"
    else
      fail "O ambiente virtual não produziu bin/python."
    fi
  fi
  RUNTIME_PYTHON="$venv_py"
  local info
  info="$(get_python_info)"
  local major minor bits
  major="$(printf '%s' "$info" | sed -E 's/.*"major": ([0-9]+).*/\1/')"
  minor="$(printf '%s' "$info" | sed -E 's/.*"minor": ([0-9]+).*/\1/')"
  bits="$(printf '%s' "$info" | sed -E 's/.*"bits": ([0-9]+).*/\1/')"
  if [ "$major" != "3" ] || [ "$minor" != "11" ] || [ "$bits" != "64" ]; then
    fail "O ambiente virtual existente não usa Python 3.11 x64."
  fi
  if ! "$RUNTIME_PYTHON" -m pip install --require-hashes --no-input --disable-pip-version-check --requirement "$lock_path"; then
    fail "Falha ao instalar $(basename "$lock_path"); o ambiente pode estar incompleto."
  fi
  if ! "$RUNTIME_PYTHON" -m pip install --no-deps --no-build-isolation --no-input --disable-pip-version-check --editable "$REPO_ROOT"; then
    fail "Falha ao instalar a CLI em modo editável."
  fi
  test_pip_check
  if [ "$HAS_FAILURES" = "1" ]; then
    fail "O ambiente foi criado, mas pip check não passou."
  fi
  echo "Ambiente Python instalado em $VENV_ABS."
}

# Copia ffmpeg e ffprobe locais para .tools/ffmpeg, sem baixar binários.
install_local_ffmpeg() {
  if [ -z "$OPT_FFMPEG_SOURCE" ]; then
    fail "Informe --ffmpeg-source com um diretório local que contenha ffmpeg e ffprobe."
  fi
  local source
  if ! source="$(resolve_local_path "$OPT_FFMPEG_SOURCE")"; then
    fail "Origem de FFmpeg inválida."
  fi
  if [ ! -d "$source" ]; then
    fail "--ffmpeg-source deve ser um diretório local."
  fi
  local ffmpeg_src="" ffprobe_src=""
  for cand in "$source/ffmpeg" "$source/ffmpeg.exe"; do
    if [ -f "$cand" ] && [ ! -L "$cand" ]; then
      ffmpeg_src="$cand"
      break
    fi
  done
  for cand in "$source/ffprobe" "$source/ffprobe.exe"; do
    if [ -f "$cand" ] && [ ! -L "$cand" ]; then
      ffprobe_src="$cand"
      break
    fi
  done
  if [ -z "$ffmpeg_src" ] || [ -z "$ffprobe_src" ]; then
    fail "A origem local precisa conter ffmpeg e ffprobe regulares."
  fi
  local dest="$REPO_ROOT/.tools/ffmpeg"
  mkdir -p "$dest"
  if ! dest="$(resolve_repo_path "$dest")"; then
    fail "Destino de FFmpeg fora do repositório."
  fi
  for target in "$dest/ffmpeg" "$dest/ffprobe"; do
    if [ -L "$target" ]; then
      fail "O destino de FFmpeg contém um executável linkado."
    fi
  done
  cp -f "$ffmpeg_src" "$dest/ffmpeg"
  cp -f "$ffprobe_src" "$dest/ffprobe"
  chmod +x "$dest/ffmpeg" "$dest/ffprobe"
  echo "FFmpeg instalado localmente em $dest."
}

# Copia o binário gitleaks local exigindo a versão pinada, sem download.
install_local_gitleaks() {
  if [ -z "$OPT_GITLEAKS_SOURCE" ]; then
    fail "Informe --gitleaks-source com o binário gitleaks $GITLEAKS_VERSION."
  fi
  local source
  if ! source="$(resolve_local_path "$OPT_GITLEAKS_SOURCE")"; then
    fail "Origem de gitleaks inválida."
  fi
  if [ -d "$source" ]; then
    fail "--gitleaks-source deve apontar para o arquivo gitleaks."
  fi
  local base
  base="$(basename "$source")"
  if [ "$base" != "gitleaks" ] && [ "$base" != "gitleaks.exe" ]; then
    fail "--gitleaks-source deve apontar para o arquivo gitleaks."
  fi
  if ! "$source" version 2>/dev/null | grep -q -F "$GITLEAKS_VERSION"; then
    fail "O gitleaks fornecido não é a versão $GITLEAKS_VERSION."
  fi
  local dest="$REPO_ROOT/.tools/gitleaks"
  mkdir -p "$dest"
  if ! dest="$(resolve_repo_path "$dest")"; then
    fail "Destino de gitleaks fora do repositório."
  fi
  if [ -L "$dest/gitleaks" ]; then
    fail "O destino de gitleaks contém um executável linkado."
  fi
  cp -f "$source" "$dest/gitleaks"
  chmod +x "$dest/gitleaks"
  echo "gitleaks $GITLEAKS_VERSION instalado localmente em $dest."
}

# Copia somente tiny|base|small válidos para o model dir, sem rede e sem aceitar links.
install_local_models() {
  if [ -z "$OPT_MODEL_SOURCE" ]; then
    fail "Informe --model-source com um diretório local de modelos CTranslate2."
  fi
  local source
  if ! source="$(resolve_local_path "$OPT_MODEL_SOURCE")"; then
    fail "Origem de modelos inválida."
  fi
  if [ ! -d "$source" ]; then
    fail "--model-source deve ser um diretório local."
  fi
  local found=""
  for name in $ALLOWED_MODELS; do
    if [ -d "$source/$name" ] && [ -f "$source/$name/config.json" ] && [ -f "$source/$name/model.bin" ] && [ -f "$source/$name/tokenizer.json" ]; then
      local vocab_ok=0
      for vocab in "$source/$name"/vocabulary.*; do
        if [ -f "$vocab" ] && [ ! -L "$vocab" ]; then
          vocab_ok=1
        fi
      done
      if [ "$vocab_ok" = "1" ]; then
        found="$found $name"
      fi
    fi
  done
  found="$(printf '%s' "$found" | sed -E 's/^ +//')"
  if [ -z "$found" ]; then
    fail "Nenhum modelo local completo foi encontrado na origem."
  fi
  mkdir -p "$MODEL_ABS"
  if ! MODEL_ABS="$(resolve_repo_path "$MODEL_ABS")"; then
    fail "Destino de modelos fora do repositório."
  fi
  for name in $found; do
    local dest="$MODEL_ABS/$name"
    if [ -L "$dest" ]; then
      fail "O destino do modelo $name é um link."
    fi
    if [ -e "$dest" ] && [ "$MODE_FORCE" = "0" ]; then
      fail "O modelo $name já existe; remova-o manualmente antes de reinstalar."
    fi
    mkdir -p "$dest"
    cp -rf "$source/$name/." "$dest/"
  done
  echo "Modelos locais instalados:$found"
}

# Determina o arquivo de perfil do usuário para registro reversível, com override para testes.
user_profile_file() {
  if [ -n "${EDITOR_TEST_PROFILE:-}" ]; then
    printf '%s' "$EDITOR_TEST_PROFILE"
    return 0
  fi
  local home="${HOME:-}"
  if [ -z "$home" ]; then
    printf '%s' "$REPO_ROOT/.tools/bin/.path-hint"
    return 0
  fi
  local kernel="${EDITOR_FAKE_UNAME:-}"
  if [ -z "$kernel" ]; then
    kernel="$(uname -s 2>/dev/null || echo unknown)"
  fi
  if [ "$kernel" = "Darwin" ]; then
    case "${SHELL:-}" in
      *zsh) printf '%s' "$home/.zprofile"; return 0 ;;
    esac
    if [ -f "$home/.zprofile" ]; then
      printf '%s' "$home/.zprofile"
      return 0
    fi
  fi
  printf '%s' "$home/.profile"
}

# Garante o shim dir no PATH do usuário de forma idempotente, sem admin e sem shell intermediário.
ensure_user_path() {
  local shim_dir="$1"
  local profile
  profile="$(user_profile_file)"
  local profile_dir
  profile_dir="$(dirname "$profile")"
  if [ ! -d "$profile_dir" ]; then
    mkdir -p "$profile_dir"
  fi
  if [ ! -f "$profile" ]; then
    : > "$profile"
  fi
  # Linha fixa com caminho absoluto já validado; compara por substring literal para idempotência.
  if ! grep -q -F "$shim_dir" "$profile" 2>/dev/null; then
    printf '\nexport PATH="%s:$PATH"\n' "$shim_dir" >> "$profile"
  fi
  # Sessão atual recebe o diretório uma única vez, sem duplicar entradas.
  case ":$PATH:" in
    *":$shim_dir:"*) ;;
    *) export PATH="$shim_dir:$PATH" ;;
  esac
  echo "Para reverter, remova '$shim_dir' do PATH do usuário em $profile e apague o diretório."
}

# Cria o shim editor com o Python do venv por caminho absoluto e registra o PATH por usuário.
install_shim() {
  local kernel="${EDITOR_FAKE_UNAME:-}"
  if [ -z "$kernel" ]; then
    kernel="$(uname -s 2>/dev/null || echo unknown)"
  fi
  case "$kernel" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      echo "O registro no PATH neste sistema usa scripts/install.ps1 -AddToPath; no Unix use scripts/install.sh." >&2
      exit 1
      ;;
  esac
  local shim_dir="$SHIM_ABS"
  mkdir -p "$shim_dir"
  if ! shim_dir="$(resolve_repo_path "$shim_dir")"; then
    fail "Destino do shim fora do repositório."
  fi
  SHIM_ABS="$shim_dir"
  local venv_py=""
  if [ -f "$VENV_ABS/bin/python" ]; then
    venv_py="$VENV_ABS/bin/python"
  elif [ -f "$VENV_ABS/Scripts/python.exe" ]; then
    venv_py="$VENV_ABS/Scripts/python.exe"
  else
    fail "O ambiente virtual não foi encontrado; execute --install antes de --add-to-path."
  fi
  # Caminho absoluto sem links no diretório do venv; o binário pode ser link (padrão do venv Unix).
  venv_py="$(abspath "$venv_py")"
  local shim_path="$shim_dir/editor"
  if [ -L "$shim_path" ]; then
    fail "O shim existente é um link ou objeto inválido."
  fi
  # Conteúdo fixo com caminho absoluto resolvido; "$@" repassa argumentos sem interpretar entrada externa.
  {
    printf '#!/bin/sh\n'
    printf 'exec "%s" -m editor "$@"\n' "$venv_py"
  } > "$shim_path"
  chmod +x "$shim_path"
  ensure_user_path "$shim_dir"
  echo "Shim registrado em $shim_path; abra um novo terminal e execute editor --help."
}

# Executa os gates de segredo e SCA somente quando solicitados, bloqueando ferramenta ausente.
invoke_security_gates() {
  if ! test_gitleaks; then
    fail "Gate de segredo indisponível: instale gitleaks antes de executá-lo."
  fi
  # test_gitleaks guarda o binário em FOUND_GITLEAKS; exige o binário utilizável.
  local gitleaks="$FOUND_GITLEAKS"
  if [ -z "$gitleaks" ]; then
    fail "Gate de segredo indisponível: instale gitleaks antes de executá-lo."
  fi
  if [ -f "$REPO_ROOT/.tools/gitleaks/gitleaks" ]; then
    gitleaks="$REPO_ROOT/.tools/gitleaks/gitleaks"
  elif [ -f "$REPO_ROOT/.tools/gitleaks/gitleaks.exe" ]; then
    gitleaks="$REPO_ROOT/.tools/gitleaks/gitleaks.exe"
  else
    gitleaks="$(resolve_executable gitleaks 2>/dev/null || true)"
  fi
  if [ -z "$gitleaks" ]; then
    fail "Gate de segredo indisponível: instale gitleaks antes de executá-lo."
  fi
  if ! "$gitleaks" detect --source "$REPO_ROOT" --no-banner; then
    fail "Gate de segredo bloqueado por gitleaks."
  fi
  if [ -z "$RUNTIME_PYTHON" ]; then
    fail "Gate SCA indisponível: ambiente virtual não validado."
  fi
  local lock_path
  lock_path="$(get_platform_lock_path)"
  if ! "$RUNTIME_PYTHON" -m pip_audit -r "$lock_path"; then
    fail "Gate SCA bloqueado por pip-audit."
  fi
  echo "Gates gitleaks e pip-audit concluídos sem bloqueio."
}

# Executa toda a verificação sem criar, atualizar ou baixar qualquer componente.
invoke_verification() {
  local lock_ok=0
  local lock_path
  lock_path="$(get_platform_lock_path)"
  if test_lockfile; then
    lock_ok=1
  fi
  test_virtualenv || true
  test_python_target || true
  if [ "$lock_ok" = "1" ]; then
    test_python_packages "$lock_path" || true
  fi
  test_ffmpeg || true
  test_models || true
  test_gitleaks || true
  test_cuda || true
  if [ -n "$RUNTIME_PYTHON" ]; then
    test_pip_check || true
  fi
  if [ "$MODE_SECURITY_GATES" = "1" ]; then
    invoke_security_gates
  fi
}

# Interpreta os argumentos longos Unix, espelhando os switches do ps1 sem abreviações ambíguas.
parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --verify) MODE_VERIFY=1; shift ;;
      --install) MODE_INSTALL=1; shift ;;
      --install-ffmpeg) MODE_INSTALL_FFMPEG=1; shift ;;
      --install-models) MODE_INSTALL_MODELS=1; shift ;;
      --install-gitleaks) MODE_INSTALL_GITLEAKS=1; shift ;;
      --add-to-path) MODE_ADD_TO_PATH=1; shift ;;
      --security-gates) MODE_SECURITY_GATES=1; shift ;;
      --force) MODE_FORCE=1; shift ;;
      --venv-path)
        [ $# -ge 2 ] || fail "Falta valor para --venv-path."
        OPT_VENV_PATH="$2"; shift 2 ;;
      --model-dir)
        [ $# -ge 2 ] || fail "Falta valor para --model-dir."
        OPT_MODEL_DIR="$2"; shift 2 ;;
      --shim-dir)
        [ $# -ge 2 ] || fail "Falta valor para --shim-dir."
        OPT_SHIM_DIR="$2"; shift 2 ;;
      --python-path)
        [ $# -ge 2 ] || fail "Falta valor para --python-path."
        OPT_PYTHON_PATH="$2"; shift 2 ;;
      --ffmpeg-source)
        [ $# -ge 2 ] || fail "Falta valor para --ffmpeg-source."
        OPT_FFMPEG_SOURCE="$2"; shift 2 ;;
      --model-source)
        [ $# -ge 2 ] || fail "Falta valor para --model-source."
        OPT_MODEL_SOURCE="$2"; shift 2 ;;
      --gitleaks-source)
        [ $# -ge 2 ] || fail "Falta valor para --gitleaks-source."
        OPT_GITLEAKS_SOURCE="$2"; shift 2 ;;
      -h|--help) print_usage; exit 0 ;;
      --) shift; break ;;
      -*) fail "Opção desconhecida: $1. Use --help." ;;
      *) fail "Argumento inesperado: $1. Use --help." ;;
    esac
  done
}

# Ponto de entrada: valida modos exclusivos e despacha para verificação ou instalação explícita.
main() {
  parse_args "$@"
  local mode_count=0
  for flag in "$MODE_VERIFY" "$MODE_INSTALL" "$MODE_INSTALL_FFMPEG" "$MODE_INSTALL_MODELS" "$MODE_INSTALL_GITLEAKS" "$MODE_ADD_TO_PATH"; do
    if [ "$flag" = "1" ]; then
      mode_count=$((mode_count + 1))
    fi
  done
  if [ "$mode_count" -eq 0 ]; then
    fail "Nenhuma ação selecionada. Use --verify ou uma opção explícita (--install, --add-to-path ou --install-*)."
  fi
  if [ "$MODE_VERIFY" = "1" ] && [ "$mode_count" -gt 1 ]; then
    fail "--verify não pode ser combinado com opções de instalação."
  fi
  if [ "$MODE_SECURITY_GATES" = "1" ] && [ "$MODE_VERIFY" != "1" ]; then
    fail "--security-gates exige --verify para deixar o modo de execução explícito."
  fi
  if ! VENV_ABS="$(resolve_repo_path "$OPT_VENV_PATH")"; then
    fail "Caminho do venv fora do repositório."
  fi
  if ! MODEL_ABS="$(resolve_repo_path "$OPT_MODEL_DIR")"; then
    fail "Caminho de modelos fora do repositório."
  fi
  if ! SHIM_ABS="$(resolve_repo_path "$OPT_SHIM_DIR")"; then
    fail "Caminho do shim fora do repositório."
  fi
  select_python
  if [ "$MODE_INSTALL" = "1" ]; then
    install_python_env
  fi
  if [ "$MODE_INSTALL_FFMPEG" = "1" ]; then
    install_local_ffmpeg
  fi
  if [ "$MODE_INSTALL_GITLEAKS" = "1" ]; then
    install_local_gitleaks
  fi
  if [ "$MODE_INSTALL_MODELS" = "1" ]; then
    install_local_models
  fi
  if [ "$MODE_ADD_TO_PATH" = "1" ]; then
    install_shim
  fi
  if [ "$MODE_VERIFY" = "1" ]; then
    invoke_verification
  fi
  if [ "$HAS_FAILURES" = "1" ]; then
    exit 1
  fi
}

main "$@"
