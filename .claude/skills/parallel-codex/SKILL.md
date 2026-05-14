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
