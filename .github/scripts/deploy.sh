#!/usr/bin/env bash
#
# Когда:   шаг deploy в deploy-dev.yml / manual.yml — исполняется по SSH на хосте окружения.
# Зачем:   выкатить сервисы окружения из GHCR-образов; без project-specific значений.
# Вход:    $1 = env (dev/prod/…); $2 = owner/repo (префикс GHCR, имя репо = проект);
#          $3 = tag образа; $4 = (опц.) сервисы через запятую/пробел, пусто/"all" = все.
#          GHCR-креды — из env: GHCR_USER, GHCR_TOKEN.
# Алгоритм:
#   валидация env и имени проекта; каталог /srv/deploy/<project>/<env> (неймспейс по
#   проекту — на одном хосте уживается несколько); COMPOSE_PROJECT_NAME=<project>-<env>
#   (изоляция стеков); docker login ghcr.io → docker compose pull → up -d --wait.
# Выход:   развёрнутый стек; docker compose ps в лог; ненулевой код при ошибке.
set -euo pipefail

ENVIRONMENT="$1"
REPO="$2"
TAG="$3"
SERVICES="${4:-}"

[ "$SERVICES" = "all" ] && SERVICES=""
SERVICES="${SERVICES//,/ }"

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

echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin

mkdir -p "$DIR"
cd "$DIR"
export GITHUB_REPOSITORY="$REPO" TAG="$TAG"
export IMAGE_PREFIX="ghcr.io/${REPO,,}"
export COMPOSE_PROJECT_NAME="$CPN"
# какой поднабор сервисов поднимать на окружении (compose profiles); приходит из
# Environment Variable COMPOSE_PROFILES через ssh-action. Пусто = все дефолтные сервисы.
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"

echo "deploy [$PROJECT/$ENVIRONMENT] project=$CPN tag=$TAG services='${SERVICES:-all}'"
# shellcheck disable=SC2086
docker compose pull $SERVICES
# shellcheck disable=SC2086
docker compose up -d --wait --wait-timeout 300 $SERVICES
docker compose ps
