---
name: setup-framework
description: >
  Roll out the whole very-ai-framework (dev-flow process + GitMark knowledge base + CI/CD) 
  from the template repo into a target project: copy skills/commands into .claude/,
  scaffold the KB, port the GitHub Actions pipeline, and configure environments / secrets /
  deploy. Use to set up the framework in a new or different repository.
---

# Setup framework — deploy all three pillars into a target repo

You're rolling out the **whole framework** from a **template repository** into a **target
repository**. The framework is three nested parts, and this skill installs all of them:

- **dev-flow** (process) — the `dev-flow` + `task-breakdown` skills and the development rules in `CLAUDE.md`.
- **Knowledge Base** (GitMark) — the `kb-search` + `kb-maintain` skills, the `/doc` `/kb-build` `/kb-graph`
  commands, the `gitmark` CLI, and a `docs/gitmark/` KB scaffold.
- **CI/CD** — GitHub Actions workflows + scripts, pre-commit, and the environments / secrets / deploy setup.

- **Template (source of files):** `https://github.com/virrius/very-ai-framework`
- **Target repository:** the project you're currently in.

CI/CD contract: each service is a `services/<name>/` directory with its own `Dockerfile`; service
names and ports are never hardcoded anywhere. Work step by step.

## Step 0. What to clarify with the user (ask BEFORE starting, don't silently assume)

First **study the target repository** and derive everything obvious from it (language, toolchain,
service structure, which env variables the app reads). Ask **only what can't be derived from
the code**: addresses/access, secret values, the Codex/@claude choice.

**Repositories**
- The target repository (URL or confirm "current"). Private? (for self-hosted
  Codex and private images — usually yes).
- The template URL, if it differs from the default.

**Environments and servers**
- Which environments are needed: `dev`, `prod`, `staging`, …?
- Server addresses: for each — `SSH_HOST`, `SSH_USER`.

**Access / credentials**
- A GitHub token with rights: repo, Actions, **secrets**, **environments** (it needs to create
  environments and secrets).
- Whether SSH access to the server already exists and whether a deploy key can be set up (put the
  public one in `authorized_keys`).
- Whether the server has/installs Docker; whether `docker login ghcr.io` is available (private images).

**Codex review**
- Whether it's needed at all. If yes — by which method: **subscription** (a self-hosted runner or
  a ChatGPT account with Codex for `codex login`?) or **API** (`OPENAI_API_KEY`).

**@claude fixes (optional)**
- Whether they're needed. If yes — whether there's a `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`)
  and whether the "Claude" GitHub App is installed on the repo.

**App config** (derived from the repo — ask only the non-obvious)
- Determine the language/toolchain and the set of app env variables **yourself** from the code/configs
  of the target repo — don't ask.
- Ask only what can't be obtained from the repo: **secret values** (passwords, keys,
  tokens) and which variables **differ between environments**.

If any of this is missing — **ask directly**, don't substitute values yourself.

## Step 1. Clone the template

From the root of the **target** repository:

```bash
TEMPLATE=https://github.com/virrius/very-ai-framework   # or the provided URL
git clone --depth 1 "$TEMPLATE" /tmp/aifw-template
```

Everything below copies from `/tmp/aifw-template` into the current (target) repo.

## Step 2. Install the agent tooling (dev-flow + KB skills and commands)

Skills and commands are discovered in `.claude/skills/` and `.claude/commands/`. Copy the
framework's dogfood skills + commands in (the template keeps them under `.claude/`; this
installer skill lives separately in the template's root `skills/` and is **not** part of the copy):

```bash
mkdir -p .claude/skills .claude/commands
cp -r /tmp/aifw-template/.claude/skills/* .claude/skills/
cp /tmp/aifw-template/.claude/commands/*.md .claude/commands/
# tidy: drop any copied bytecode caches (regenerated, gitignored)
find .claude/skills -type d -name __pycache__ -prune -exec rm -rf {} +
```

This carries the GitMark CLI with the `kb-search` skill (`gitmark.py`, `graph.py`, the `gm/`
package). The commands call it by the stable path `.claude/skills/kb-search/gitmark.py`, so no
path patching is needed — it works because the whole `.claude/` tree is copied as-is.

