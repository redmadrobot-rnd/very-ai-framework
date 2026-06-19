#!/usr/bin/env bash
# Deploy services to an environment on this host. Invoked over SSH by CI.
#
# Usage: deploy.sh <env> <owner/repo> <tag> [services]
#   env       — dev | prod | ... (каталог /srv/deploy/<env>, свой порт/проект)
#   owner/repo — префикс образов GHCR (ghcr.io/<owner/repo>/<svc>)
#   tag       — тег образа
#   services  — опционально: "api,worker" или "api worker"; пусто/"all" = все
#
# GHCR-креды берутся из env: GHCR_USER, GHCR_TOKEN.
set -euo pipefail

ENVIRONMENT="$1"
REPO="$2"
TAG="$3"
SERVICES="${4:-}"

# нормализуем список сервисов: "all"->все, запятые->пробелы
[ "$SERVICES" = "all" ] && SERVICES=""
SERVICES="${SERVICES//,/ }"

DIR="/srv/deploy/${ENVIRONMENT}"
case "$ENVIRONMENT" in
  prod) API_PORT=8090 ;;
  *) API_PORT=8080 ;;
esac

echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin

mkdir -p "$DIR"
cd "$DIR"
export GITHUB_REPOSITORY="$REPO" TAG="$TAG" API_PORT
export COMPOSE_PROJECT_NAME="afc_${ENVIRONMENT}"

echo "deploy [$ENVIRONMENT] tag=$TAG services='${SERVICES:-all}' api_port=$API_PORT"
# shellcheck disable=SC2086
docker compose pull $SERVICES
# shellcheck disable=SC2086
docker compose up -d $SERVICES
docker compose ps
