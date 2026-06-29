#!/usr/bin/env bash
# Deploy services to an environment on this host. Invoked over SSH by CI.
#
# Usage: deploy.sh <env> <owner/repo> <tag> [services]
#   env        — имя окружения (dev/prod/...)
#   owner/repo — префикс образов GHCR (ghcr.io/<owner/repo>/<svc>); имя репо = имя проекта
#   tag        — тег образа
#   services   — опционально: список через запятую/пробел; пусто/"all" = все
#
# Каталог деплоя: /srv/deploy/<project>/<env> — неймспейс по проекту, чтобы на одном
# хосте уживалось несколько проектов. compose-стек изолирован по COMPOSE_PROJECT_NAME.
#
# GHCR-креды из env: GHCR_USER, GHCR_TOKEN. Никаких project-specific значений.
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

echo "deploy [$PROJECT/$ENVIRONMENT] project=$CPN tag=$TAG services='${SERVICES:-all}'"
# shellcheck disable=SC2086
docker compose pull $SERVICES
# shellcheck disable=SC2086
docker compose up -d --wait --wait-timeout 300 $SERVICES
docker compose ps
