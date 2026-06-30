#!/usr/bin/env bash
#
# Печатает git base ref для affected-фильтра: merge-base текущей ветки с
# дефолтной (origin/<default>). Пусто, если вычислить нельзя — тогда
# affected-envs.sh не фильтрует и возвращает все окружения (безопасный fallback).
#
# Требует полную историю (checkout с fetch-depth: 0). Вынесено из YAML отдельно,
# чтобы шаг можно было прогнать локально, а не только на раннере.
#
#   $1 = имя дефолтной ветки (по умолчанию main)
set -uo pipefail

default="${1:-main}"
git fetch --no-tags --depth=100 origin "$default" >/dev/null 2>&1 || true
git merge-base "origin/$default" HEAD 2>/dev/null || true
