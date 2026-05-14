---
name: merge-arbiter
description: Serialize Codex branches through the merge queue, use git rerere and 3-way merge, run validation gates, tag checkpoints, and rollback failed merges.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/merge-arbiter *) Bash(python3 .orchestration/scripts/orch.py merge-next *) Bash(git status *) Bash(git log *) Bash(git diff *)
---

# merge-arbiter

Use this skill when one or more Codex branches are in `.orchestration/merge-queue.json`.

## Procedure

1. Check queue:
   ```bash
   cat .orchestration/merge-queue.json
   ```
2. Ensure root worktree is clean:
   ```bash
   git status --short
   ```
3. Merge next item:
   ```bash
   .orchestration/bin/merge-arbiter --cleanup
   ```
4. If validation fails, the script resets to pre-merge HEAD.
5. If conflict occurs, the task is marked `blocked`.

## Rules

- Merges are always serial.
- Never merge with a dirty main worktree.
- Do not manually resolve protected-file conflicts without user approval.
