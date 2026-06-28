#!/usr/bin/env bash
# Подтягивает в воркспейс прошлые overview-артефакты с ветки `overview` (если она есть),
# чтобы агент имел baseline: предыдущий onepager и .puml для проверки дрейфа архитектуры.
# Первый релиз (ветки ещё нет) — тихо пропускается, агент стартует с чистого листа.
set -euo pipefail

if ! git ls-remote --exit-code --heads origin overview >/dev/null 2>&1; then
  echo "overview: ветки ещё нет — первый релиз, baseline пуст"
  exit 0
fi

git fetch --quiet --depth 1 origin overview
mkdir -p .overview/architecture
git --work-tree=. checkout origin/overview -- .overview 2>/dev/null || true
echo "overview: baseline восстановлен с ветки overview"
ls -R .overview 2>/dev/null || true
