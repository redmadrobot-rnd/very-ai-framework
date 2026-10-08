#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: выкатывает сервисы окружения из GHCR-образов на хосте (docker login → pull → up -d).
#             Без project-specific значений — проект берётся из имени репозитория.
# Вход:    $1 = env (dev/prod/…); $2 = owner/repo (префикс GHCR, имя репо = проект);
#          $3 = tag образа; $4 = (опц.) сервисы через запятую/пробел, пусто/"all" = все.
#          GHCR-креды — из env: GHCR_USER, GHCR_TOKEN.
# Алгоритм:
#   валидация env и имени проекта; каталог /srv/deploy/<project>/<env> (неймспейс по
#   проекту — на одном хосте уживается несколько); COMPOSE_PROJECT_NAME=<project>-<env>
#   (изоляция стеков); docker login ghcr.io → docker compose pull → up -d --wait.
#   После выката ВСЕГО стека на релизную координату (vX… или manual-<hex>) пишет в
#   .deployed "<tag> <DEPLOY_SHA>" — правду о том, что стоит, для гейта релиза и среза
#   хотфикса. Прод целиком — только на такую координату и с полным DEPLOY_SHA.
# Выход:   развёрнутый стек; docker compose ps в лог; ненулевой код при ошибке.
set -euo pipefail

ENVIRONMENT="$1"
REPO="$2"
TAG="$3"
SERVICES="${4:-}"

[ "$SERVICES" = "all" ] && SERVICES=""
SERVICES="${SERVICES//,/ }"
RELEASE_TAG_RE='^(v[0-9A-Za-z._-]+|manual-[0-9a-f]{7,40})$'

if ! [[ "$ENVIRONMENT" =~ ^[a-z][a-z0-9_-]*$ ]]; then
  echo "invalid environment: '$ENVIRONMENT'" >&2
  exit 1
fi

PROJECT="${REPO##*/}"
if ! [[ "$PROJECT" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "invalid project (repo name): '$PROJECT'" >&2
  exit 1
fi

DIR="/srv/deploy/${PROJECT}/${ENVIRONMENT}"

# имя compose-проекта: уникально на проект+окружение и в нижнем регистре с допустимыми
# символами (docker: ^[a-z0-9][a-z0-9_-]*) — иначе стеки разных проектов схлопнутся
CPN="${PROJECT,,}-${ENVIRONMENT}"
CPN="${CPN//[^a-z0-9_-]/-}"
while [[ "$CPN" == [-_]* ]]; do CPN="${CPN#?}"; done  # docker требует старт с [a-z0-9]

mkdir -p "$DIR"
cd "$DIR"

# хэш docker-compose.yml: сменился (или сервисы не заданы) → пересоздаём весь стек,
# иначе только затронутые. Только выкат всего стека описывает окружение одним тегом —
# точечный оставляет стек смешанным и .deployed не трогает.
STACK_SHA="$(sha256sum docker-compose.yml | cut -c1-16)"
FULL=1
if [ -n "$SERVICES" ] && [ "$STACK_SHA" = "$(cat .stack.sha 2>/dev/null || true)" ]; then
  FULL=""
fi

# .deployed прода — точка отсчёта гейта релиза: `latest` коммит не называет, голый тег
# можно сдвинуть. Отказ ДО любого касания стека.
if [ "$ENVIRONMENT" = prod ] && [ -n "$FULL" ]; then
  if ! [[ "$TAG" =~ $RELEASE_TAG_RE ]]; then
    echo "::error::полный выкат прода на '$TAG' не делаем: ждём vX.Y.Z или manual-<sha>. Стек не тронут" >&2
    exit 1
  fi
  if ! [[ "${DEPLOY_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "::error::полный выкат прода без полного DEPLOY_SHA (40 hex) не делаем. Руками: DEPLOY_SHA=\$(git rev-parse '$TAG^{commit}') deploy.sh …. Стек не тронут" >&2
    exit 1
  fi
fi

echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin

export GITHUB_REPOSITORY="$REPO" TAG="$TAG"
export IMAGE_PREFIX="ghcr.io/${REPO,,}"
export COMPOSE_PROJECT_NAME="$CPN"
# какой поднабор сервисов поднимать на окружении (compose profiles); приходит из
# Environment Variable COMPOSE_PROFILES через ssh-action. Пусто = все дефолтные сервисы.
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"

echo "deploy [$PROJECT/$ENVIRONMENT] project=$CPN tag=$TAG services='${SERVICES:-all}'"
if [ -z "$FULL" ]; then
  # shellcheck disable=SC2086
  docker compose --compatibility pull $SERVICES
  # shellcheck disable=SC2086
  docker compose --compatibility up -d --wait --wait-timeout 300 --force-recreate $SERVICES
else
  # весь стек; --remove-orphans убирает контейнеры выпавших из compose/профилей сервисов
  docker compose --compatibility pull
  docker compose --compatibility up -d --wait --wait-timeout 300 --force-recreate --remove-orphans
  echo "$STACK_SHA" > .stack.sha
fi
# Пишется только после успешного up (set -e): упавший выкат оставляет прошлое значение.
if [ -z "$FULL" ]; then
  echo ".deployed не трогаем: выкат не всего стека ($SERVICES)"
elif [[ "$TAG" =~ $RELEASE_TAG_RE ]]; then
  echo "${TAG}${DEPLOY_SHA:+ $DEPLOY_SHA}" > .deployed
else
  echo ".deployed не трогаем: '$TAG' — не релизная координата"
fi
docker compose ps
