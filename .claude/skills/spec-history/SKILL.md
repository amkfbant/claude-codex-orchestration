---
name: spec-history
description: Show version history or unified diffs for task spec.md files using spec history and spec diff, useful when the user asks how a spec changed or wants to compare approved versions.
allowed-tools: Read Bash(.orchestration/bin/spec history *) Bash(.orchestration/bin/spec diff *) Bash(python3 .orchestration/scripts/orch.py spec history *) Bash(python3 .orchestration/scripts/orch.py spec diff *)
---

# spec-history

Use this skill when the user asks to inspect previous spec versions or compare spec revisions.

## Commands

```bash
.orchestration/bin/spec history <task-id>
.orchestration/bin/spec diff <task-id> --from v1 --to current
.orchestration/bin/spec diff <task-id> --from v1 --to v2
```

Explain which acceptance criteria, scope, and test expectations changed.
