# srv-explore → MCP-брокер: ресёрч прайор-арта

Статус: ресёрч под будущую эволюцию srv-explore (не входит в PR #45). Вопрос:
сделать read-only доступ к серверам **capability агента через MCP**, где креды и
enforcement живут server-side, вне досягаемости инженера. Ниже — что уже есть на
рынке и что из этого переиспользовать.

## Вывод в одну строку

Идея не нова — есть три зрелых слоя прайор-арта: (1) infra read-only MCP-серверы
под конкретные ресурсы, (2) MCP-гейтвеи/брокеры с auth+RBAC+audit, (3) спека
Remote MCP на OAuth 2.1. Строить свой брокер с нуля не нужно — нужно **скомпоновать**
существующее и правильно посадить границу на слой ресурса.

## Главный урок (подтверждает нашу же гипотезу)

Каждый «read-only», который жил в промпте / флаге / строковой проверке — **пробит**:

- **Anthropic reference postgres-mcp** — read-only транзакция сбита stacked-SQL
  `COMMIT; DROP SCHEMA public CASCADE;` (драйвер `pg` глотает несколько стейтментов
  за вызов). Сервер задепрекейчен 2025-07. — Datadog Security Labs.
- **CVE-2025-59333** (`executeautomation/mcp-database-server`, CVSS 8.1) — read-only
  охранялся `startsWith("SELECT")`; обходится мульти-стейтментом и `SELECT
  func_that_writes()`. Канонический кейс «строковая проверка — не security-контроль».
- **CVE-2026-46519** (`Flux159/mcp-server-kubernetes`) — read-only фильтр применялся
  на `tools/list`, но не на `tools/call`: write-tool звался по имени. Фикс v3.6.0.
- **Supabase MCP «lethal trifecta»** — агент ходил `service_role` (обход RLS), читал
  недоверенные тикеты, инъекция уводила `integration_tokens` в публичный ответ.

Настоящая граница во всех случаях — **слой ресурса**: read-only роль СУБД, RBAC
ServiceAccount (get/list/watch), платформенный грант, либо билд без write-инструментов.
Ровно тезис, который у нас уже записан в concept: роль БД — фундамент, гард — defense
поверх. Ресёрч это только усиливает.

## Слой 1 — infra read-only MCP-серверы (по ресурсам)

Класс enforcement: **HARD** (модель не обойдёт вводом) / **FLAG** (тумблер прячет
write-tools, обходится если проверка только на discovery) / **ALLOWLIST** (парсинг
на сервере) / **NONE**.

**Базы данных**
- **PlanetScale MCP** — платформенный грант none/read-only/full + эфемерные креды на
  запрос + чтение с реплик. **HARD**, сильнейший в группе. Hosted, OAuth.
- **Supabase MCP** — `--read-only` = запуск SQL под read-only ролью Postgres. **HARD**
  (для DB-tools; management-API вне охвата).
- **crystaldba/postgres-mcp (Pro)** — `--access-mode restricted`: read-only tx +
  таймаут + `pglast`-парсинг, режет `COMMIT`/`ROLLBACK` (закрывает дыру reference-
  сервера). Сильно, но опирается на честность tx, не на роль. ALLOWLIST+tx.
- **Neon MCP** — `x-read-only`: фильтр tools + read-only tx. FLAG+tx.
- **Anthropic reference postgres** — **BROKEN, deprecated**. Не использовать.
- executeautomation/mcp-database-server — **NONE** (см. CVE выше).

**Kubernetes / Docker** — на уровне API никто не enforce'ит; единственный HARD —
дать серверу kubeconfig/ServiceAccount с read-only ClusterRole.
- containers/kubernetes-mcp-server (Red Hat), Azure/aks-mcp (`--access-level
  readonly` по умолчанию + RBAC), GoogleCloudPlatform/kubectl-ai — FLAG(+RBAC).
- Purpose-built read-only: patrickdappollonio/mcp-kubernetes-ro,
  vijaykodam/kubernetes-readonly-mcp.
- **Docker** — read-only режима нет ни у кого (docker socket = ~root); граница =
  какие tools включил. docker/mcp-gateway даёт только per-tool allow/deny.

**Логи / observability** — самый «естественно read-only» слой.
- grafana/loki-mcp, pab1it0/prometheus-mcp-server, elastic/mcp-server-elasticsearch
  — query-only по природе.
- grafana/mcp-grafana — `--disable-write` (FLAG). Datadog official — write-heavy, RO
  нет (ограничивать RBAC/read-only API-ключом). Sentry/Honeycomb — scope-gated
  (`*:read` / `mcp:write` не давать) → read-only по умолчанию.

**SSH / remote-shell** — **категорически НЕ read-only.** Все сервера дают
произвольный exec; максимум — regex-allowlist (classfang/ssh-mcp-server) или права
OS-юзера. То есть для shell-слоя «read-only эксплорер» вынужден **определять свой
allowlist** — это ровно то, что делает наш `guard.py`. Наш подход здесь prior-art-
consistent, но вывод стратегический: shell-поверхность надо **сужать**, а не
защищать парсером.

**Файловая система**
- rust-mcp-stack/rust-mcp-filesystem — read-only by default (**HARD**), лучший RO.
- danielsuguimoto/readonly-filesystem-mcp — форк без write-tools (**HARD**, незрелый).
- Reference filesystem MCP — write-tools есть, RO-флага нет; RO только через OS/`,ro`.
- github/github-mcp-server `--read-only` — в ряде конфигов не ограничивал запись
  (issue #2156). Ещё раз: удаление tools > runtime-флаг.

## Слой 2 — MCP-гейтвеи / брокеры (auth + RBAC + audit + secret-injection)

Это и есть «креды и enforcement server-side, токен клиента вниз не проходит».

- **agentgateway** (solo.io → CNCF-track) — native MCP OAuth 2.1, **tool-level RBAC
  (CEL)**, token exchange, крипто-audit. Ближайшее к «read-only подмножество tools
  на гейтвее».
- **Envoy AI Gateway** (CNCF) — spec-совместимый OAuth 2.0, **per-tool authz, где
  `tools/list` enforce'ит те же правила, что `tools/call`** (неавторизованные tools
  даже не видны) — прямой ответ на класс CVE-2026-46519.
- **IBM ContextForge (mcp-context-forge)** — gateway/registry/proxy: fine-grained
  RBAC, **server-side credential injection**, SSRF-защита, OIDC, audit, OTel.
- **Docker MCP Gateway** — изоляция каждого MCP в контейнере, **secret injection в
  рантайме** (ключи не в env клиента), verify-signatures + курируемый Catalog.
- **Cloudflare MCP Server Portals** — Zero-Trust PEP: Access (SSO/MFA/device posture)
  на identity, трафик в логи/DLP.
- **Teleport MCP Access** — энролл MCP-серверов в кластер, прокси всех запросов,
  `mcp-user` роль + список `mcp.tools`, RBAC + audit. Есть гайд под Vault MCP.
- **Obot / Pomerium / Lasso** — open-source gateways с per-tool политиками.
- **MCP-Scan** (Invariant→Snyk) — не рантайм-PEP, а сканер: tool-poisoning,
  инъекции в описаниях tools, tool-pinning против rug-pull.

## Слой 3 — эфемерный доступ под tool + спека

- **Remote MCP = OAuth 2.1 resource server** (ревизия спеки 2025-06-18): обязательный
  PKCE, audience-bound токены (RFC 8707), **сервер НЕ пробрасывает клиентский токен
  вниз** (защита от confused-deputy). Транспорт — Streamable HTTP (2025-03-26).
- **HashiCorp Vault MCP Server** + **Boundary** — dynamic secrets, эфемерные креды на
  время сессии, auto-revoke. Классический JIT-брокер под backing MCP-tool.
- **Aembit** — workload/agent IAM: секреты брокерятся just-in-time под задачу,
  scoped+time-limited, агент сырой ключ не видит.
- Паттерны: «PEP for MCP / agent gateway», «credential never reaches the client»,
  «JIT access broker for agents», «token exchange / delegated identity» (RFC 8693).

## Что это значит для very-ai-framework

**Не строить бифокальный брокер с нуля.** Композиция:

1. **БД** — не psql-по-SSH, а **read-only роль** (уже есть рецепт) + MCP типа
   Supabase/PlanetScale-модели (грант на роль/платформу), чтение с реплики. HARD-
   граница бесплатно.
2. **Логи/метрики** — observability MCP (Loki/Prometheus/Grafana `--disable-write`,
   Elastic read-only ключ), не `journalctl` по shell.
3. **K8s/docker** (если есть) — read-only ServiceAccount RBAC + профильный MCP.
4. **Shell-остаток** — то, что не покрыто типизированными MCP. Здесь остаётся наш
   `guard.py`-allowlist, но как **можно меньше**: shell — единственная категория без
   настоящего read-only, поэтому цель — сузить её типизированными tools, а не
   расширять парсер.
5. **Обёртка доступа** — при переходе на remote посадить перед MCP гейтвей с
   **per-tool RBAC + audit + secret-injection** (agentgateway / Envoy AI Gateway /
   ContextForge). Тогда: креды server-side, инженер держит только OAuth-токен на
   «спросить», dev — свободно, prod — required-reviewer выдаёт сессионный токен,
   `tools/list` enforce'ит то же, что `tools/call`.

**Развилка A vs B** (из обсуждения) ресёрчем закрыта в пользу **A (типизированные
tools)**: единственные HARD-границы в проде приходят с слоя ресурса; `run_read_command`
с гардом внутри воспроизводит именно тот класс, что ловили CVE. Один узкий escape-hatch
допустим только под audit на гейтвее.

## Референсы под нашу схему (агент-как-сервис на инфре, задача на вход)

Идея «агент живёт на сервере, настроен как мы, на вход задача, наружу findings» —
реализована и зрелыми проектами. Ближайшие:

**Read-only investigation-агенты как сервис на инфре**
- **HolmesGPT** (Robusta, CNCF Sandbox, Apache-2.0, ~2.8k★) — единственный самый
  близкий аналог. AI-агент расследует алерт/инцидент и возвращает root-cause.
  **Operator Mode** = крутится 24/7 in-cluster как сервис. **Read-only by design,
  уважает RBAC**, маркетится как безопасный для прода. 50+ toolset'ов (k8s,
  Prometheus, Grafana, Datadog, Postgres/MySQL/Mongo…), часть — через MCP,
  bring-your-own-model. github.com/HolmesGPT/holmesgpt
