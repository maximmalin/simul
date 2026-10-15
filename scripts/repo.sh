#!/usr/bin/env bash
# Создание репозитория и публикация проекта без веб-интерфейса.
#
# Проблема, которую снимает: чтобы завести новый репозиторий на Forgejo,
# приходилось открывать GUI. Скрипт делает то же из терминала, используя
# те же учётные данные, что и обычный git push — ничего вводить не нужно.
#
#   ./scripts/repo.sh init krystall-radio "Описание проекта"
#   ./scripts/repo.sh push forgejo main
#   ./scripts/repo.sh list
#
# Учётные данные берутся из ~/.git-credentials — того же файла, которым
# пользуется git. Отдельный API-токен не нужен: Forgejo принимает пароль
# по HTTP Basic и для git-операций, и для API.
set -euo pipefail

HOST_DEFAULT="http://127.0.0.1:3000"
OWNER_DEFAULT="mirrage"
CRED_FILE="${GIT_CREDENTIALS:-$HOME/.git-credentials}"

die() { printf '\033[31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
info(){ printf '  %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Учётные данные
# ---------------------------------------------------------------------------
read_credential() {
  # В ~/.git-credentials хранится «http://пользователь:пароль@хост»,
  # причём двоеточие в хосте записано как %3a. Поэтому ищем по
  # «хост:порт» без схемы, а разбираем всё запись целиком.
  [ -f "$CRED_FILE" ] || die "нет файла учётных данных: $CRED_FILE"

  local hostport="${1#*://}"              # убрать схему
  hostport="${hostport%%/*}"              # убрать путь
  local encoded="${hostport//:/%3a}"      # двоеточие в %3a
  local line
  line="$(grep -F "@$encoded" "$CRED_FILE" | head -1 || true)"
  [ -n "$line" ] || die "в $CRED_FILE нет записи для $hostport"

  local rest="${line#*://}"               # пользователь:пароль@хост
  rest="${rest%@*}"
  CRED_USER="${rest%%:*}"
  CRED_PASS="${rest#*:}"
  [ -n "$CRED_USER" ] && [ -n "$CRED_PASS" ] \
    || die "не удалось разобрать запись учётных данных"
}

api() {
  # api <метод> <путь> [json-тело]
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -m 30 -u "$CRED_USER:$CRED_PASS" \
         -X "$method" -H 'Content-Type: application/json' \
         -d "$body" "$HOST$path"
  else
    curl -sS -m 30 -u "$CRED_USER:$CRED_PASS" -X "$method" "$HOST$path"
  fi
}

# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
cmd_init() {
  local name="${1:-}" desc="${2:-}" private="${3:-false}"
  [ -n "$name" ] || die "нужно имя: repo.sh init <имя> [описание]"

  read_credential "$HOST"

  # уже существует?
  if api GET "/api/v1/repos/$OWNER/$name" | grep -q '"full_name"'; then
    ok "репозиторий $OWNER/$name уже существует — пропускаю создание"
  else
    local payload
    payload=$(printf '{"name":"%s","description":%s,"private":%s,"auto_init":false,"default_branch":"main"}' \
      "$name" "$(printf '%s' "$desc" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
      "$private")
    local reply
    reply="$(api POST "/api/v1/user/repos" "$payload")"
    if printf '%s' "$reply" | grep -q '"full_name"'; then
      ok "создан $OWNER/$name"
    else
      printf '%s\n' "$reply" | head -5 | sed 's/^/  /' >&2
      die "сервер не принял запрос"
    fi
  fi

  # remote
  local url="$HOST/$OWNER/$name.git"
  if git remote get-url forgejo >/dev/null 2>&1; then
    git remote set-url forgejo "$url"
    ok "remote forgejo -> $url"
  else
    git remote add forgejo "$url"
    ok "добавлен remote forgejo -> $url"
  fi

  # README-файл нужен, иначе Forgejo откажется принимать пустой push
  if [ -z "$(git ls-files)" ]; then
    [ -f README.md ] || printf '# %s\n\n%s\n' "$name" "$desc" > README.md
    git add README.md
    git -c user.name="${GIT_USER_NAME:-Developer}" \
        -c user.email="${GIT_USER_EMAIL:-dev@example.com}" \
        commit -q -m "initial commit" || true
    ok "создан начальный коммит"
  fi

  if [ -n "$(git status --porcelain)" ]; then
    info "есть незакоммиченные изменения — коммичу"
    git add -A
    git -c user.name="${GIT_USER_NAME:-Developer}" \
        -c user.email="${GIT_USER_EMAIL:-dev@example.com}" \
        commit -q -m "wip: $(date +%Y-%m-%d\ %H:%M)"
  fi

  BRANCH="$(git branch --show-current || echo main)"
  info "пушу ветку $BRANCH…"
  GIT_TERMINAL_PROMPT=0 git push -u forgejo "$BRANCH"

  # Сервер выставляет дефолтную ветку по первой ветке, которую увидел.
  # Если репозиторий создан с default_branch=main, а запушен master,
  # дефолт молча станет master — и потом клоны будут не туда.
  api PATCH "/api/v1/repos/$OWNER/$name" \
    "{\"default_branch\":\"$BRANCH\"}" >/dev/null || true
  api PATCH "/api/v1/repos/$OWNER/$name/branches/$BRANCH" \
    '{"protected":false}' >/dev/null || true

  actual="$(api GET "/api/v1/repos/$OWNER/$name" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("default_branch",""))' 2>/dev/null || echo "")"
  if [ "$actual" != "$BRANCH" ]; then
    info "предупреждение: дефолтная ветка на сервере — ${actual:-неизвестна}, ожидалась $BRANCH"
  else
    ok "дефолтная ветка на сервере: $actual"
  fi

  ok "опубликовано: $HOST/$OWNER/$name"
}

cmd_push() {
  local remote="${1:-forgejo}" branch="${2:-}"
  [ -n "$branch" ] || branch="$(git branch --show-current || echo main)"
  GIT_TERMINAL_PROMPT=0 git push -u "$remote" "$branch"
}

cmd_list() {
  read_credential "$HOST"
  # разбор в отдельный скрипт: f-строки с кавычками внутри bash heredoc
  # собираются непредсказуемо
  api GET "/api/v1/user/repos?limit=100" \
    | python3 "$(dirname "$0")/repo_list.py"
}

cmd_delete() {
  local name="${1:-}"
  [ -n "$name" ] || die "нужно имя: repo.sh delete <имя>"
  read_credential "$HOST"
  printf '  удалить %s/%s? введите имя для подтверждения: ' "$OWNER" "$name"
  read -r answer
  [ "$answer" = "$name" ] || { info "отменено"; return 0; }
  api DELETE "/api/v1/repos/$OWNER/$name" >/dev/null
  ok "удалён $OWNER/$name"
}

usage() {
  cat <<'EOF'
Создание и публикация репозитория без веб-интерфейса.

  repo.sh init <имя> [описание] [private]   создать репозиторий и запушить
  repo.sh push [remote] [branch]            отправить изменения
  repo.sh list                              что есть на сервере
  repo.sh delete <имя>                      удалить (с подтверждением)

Хост и владелец берутся из переменных окружения:
  REPO_HOST   по умолчанию http://127.0.0.1:3000
  REPO_OWNER  по умолчанию mirrage

Учётные данные — из ~/.git-credentials, отдельный токен не нужен.
EOF
}

# ---------------------------------------------------------------------------
HOST="${REPO_HOST:-$HOST_DEFAULT}"
OWNER="${REPO_OWNER:-$OWNER_DEFAULT}"
CRED_USER=""; CRED_PASS=""

case "${1:-}" in
  init)   shift; cmd_init "$@" ;;
  push)   shift; cmd_push "$@" ;;
  list)   shift; cmd_list "$@" ;;
  delete) shift; cmd_delete "$@" ;;
  *)      usage; [ "${1:-}" = "" ] && exit 0 || exit 1 ;;
esac