**Alternative — plugin install** (when the template repo is public, or you have SSH set up and a
`.claude-plugin/` manifest): `claude plugin marketplace add virrius/very-ai-framework` then
`claude plugin install`. For a **private** template repo this is currently unreliable (SSH-only,
Windows bugs) — prefer the copy above.

After this the agent should pick up `/doc`, `/kb-build`, `/kb-graph` and the skills (reload the
session if needed).

## Step 3. Install the development rules (dev-flow)

The dev rules live in the template's `CLAUDE.md` (feature branches, pre-commit, services contract,
KB, `@pytest.mark.heavy`, secrets in Environments). Port them into whichever agent-instructions
file the target uses, **without binding to the name**:

- the repo already has `CLAUDE.md` / `AGENTS.md` / another (`.cursorrules`,
  `.github/copilot-instructions.md`, …) — **append** the rules section there, **without
  overwriting** what exists;
- there's no such file — create one: `cp /tmp/aifw-template/CLAUDE.md .`.

## Step 4. Scaffold the Knowledge Base

```bash
mkdir -p docs/gitmark
cp /tmp/aifw-template/docs/gitmark/ontology.md docs/gitmark/   # the ontology spec, reusable as-is
for pat in '.gitmark/' '__pycache__/' '*.pyc'; do grep -qxF "$pat" .gitignore 2>/dev/null || echo "$pat" >> .gitignore; done   # derived index + bytecode — never committed
python3 .claude/skills/kb-search/gitmark.py index              # build the search index
```

`docs/gitmark/` is the KB **source of truth** (the rest of `docs/` is free for non-KB material).
The KB starts almost empty — bootstrap it by running **`/kb-build`** (it surveys the codebase and
generates per-area docs following the ontology), or add docs over time with **`/doc`**.

## Step 5. Port the CI/CD pipeline files + meet the contract

```bash
mkdir -p .github/workflows .github/scripts
cp /tmp/aifw-template/.github/workflows/{feature,pr,codex-command,claude,push-main,release,manual}.yml .github/workflows/
cp /tmp/aifw-template/.github/scripts/{codex_review.py,services.sh,deploy.sh} .github/scripts/
cp /tmp/aifw-template/{.pre-commit-config.yaml,pyproject.toml} .
```

Don't copy the template's `README.md` / `CICD.md` blindly — the target project has its own.

Bring the project to the contract:
- Each service is a `services/<name>/` directory with a `Dockerfile`.
- **`docker-compose.yml`** — the project writes its own, we do **not** copy it from the template.
  It only needs to match the contract: image `ghcr.io/${GITHUB_REPOSITORY}/<svc>:${TAG}`
  and `env_file: .env` (this `.env` is assembled by deploy from Environments — don't commit it by hand).
  A reference example — `docker-compose.example.yml` in the template repo.
- **Dependencies** — declare them in `pyproject.toml` → `[project].dependencies` (PEP 621);
  `pip-audit .` reads them from there, a separate `requirements.txt` isn't needed. The CI test jobs
  install the project editable (`pip install -e .`) so tests import the real package, not a sys.path hack.
- **Test-only deps** (e.g. `httpx` for `fastapi.TestClient`, pytest plugins) → declare a
  `[project.optional-dependencies]` `test = [...]` extra; CI installs it via `pip install -e ".[test]"`.
- Tests: `tests/unit`, `tests/integration`; heavy/slow ones — `@pytest.mark.heavy`.
  No tests yet — that's fine: the test jobs tolerate it (they don't fail on "no tests"),
  but without them the quality gate is weaker — add at least a couple.
- If the project isn't Python — in `feature.yml`/`pr.yml` replace the ruff/pytest/pip-audit steps
  with the project's tooling (the job structure stays).
- Enable pre-commit:
  ```bash
  pip install pre-commit && pre-commit install
  ```
  For the local `secret-scan` hook install `trufflehog`. Or without the hook — run
  `pre-commit run` by hand before a commit (the agent variant).

## Step 6. Configure GitHub