- **k8sgpt-operator** (CNCF Sandbox) — in-cluster сервис непрерывной read-only
  диагностики, self-hosted local LLM (air-gap), есть MCP-режим.
  github.com/k8sgpt-ai/k8sgpt-operator
- **kagent** (Solo.io, CNCF Sandbox, ~3k★) — k8s-native рантайм агентов как
  workload'ов; **read-only через per-agent RBAC ServiceAccount**, нативные MCP/A2A.
  Лучший, если строить свой эксплорер на платформе. github.com/kagent-dev/kagent
- **Xata Agent** (~1.1k★) — «AI SRE for PostgreSQL», self-hosted через docker-compose,
  **read-only preset SQL** (никогда не деструктивные команды), findings+Slack.
  Прямой аналог по БД-оси. github.com/xataio/agent

**Фреймворки «агент за одним MCP-tool (задача на вход)»**
- **lastmile-ai/mcp-agent** (~8.4k★) — turnkey: агент-воркфлоу превращается в
  вызываемый MCP-tool (`@app.tool` — sync возвращает результат, async — хэндл задачи).
  github.com/lastmile-ai/mcp-agent
- **evalstate/fast-agent** — `--transport http` отдаёт агента как MCP-сервер, чисто.
- **Claude Agent SDK headless — под наш стек напрямую.** Anthropic **официально
  документирует** запуск SDK как долгоживущего сервиса: паттерн «ephemeral»
  (`TASK_PROMPT` на вход → результат) — буквально задача-in/findings-out; советуют
  **вернуть job-id и поллить**, а не держать HTTP на весь цикл; мульти-тенант
  изоляция (`settingSources:[]`, `CLAUDE_CONFIG_DIR`, per-tenant `cwd`), **egress-
  allowlist**. Субагенты `.claude/agents` — программно через `AgentDefinition`.
  code.claude.com/docs/en/agent-sdk/hosting + cookbook (anthropics/claude-cookbooks).
  OSS-пример: dzhng/claude-agent-server (~579★, WebSocket-обёртка SDK).
