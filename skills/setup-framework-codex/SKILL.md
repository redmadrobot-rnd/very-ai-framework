---
name: setup-framework-codex
description: >
  Roll out the whole very-ai-framework into a target project for Codex: AGENTS.md,
  repo-local .codex skills/commands, GitMark KB scaffold, CI/CD workflows, Codex PR
  review hooks, and deploy setup. Use when the target repository will be operated by Codex.
---

# Setup framework for Codex

You're rolling out **very-ai-framework** from this template into a **target repository**.
The framework has three pillars:

- **Dev-flow** - agent development procedure and repo rules.
- **Knowledge Base** - GitMark markdown ontology, search, lint, and graph tooling.
- **CI/CD** - GitHub Actions checks, tests, Codex PR review commands, and deploy scripts.

**Template:** `https://github.com/redmadrobot-rnd/very-ai-framework`
**Target repository:** the project you're currently in.

## Step 0. Clarify only what code cannot tell you

First inspect the target repository. Derive language, toolchain, service layout,
Dockerfiles, test commands, and app env variable names from code/configs.

Ask the user only for non-derivable decisions and access:

- target repository confirmation and whether it is private;
- required environments: `dev`, `prod`, `staging`, etc.;
- server coordinates per environment: `SSH_HOST`, `SSH_USER`;
- whether deploy SSH access already exists;
- GitHub token/access with rights for Actions, secrets, variables, and environments;
- Codex review method: self-hosted runner with ChatGPT subscription auth, or API-based rewrite;
- whether optional `@claude` fixing is needed;
- actual secret values and environment-specific config values.

Do not substitute fake server addresses, tokens, or secret values.

## Step 1. Clone the template

From the target repository root:

```bash
TEMPLATE=https://github.com/redmadrobot-rnd/very-ai-framework
TEMPLATE_DIR=$(mktemp -d -t aifw-template.XXXXXX)
git clone --depth 1 "$TEMPLATE" "$TEMPLATE_DIR"
```

## Step 2. Install Codex agent instructions

If the target already has `AGENTS.md`, append a clearly marked
`very-ai-framework` section instead of overwriting local rules. If it does not, copy the
template file:

```bash
cp "$TEMPLATE_DIR/AGENTS.md" AGENTS.md
```

If the target also uses Claude Code, keep or append `CLAUDE.md` separately. Do not make
Codex depend on `CLAUDE.md` as the primary instruction file.

## Step 3. Install repo-local Codex skills and commands

Codex-native assets live under `.codex/` in the target repo. They are copied from the
framework's canonical skill sources and path-patched for `.codex`.

```bash
mkdir -p .codex/skills .codex/commands
cp -R "$TEMPLATE_DIR/.claude/skills/." .codex/skills/
cp "$TEMPLATE_DIR/.claude/commands/"*.md .codex/commands/
find .codex -type f \( -name '*.md' -o -name '*.py' \) \
  -exec perl -0pi -e 's#\.claude/skills#\.codex/skills#g; s#\.claude/commands#\.codex/commands#g; s#CLAUDE\.md#AGENTS.md#g' {} +
```

Keep `.codex/skills/kb-search/gitmark.py` committed with the target repo. It is the
GitMark CLI used by KB search/lint/index commands.

## Step 4. Scaffold the Knowledge Base

```bash
mkdir -p docs/gitmark
cp "$TEMPLATE_DIR/docs/gitmark/README.md" "$TEMPLATE_DIR/docs/gitmark/ontology.md" docs/gitmark/
for pat in '.gitmark/' '__pycache__/' '*.pyc'; do
  grep -qxF "$pat" .gitignore 2>/dev/null || echo "$pat" >> .gitignore
done
```

Use the resolver installed with CI scripts after Step 5:

```bash
bash .github/scripts/gitmark.sh index
bash .github/scripts/gitmark.sh lint
```

The scaffold starts nearly empty. To fill it, run the Codex command instructions in
`.codex/commands/kb-build.md`, then re-run lint and index.

## Step 5. Port CI/CD files

```bash
mkdir -p .github/workflows .github/scripts
cp -R "$TEMPLATE_DIR/.github/workflows/." .github/workflows/
cp -R "$TEMPLATE_DIR/.github/scripts/." .github/scripts/
cp "$TEMPLATE_DIR/.pre-commit-config.yaml" .
```

Do not blindly copy the template `README.md`, `CICD.md`, or `docker-compose.example.yml`
over target project files. Use them as references.

Bring the target repo to the CI/CD contract:

- each service is a `services/<name>/` directory with its own `Dockerfile`;
- `docker-compose.yml` is project-specific and references
  `ghcr.io/${GITHUB_REPOSITORY}/<svc>:${TAG}` plus `env_file: .env`;
- test dependencies are declared in `pyproject.toml` or the target's own package manager;
- if the target is not Python, replace `_checks.yml` and `_tests.yml` internals with the
  native checks while keeping the workflow shape.

## Step 6. Configure GitHub

Create GitHub Environments such as `dev` and `prod`.

For each environment:

- Variables: `SSH_HOST`, `SSH_USER`, optional `APP_DOTENV`, optional `COMPOSE_PROFILES`;
- Secrets: `SSH_KEY`, optional `APP_SECRET`, and any app-specific secrets.

For Codex PR review:

- preferred: a self-hosted GitHub Actions runner labeled `self-hosted,codex`, with
  Codex CLI authenticated by ChatGPT subscription via `codex login --device-auth`;
- alternative: rewrite review workflows to an API-key based action and set
  `OPENAI_API_KEY`.

Do not require `codex-review` as a protected-branch status check unless the repo has a
reliable runner and the team explicitly wants auto-review to be merge-blocking.

## Step 7. Deploy target services

Deploy is meaningful only after the target has real services and GitHub environments.
The framework deploy path is:

1. PR to `main` runs checks and tests.
2. Merge to `main` builds changed service images and deploys `dev`.
3. Tag `v*` promotes pinned images and deploys `prod`.
4. Manual deploy can build/deploy selected services or roll back by image tag.

Server requirements:

- Docker and Docker Compose;
- deploy user's public key in `authorized_keys`;
- access to GHCR for private images.

The deploy directory is `/srv/deploy/<project>/<environment>`.

## Step 8. Verify

Run the smallest checks that prove the install:

```bash
bash .github/scripts/gitmark.sh lint
bash .github/scripts/gitmark.sh index
bash .github/scripts/discover-test-dirs.sh
pre-commit run --all-files
```

Report:

- what was installed for dev-flow, KB, and CI/CD;
- what could not be configured without human-provided access;
- exact remaining decisions: servers, secrets, Codex runner/API mode, branch protection.
