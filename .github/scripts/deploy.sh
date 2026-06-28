#!/usr/bin/env bash
# Deploy services to an environment on this host. Invoked over SSH by CI.
#
# Usage: deploy.sh <env> <owner/repo> <tag> [services]
#   env        — имя окружения (каталог /srv/deploy/<env>, изоляция compose-проекта)
#   owner/repo — префикс образов GHCR (ghcr.io/<owner/repo>/<svc>)
#   tag        — тег образа
#   services   — опционально: список через запятую/пробел; пусто/"all" = все
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

DIR="/srv/deploy/${ENVIRONMENT}"

echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin

mkdir -p "$DIR"
cd "$DIR"
export GITHUB_REPOSITORY="$REPO" TAG="$TAG"
export IMAGE_PREFIX="ghcr.io/${REPO,,}"
export COMPOSE_PROJECT_NAME="$ENVIRONMENT"

echo "deploy [$ENVIRONMENT] tag=$TAG services='${SERVICES:-all}'"
# shellcheck disable=SC2086
docker compose pull $SERVICES
# shellcheck disable=SC2086
docker compose up -d --wait --wait-timeout 300 $SERVICES
docker compose ps
