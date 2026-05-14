---
name: diff-reviewer
description: Statically review Codex diffs before acceptance, detecting protected paths, unrelated files, dependency changes, test-weakening patterns, and Codex-created commits via pre_head diffing.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/diff-reviewer *) Bash(python3 .orchestration/scripts/orch.py review-diff *) Bash(git diff *) Bash(git status *)
---

# diff-reviewer

Use this skill before accepting any Codex branch.

## Procedure

```bash
.orchestration/bin/diff-reviewer <task-id>
```

Read:

```text
.orchestration/tasks/<task-id>/review.json
```

The reviewer compares against `last_dispatch_head` when available, so already-committed Codex changes are not missed.

## Reject high severity findings

Reject if:

- protected path changed
- `.env*` or secrets touched
- test skip/only added
- assertion removed
- `.orchestration/` changed
- CI workflow changed without explicit assignment
