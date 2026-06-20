---
name: dev-flow
description: >
  Feature/fix development procedure for this framework: proposal → research → technical
  concept → tasks (/goal) → worktree → implementation (human-in-the-loop) →
  pre-commit/CI/PR → review → deploy. The skill is about HOW the agent interacts with the
  user at each stage. Use it when a feature/fix starts or someone asks "how do we develop here".
---

# dev-flow — how the agent runs a feature together with the user

One feature run takes from an hour to a day (3–4h expected). Work happens in an isolated
worktree, the design is markdown, and only green code ships. Below: where the agent
**asks**, where it **silently gathers context**, and where it **stops and waits for a human**.

## 1. Worktree — entering the pipe, first thing
As soon as the agent realizes a feature/fix per this procedure is coming, work moves
into an isolated worktree — and **continues only there**. The worktree isn't a step before
coding, it's the container for the whole run: proposal, research, design, task breakdown
and implementation all happen inside it. This gives clean parallelism between features and
makes rollback = deleting the worktree. `main` is never touched at any stage.

**First action of the run — be in a worktree.** Before any proposal/research/code, check:

```bash
[ "$(git rev-parse --absolute-git-dir)" != "$(cd "$(git rev-parse --git-common-dir)" && pwd)" ]
```

True → already in a worktree, proceed. False → call **`EnterWorktree`** (new branch under
`.claude/worktrees/<feature>`, session CWD moves there) and continue there. Either way, the
rest of the run happens inside the worktree.

At the end of the run the worktree is closed via `ExitWorktree` (`keep` — leave it, `remove` —
delete it along with the branch).

## 2. Proposal — input from the user
The user gives a free-form proposal/design doc. The agent:
- reads the idea, technical context, constraints and acceptance criteria;
- **does not start coding.** If the overall idea, scope or DoD aren't legible —
  it asks clarifying questions and records the answers.

## 3. Research: gathering context — the agent works silently
The user brings links/access/sources. On its own the agent:
- finds relevant entries in the project documentation;
- studies the codebase;
- collects logs/traces/facts from the environments.
All of it goes into context. Bulky research — **via a subagent** ("do it as a subagent"),
so the main context stays clean. Before a fix — reproduce from facts, don't guess.

## 4. Technical concept — dialogue and recording decisions
The goal is the **minimal viable path**. In dialogue with the user the agent:
- clarifies the non-obvious/unstated parts of the original task (asks, doesn't assume);
- proposes best practices, designs pipelines / data schemas / protocols;
- compares with alternatives and **justifies** the engineering decisions made.
**The output is agreed with the user:** the stack is fixed, the HLD is described.

## 5. Tasks and /goal — the agent writes the plan, the human accepts it
The accumulated context (HLD + stack + decisions) is sliced into tasks per the
**`task-breakdown`** skill: self-contained units, written "by an agent for agents", with a
mandatory **DoD and a list of checks**. The human confirms the breakdown, after which `/goal`
is launched ("implement the tasks, reach each one's DoD").

## 6. Implementation — human-in-the-loop (the main interaction cycle)
Non-deterministic execution of the design's tasks. The cycle: write code → find bugs →
update tasks/specs → e2e → try it locally → performance. Interaction rules:
- the agent goes in **iterations**, the human evaluates, steers and corrects along the way;
- the agent freely edits code within the worktree and updates the knowledge base;
- **acceptance criteria are validated by the human in the moment** — the final call is theirs;
  actual readiness matters more than formal DoD and green tests;
- the agent **stops and calls the human** when: changes spill beyond the feature scope;
  a fundamental problem surfaces (a return to design is possible); a subtask hasn't moved
  for ~an hour → change the approach instead of hammering the same one.

## 7. pre-commit → CI/PR — automation, the agent only reacts
Locally before a commit: `ruff`, tests, secret scan — **any step fails → don't commit,
fix it**, don't bother the human. Then push → feature CI, PR → tests + auto Codex review.
On Codex comments (severity high/medium) and on the `@claude` tag the agent makes the edits.

## 8. Review and deploy — the human has the final word
With the PR the agent attaches a **short digest of the specs and key engineering decisions**
(for the reviewer to look at: data models and DB entities, indexes, protocols, API endpoints).
Merge — **after human approval**. dev/prod deploy is automatic. After it — the agent updates
the project overview (pages/miro) and the changelog.

## Interaction principles
- Ask **before** starting whatever isn't derivable from the code; silently gather what is.
- Record decisions in writing (HLD, tasks, PR digest) — don't keep them in the chat.
- The human is the last validator of readiness and merge; the agent doesn't declare "done" itself.
