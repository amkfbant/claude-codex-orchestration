# Operations Guide

## Quick commands

```bash
.orchestration/bin/task-ledger list
python3 .orchestration/scripts/orch.py poll --verbose
.orchestration/bin/codex-dispatch <task-id>
.orchestration/bin/parallel-codex --max-workers 3
.orchestration/bin/diff-reviewer <task-id>
.orchestration/bin/merge-arbiter --cleanup
.orchestration/bin/resume-session --repair
.orchestration/bin/stuck-detector
```

## Installation into an existing repository

1. Copy this kit into the repository root.
2. Merge `.gitignore` rules instead of overwriting an existing project `.gitignore`.
3. Fill placeholders in `AGENTS.md`.
4. Set validation commands.
5. Compile-check the orchestrator.

```bash
chmod +x .orchestration/scripts/orch.py .orchestration/bin/* scripts/install_orchestration.sh
python3 -m py_compile .orchestration/scripts/orch.py
python3 .orchestration/scripts/orch.py init \
  --install "<install-command>" \
  --lint "<lint-command>" \
  --typecheck "<typecheck-command>" \
  --test "<test-command>" \
  --build "<build-command>"
```

## Sample task creation

Single repo:

```bash
.orchestration/bin/task-ledger new \
  --title "Fix date formatting" \
  --objective "Use UTC formatting in billing export" \
  --acceptance "Existing billing tests pass and new UTC case is covered" \
  --paths "src/billing,tests/billing"
```

Monorepo package:

```bash
.orchestration/bin/task-ledger new \
  --title "Add API user search" \
  --objective "Add search endpoint in packages/api only" \
  --acceptance "API package tests pass and search returns name/email matches" \
  --paths "packages/api/src/users,packages/api/tests/users"
```

Shared-resource task, intentionally serialized:

```bash
.orchestration/bin/task-ledger new \
  --title "Add dependency for API search" \
  --objective "Add approved dependency and update lockfile" \
  --acceptance "Dependency rationale documented and full validation passes" \
  --paths "package.json,pnpm-lock.yaml"
```

Exploratory task with unknown paths must be explicit and will not be parallelized:

```bash
.orchestration/bin/task-ledger new \
  --title "Investigate flaky checkout test" \
  --objective "Find likely root cause without broad source edits" \
  --acceptance "Report includes root cause and next implementation task" \
  --allow-empty-paths
```

## State machine

```text
pending -> assigned -> running -> review -> merged
running -> failed -> assigned
failed -> blocked
blocked -> pending
review -> blocked on merge conflict
review -> failed on validation failure
```

## Protected files

```text
.orchestration/**
.env
.env.*
secrets/**
*.pem
*.key
.git/**
.github/workflows/**
CODEOWNERS
```

## Shared resources that force serialization

```text
package.json
lockfiles
migrations/**
OpenAPI / Swagger schemas
GraphQL / Prisma schemas
CI workflows
repository-wide config
```

## Runtime artifact policy

The root `.gitignore` intentionally ignores volatile runtime artifacts:

```text
.orchestration/cache/*
.orchestration/locks/*
.orchestration/tasks/*/codex.stdout.jsonl
.orchestration/tasks/*/codex.stderr.log
.orchestration/tasks/*/validation.log
.orchestration/tasks/*/merge-validation.log
```

These remain committable when you want durable orchestration state in the repository:

```text
.orchestration/ledger.json
.orchestration/progress.jsonl
.orchestration/merge-queue.json
.orchestration/schemas/**
.orchestration/scripts/**
.orchestration/bin/**
```

## Validation behavior

Validation runs in this order:

```text
install -> lint -> typecheck -> test -> build
```

`install` is skipped only when no install command is configured or the same install command plus dependency inputs were already installed for the same worktree. The stamp is per worktree, not global, because dependency directories are usually worktree-local. After validation, the worktree must remain clean; if install/test commands generate uncommitted repository changes, the task is marked failed instead of silently committing generated artifacts.

## Reviewing a Codex result

```bash
cat .orchestration/tasks/<task-id>/exit.json | jq
cat .orchestration/tasks/<task-id>/codex.final.validation.json | jq
cat .orchestration/tasks/<task-id>/review.json | jq
cat .orchestration/tasks/<task-id>/validation.json | jq
```

`review.json.approved` must be true before merge queue admission unless Claude explicitly accepts medium warnings.

## Merge

```bash
.orchestration/bin/merge-arbiter --cleanup
```

If post-merge validation fails, the script resets to the pre-merge HEAD and marks the task failed.

## Resume after interruption

```bash
.orchestration/bin/resume-session --repair
.orchestration/bin/stuck-detector
python3 .orchestration/scripts/orch.py poll --verbose
```

Read order:

```text
CLAUDE.md
AGENTS.md
.orchestration/ledger.json
.orchestration/progress.jsonl
.orchestration/merge-queue.json
.orchestration/tasks/*/exit.json
.orchestration/tasks/*/review.json
.orchestration/tasks/*/validation.json
git status --short
git worktree list
```

## Troubleshooting FAQ

### Codex changed files but task says no changes

This revision compares against the dispatch `pre_head` and should detect committed and uncommitted changes. Inspect:

```bash
cat .orchestration/tasks/<task-id>/exit.json | jq .pre_head
git -C <worktree> diff --name-only <pre_head>
git -C <worktree> status --porcelain=v1
```

### Validation fails because dependencies are missing

Check the install command and install stamp:

```bash
cat .orchestration/tasks/<task-id>/validation.json | jq '.results[] | select(.name=="install")'
find .orchestration/cache/installed -type f -maxdepth 1 -print
```