1. **Environments** (Settings → Environments): create `dev` and `prod` (and others as needed).
2. **For each environment:**
   - Variables: `SSH_HOST`, `SSH_USER` (address/user — not secret; shows which server maps to the environment);
   - Secret: `SSH_KEY` — the private deploy key.

   **Generate a deploy key** (a separate pair just for deploy, do NOT reuse a personal one):
   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N "" -C "gh-deploy"   # no passphrase — CI is unattended
   # public one — onto the server (under the SSH_USER user):
   ssh-copy-id -i deploy_key.pub <SSH_USER>@<SSH_HOST>
   #   (or manually add the line from deploy_key.pub to ~/.ssh/authorized_keys)
   # private one — the whole content of the deploy_key file — into the SSH_KEY Secret
   ```
   The local `deploy_key*` files can be deleted afterwards: the private one lives in the Secret
   (it's **write-only**, not readable back — if you need it again, regenerate the pair).
   Different environments should have different keys (isolation).
3. **App config:** a `APP_DOTENV` Variable (a multiline `.env`) per environment;
   secret values — as separate Environment Secrets (e.g. `APP_SECRET`).
4. **(opt.) @claude:** `CLAUDE_CODE_OAUTH_TOKEN` Secret (`claude setup-token`) +
   install the "Claude" GitHub App. `@claude`/`@codex review` are triggered only by repo
   members (a guard on `author_association` in the workflow). **On PUBLIC repos be careful:**
   `@claude` pushes commits, `@codex` runs on self-hosted — don't expose them to forks/outsiders.
5. Enable GitHub Actions; make sure `GITHUB_TOKEN` has `packages: write` (for GHCR).
6. **Branch protection** (Settings → Branches → rule for `main`): require a PR and
   **required status checks** — `unit-tests`, `integration-tests` (they run on PR), so a red
   CI can't be merged. `static`/`security` run on push to `feature` (if you want to require
   them on PR too — add a `pull_request` trigger to them). Don't put `codex-review` in required —
   it's on self-hosted and only on PR open.
   **Important:** on a **private** repo, branch protection / rulesets require a
   **GitHub Pro/Team/Enterprise** plan — on the free plan the API returns 403 "Upgrade to Pro or make
   public". Then it's either a plan upgrade, a public repo, or living without an enforced gate
   (CI still runs and is visible in the PR, the merge just isn't technically blocked).

## Step 7. Codex review: connect it to the runner

Codex review (`pr.yml` → `codex-review`, `codex-command.yml`) runs on a
self-hosted runner with the labels `self-hosted,codex` (auth — via ChatGPT subscription).

- **If such a runner is already deployed** (shared / in another org repository) —
  just make it available to this repo: register it for the repository or for the
  organization with access to it. The workflows are already targeted at `runs-on: [self-hosted, codex]` —
  nothing else is needed.
- **If there's no ready runner** — deploy one once per the template's `CICD.md`
  (`/tmp/aifw-template/CICD.md`, or on GitHub) → "Self-hosted runner for Codex review".
- **No subscription needed** — alternative: rewrite the review onto `openai/codex-action` +
  `OPENAI_API_KEY` (API billing), then a self-hosted runner isn't required.

## Step 8. Deploy server(s)

- Install Docker; make sure `docker compose` is available.
- The public deploy key in `authorized_keys`.
- Directories are created automatically (`/srv/deploy/<env>` in `deploy.sh`).
- For private GHCR images, deploy logs in with `GHCR_USER`/`GHCR_TOKEN` (= `GITHUB_TOKEN`).

## Step 9. Verify the install

- **dev-flow / KB skills** — the agent sees `/doc`, `/kb-build`, `/kb-graph` and the skills
  (reload the session if they don't appear yet).
- **KB** — `python3 .claude/skills/kb-search/gitmark.py lint` is clean and `... gitmark.py stat`
  reports the index.
- **CI/CD** — `pre-commit run --all-files` passes; pushing a `feature/*` branch triggers Feature CI,
  and opening a PR triggers the PR checks.

Report what was installed per pillar and anything that needs a human decision (plan limits for
branch protection, missing server access, Codex method, etc.).
