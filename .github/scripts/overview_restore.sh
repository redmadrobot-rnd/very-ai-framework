#!/usr/bin/env bash
# Подтягивает в воркспейс ВСЕ прошлые выходы агента с ветки `overview` (если она есть):
# site/{index,tech,changelog}.html и architecture/*.puml. Агент правит их инкрементально
# (дописывает релиз в changelog.html, обновляет страницы, сверяет .puml на дрейф), а не с нуля.
# Входы человека (overview.rules.md, template/) приходят с тега и здесь не перетираются —
# на ветке `overview` их нет. Первый релиз (ветки ещё нет) — пропуск, агент стартует с чистого листа.
set -euo pipefail

if ! git ls-remote --exit-code --heads origin overview >/dev/null 2>&1; then
  echo "overview: ветки ещё нет — первый релиз, baseline пуст"
  exit 0
fi

git fetch --quiet --depth 1 origin overview
mkdir -p .github/overview/site .github/overview/architecture
git --work-tree=. checkout origin/overview -- .github/overview 2>/dev/null || true
echo "overview: baseline восстановлен с ветки overview"
ls -R .github/overview 2>/dev/null || true