### Raw `codex exec` asks for approval

That is intentional. Claude should use:

```bash
.orchestration/bin/codex-dispatch <task-id>
```

Raw `codex exec` is kept in the ask-list to prevent bypassing the orchestration wrapper.

### Worktrees are piling up

```bash
python3 .orchestration/scripts/orch.py cleanup
python3 .orchestration/scripts/orch.py cleanup --failed
git worktree prune
```

### A lock remains after a crash

```bash
.orchestration/bin/resume-session --repair
```

### A task needs CI workflow changes

Do not let a normal Codex task modify `.github/workflows/**`. Create a separate explicitly authorized serialized task, then review manually.

## Design decisions and rejected alternatives

- Prefer `mkdir` atomic locks over `flock` for macOS/Linux portability.
- Prefer `pre_head` based diffing over `git status` so Codex-created commits are reviewed.
- Prefer per-worktree install stamps over global dependency stamps because dependency directories are not safely shared by default.
- Prefer shell `codex exec` wrappers over MCP for this kit because PID, timeout, stdout/stderr, and rollback handling are simpler to audit.

## v2 spec and review quick commands

Create spec:

```bash
.orchestration/bin/spec create <task-id> --kind feature
```

Validate and approve:

```bash
.orchestration/bin/spec validate <task-id>
.orchestration/bin/spec approve <task-id>
```

Dispatch with semantic review:

```bash
.orchestration/bin/codex-dispatch <task-id>
```

Manual semantic review:

```bash
.orchestration/bin/codex-review <task-id>
```

Legacy v1 task:

```bash
.orchestration/bin/codex-dispatch <task-id> --allow-legacy
.orchestration/bin/merge-arbiter --allow-legacy --cleanup
```

Bypass audit checks:

```bash
grep 'spec.bypass\|codex.semantic_review.bypassed' .orchestration/progress.jsonl
```

## v3.0 operations

### Metrics

```bash
.orchestration/bin/stats --format text
.orchestration/bin/stats --format json
.orchestration/bin/stats --format html --output .orchestration/stats.html
.orchestration/bin/stats --since 24h
```

### Disaster recovery

```bash
.orchestration/bin/rebuild-ledger --dry-run
.orchestration/bin/rebuild-ledger --output .orchestration/ledger.rebuilt.json
```

### Narrative session summary

`resume-session` is mechanical repair. `summarize-session` is a narrative summary for Claude/user handoff.

```bash
.orchestration/bin/resume-session --repair
.orchestration/bin/summarize-session --since 7d --max-events 100
```

### Session and lock diagnostics (v3.3)

`manager-lock` / `manager-unlock` are retired in v3.3 and remain only as deprecated no-ops. Per-state-file `fcntl.flock` now guards every ledger / merge-queue / LEARNED.md / progress.jsonl mutation. Use:

```bash
.orchestration/bin/session-list           # active sessions seen in progress.jsonl
.orchestration/bin/lock-status            # which state locks are currently held
.orchestration/bin/stuck-detector \
    --dead-session-minutes 30             # flag running tasks whose session is silent
```

`manager-status` is kept as an alias of `session list` for backward compatibility.

See `PARALLEL_SESSIONS.md` for the full multi-session operational model.

### Audit log

```bash
.orchestration/bin/audit show --since 7d
```

### Test-first dispatch

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
.orchestration/bin/codex-dispatch <task-id> --mode test-first --pause
```

### Lessons

```bash
.orchestration/bin/lesson add --task <task-id> --context "..." --trap "..." --lesson "..."
.orchestration/bin/lesson list
.orchestration/bin/lesson show L-001
```

## v3.0 operations quick commands

Metrics:

```bash
.orchestration/bin/stats --format text
.orchestration/bin/stats --format html --output .orchestration/stats.html
```

Narrative resume:

```bash
.orchestration/bin/summarize-session --since 7d --max-events 200
```

Disaster recovery:

```bash
.orchestration/bin/rebuild-ledger --dry-run
.orchestration/bin/rebuild-ledger --output .orchestration/ledger.rebuilt.json
```

Audit:

```bash
.orchestration/bin/audit show --since 7d
```

Session and lock awareness (v3.3):

```bash
.orchestration/bin/session-list
.orchestration/bin/lock-status
.orchestration/bin/stuck-detector --dead-session-minutes 30
```

`manager-status` remains as an alias of `session list`; `manager-lock` / `manager-unlock` are deprecated no-ops.

Lessons:

```bash
.orchestration/bin/lesson list
.orchestration/bin/lesson add --task <task-id> --context "..." --trap "..." --lesson "..."
```

Test-first dispatch:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
.orchestration/bin/codex-dispatch <task-id> --mode test-first --pause
```

`resume-session` repairs mechanical state; `summarize-session` provides a narrative handoff for humans and Claude.

## v3.0 F10 Codex status and model configuration

Check Codex installation, authentication, project config, user config, inferred effective model, and project trust state:

```bash
.orchestration/bin/codex-status --suggest
.orchestration/bin/codex-status --format json | jq
python3 .orchestration/scripts/orch.py init-detect --json --dry-run | jq '.codex_preconditions'
```

Project-scoped defaults belong in `.codex/config.toml`. This kit never edits `~/.codex/config.toml`.

Before relying on `.codex/config.toml`, run Codex once from the repository root and accept the trust prompt if shown:

```bash
codex
```

## Project-specific extensions

For project-specific customisation that survives `upgrade.sh` (custom skills, `AGENTS.md` conventions, Codex profiles), see `EXTENDING.md` in this directory.

To choose models, see `.orchestration/docs/MODEL_GUIDE.md`. The kit does not hard-code concrete model names.