- **A2A + IBM ContextForge** — если «послать удалённому агенту задачу» как протокол:
  регистрируешь A2A-агента → авто-создаётся MCP-tool.

**Честный вывод по референсам.** Точную тройку (агент-за-MCP + read-only-гардрейлы +
self-host на целевой инфре) в одном пакете никто не закрывает. По оси «read-only
investigation-агент как сервис» эталон — **HolmesGPT**. По оси «под наш стек» —
**Claude Agent SDK headless hosting**: наш `agent_prompt.md` + `guard.py`
ложатся на него почти как есть, DIY остаётся только обёртка в MCP-tool. Наш вклад
поверх готового — read-only-гард для **shell** (curl/docker/ssh/journalctl), которого у
инфра-ориентированных (k8s/Postgres) агентов нет. И сквозной каветат всех источников:
read-only через парсинг SQL хрупок (референсный Anthropic PG заархивирован за
исполнение `DROP SCHEMA`) — граница обязана быть на роли СУБД.

## Источники

- Datadog: SQL-injection в postgres-mcp — securitylabs.datadoghq.com/articles/mcp-vulnerability-case-study-SQL-injection-in-the-postgresql-mcp-server/
- CVE-2025-59333 — nvd.nist.gov/vuln/detail/CVE-2025-59333
- Supabase «lethal trifecta» — generalanalysis.com/blog/supabase-mcp-blog · supabase.com/blog/defense-in-depth-mcp
- Simon Willison, lethal trifecta — simonwillison.net/tags/lethal-trifecta/
- MCP Authorization spec — modelcontextprotocol.io/specification/draft/basic/authorization
- MCP security best practices — modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- agentgateway — agentgateway.dev · github.com/agentgateway/agentgateway
- Envoy AI Gateway MCP — aigateway.envoyproxy.io/docs/capabilities/mcp/
- IBM ContextForge — github.com/IBM/mcp-context-forge
- Docker MCP Gateway — github.com/docker/mcp-gateway
- Cloudflare MCP Portals — developers.cloudflare.com/cloudflare-one/access-controls/ai-controls/mcp-portals/
- Teleport MCP Access — goteleport.com/docs/enroll-resources/mcp-access/
- crystaldba/postgres-mcp — github.com/crystaldba/postgres-mcp
- PlanetScale MCP — planetscale.com/docs/connect/mcp
- Supabase MCP — github.com/supabase-community/supabase-mcp
- K8s RO bypass CVE — manifold.security/blog/mcp-server-kubernetes-readonly-bypass
- awesome-mcp-gateways — github.com/e2b-dev/awesome-mcp-gateways
