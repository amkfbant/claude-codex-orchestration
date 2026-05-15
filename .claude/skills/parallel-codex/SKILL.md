---
name: parallel-codex
description: Run multiple independent Codex tasks in parallel only after dependency and touched-path conflict checks, each in its own git worktree, with timeout and polling.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/parallel-codex *) Bash(python3 .orchestration/scripts/orch.py parallel *) Bash(python3 .orchestration/scripts/orch.py select-parallel *)
---

# parallel-codex

Use this skill when several pending tasks may be run concurrently.

## Procedure

1. Preview safe candidates:
   ```bash
   python3 .orchestration/scripts/orch.py select-parallel --max-workers 3
   ```
2. Confirm that no candidate touches shared resources.
3. Run:
   ```bash
   .orchestration/bin/parallel-codex --max-workers 3
   ```
4. Poll:
   ```bash
   python3 .orchestration/scripts/orch.py poll --verbose
   ```

## Rules

- Never parallelize tasks with empty `touched_paths`.
- Never parallelize package manifest, lockfile, migration, OpenAPI, schema, or CI tasks.
- Every task gets its own git worktree.
- Merging remains serial through `merge-arbiter`.
- A single session must drive `merge-arbiter`; never run it from two sessions at once.

## Install cache pre-warm (v3.3)

When dispatching workers manually, pre-warm the install cache once in the main worktree so each worker can skip install:

```bash
.orchestration/bin/codex-dispatch --warm-install-only
for tid in $TASK_IDS; do
  .orchestration/bin/codex-dispatch "$tid" --skip-install &
done
wait
```

This avoids parallel package-manager lock contention (npm/pnpm/yarn) inside each worktree.

## Dispatch capacity

`ledger.settings.max_parallel_dispatches` (default 5) caps how many tasks may be in `running` state at once across all sessions. Raise it deliberately, with awareness of Codex API rate limits — watch `rate_limit_hits` in `stats`.

See `.orchestration/docs/PARALLEL_SESSIONS.md` for the full multi-session model.
