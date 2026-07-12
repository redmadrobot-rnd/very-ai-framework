---
name: task-breakdown
description: >
  Slice the accumulated feature context (proposal + research + HLD + the fixed stack)
  into a task list for the agent: self-contained units written "by an agent for agents",
  with a mandatory DoD and list of checks, explicit dependencies, ready for /goal.
  Use at the dev-flow task-creation stage — after design, before implementation.
---

# task-breakdown — turning context into tasks for /goal

This is the seam between **design** and **implementation**: all the accumulated context
(idea, research, HLD, decisions, stack) is sliced into tasks the agent then works on almost
autonomously via `/goal`. A bad breakdown = the agent loses the thread and overruns scope;
a good one = `/goal` reaches the end on its own. That's why **the human approves the breakdown**.

## Input — don't slice too early
Slicing starts only when the context is complete: **the stack is fixed** and **the HLD is described**.
If something is missing (an unresolved design question, a gap in requirements, an unclear feature
DoD) — the agent **doesn't slice**, it returns to design / asks the user. Whatever isn't asked
here will be guessed inside `/goal`, where it's already more expensive to fix.

## Slicing principle
- **Minimal viable path.** Slice toward the feature's goal, don't breed "for the future" tasks.
- **Self-contained unit.** Each task is doable by the agent in one pass and **within the
  worktree**; if a task doesn't move for ~an hour — it was sliced too coarse/fuzzy.
- **Explicit dependencies and order.** What depends on what is written out, not implied.
- **One slice — one responsibility.** Data model, protocol, endpoint, migration — separate
  tasks where possible, so review and rollback stay targeted.

## Task format — "by an agent for agents"
Tasks are written by an agent for the executor agent, not for a human manager. Each one:
- **Цель** — what and why (1–2 lines).
- **Шаги** — the concrete action plan.
- **Затронутые файлы/каталоги** — where to work.
- **Принятые решения** — a digest from the HLD: why this way, which alternatives were rejected.
- **Вырезки** — code samples, data structures, protocol schemas, if they're fixed.
- **DoD** *(mandatory)* — the acceptance criterion.
- **Список проверок** *(mandatory)* — clear actions that confirm readiness (which tests to run,
  which endpoint to hit, what to see).

Without a DoD and a list of checks a task **doesn't make it** into the breakdown — this isn't
formatting, it's the contract by which `/goal` knows the task is closed.

## Artifact structure
- A small feature — one `.md` with the task list.
- A large one — a **task folder** (one file per task) + the order/dependencies between them.
- Location: `docs/gitmark/plans/<feature>/` — `node_type: plan`.

## User interaction and handoff
1. The agent shows the breakdown **before** launching and states the scope boundaries.
2. The human confirms the task set, DoD and order (or corrects them). The final word on
   boundaries is the human's.
3. `/goal` is launched: "implement everything set in the tasks, reach each one's DoD".
4. During `/goal` the tasks are **live** — the agent appends/refines them (status, nuances found),
   but a scope change or a fundamental rework = come back here and re-approve with the human.

## Antipatterns
- Slicing before the context is complete (stack/HLD not fixed).
- A wall-of-text task covering half the feature — it can't be done in one pass or rolled back targeted.
- A vague DoD ("make it good") or a missing list of checks.
- Hidden dependencies between tasks "in someone's head" instead of in the text.
- Tasks aimed at a human reader instead of the executor agent.
