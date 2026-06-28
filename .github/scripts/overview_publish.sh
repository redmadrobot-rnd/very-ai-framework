#!/usr/bin/env bash
# Публикует сгенерированные агентом overview-артефакты на orphan-ветку `overview`.
# Запускается в CI ПОСЛЕ работы агента. Работает в отдельном клоне, не трогая build-воркспейс
# и НЕ касаясь main (правило фреймворка: в main напрямую не коммитим).
#
# На ветке `overview` лежат ТОЛЬКО выходы агента: site/{index,tech,changelog}.html и
# architecture/*.puml (source-of-truth для дрейфа). Входы человека (overview.rules.md, template/)
# остаются на main. Каждый релиз = один коммит + тег `overview/<tag>` для извлечения снапшота.
set -euo pipefail

TAG="${1:-manual}"
SRC="$(pwd)/.github/overview"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY required}"
TOKEN="${GITHUB_TOKEN:?GITHUB_TOKEN required}"
HOST="${GITHUB_SERVER_URL:-https://github.com}"; HOST="${HOST#https://}"
REMOTE="https://x-access-token:${TOKEN}@${HOST}/${REPO}.git"

WT="$(mktemp -d)"
git clone --quiet "$REMOTE" "$WT"
cd "$WT"
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

if git ls-remote --exit-code --heads origin overview >/dev/null 2>&1; then
  git checkout --quiet overview
else
  git checkout --quiet --orphan overview
  git rm -rf --quiet . 2>/dev/null || true
fi

rm -rf .github/overview
mkdir -p .github/overview/site .github/overview/architecture
for f in index.html tech.html changelog.html; do
  [ -f "$SRC/site/$f" ] && cp "$SRC/site/$f" .github/overview/site/
done
[ -d "$SRC/architecture" ] && cp "$SRC"/architecture/*.puml .github/overview/architecture/ 2>/dev/null || true

git add -A .github/overview
if git diff --cached --quiet; then
  echo "overview: нет изменений, коммит не нужен"
else
  git commit --quiet -m "overview: $TAG"
  git push --quiet origin overview
  git tag -f "overview/$TAG"
  git push --quiet -f origin "overview/$TAG"
  echo "overview: запушено на ветку overview + тег overview/$TAG"
fi
