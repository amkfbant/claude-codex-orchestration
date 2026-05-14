---
name: spec-author
description: Create, update, validate, and approve per-task spec.md files before Codex implementation. Use when a task needs requirements, acceptance criteria, input/output contracts, behavior rules, out-of-scope constraints, and expected test cases written under .orchestration/tasks/<task-id>/spec.md.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/spec *) Bash(python3 .orchestration/scripts/orch.py spec *) Bash(.orchestration/bin/task-ledger *)
---

# spec-author

Use this skill before dispatching any implementation task to Codex.

## Create a spec

```bash
.orchestration/bin/spec create <task-id> --kind feature
```

Edit `.orchestration/tasks/<task-id>/spec.md` as Claude manager. Codex must treat it as read-only.

## Validate

```bash
.orchestration/bin/spec validate <task-id>
```

## Approve

```bash
.orchestration/bin/spec approve <task-id>
```

A task should normally move `spec_draft -> spec_approved -> assigned/running`.

## Kind rules

- `feature`, `bugfix`, `behavior`, `api`, `security`, `performance`: tests required.
- `refactor`, `docs`, `config`: tests may be omitted only if the spec explicitly declares that no behavior changes.
