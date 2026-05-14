---
name: lesson-author
description: Help create append-only learned lessons after stuck-detector escalates a task to the user, using .orchestration/LEARNED.md and requiring user approval before adding entries.
allowed-tools: Read Bash(.orchestration/bin/lesson list *) Bash(.orchestration/bin/lesson show *) Bash(.orchestration/bin/lesson add *) Bash(python3 .orchestration/scripts/orch.py lesson *)
---

# lesson-author

Use this skill after `task.escalated_to_user` or when the user wants to capture a reusable project-specific trap.

## Rule

Never add a lesson without user approval. Avoid low-value or one-off notes.

## Command

```bash
.orchestration/bin/lesson add \
  --task <task-id> \
  --context "What happened" \
  --trap "The trap to avoid" \
  --lesson "Reusable rule for future Codex dispatches"
```

List or show lessons:

```bash
.orchestration/bin/lesson list
.orchestration/bin/lesson show L-001
```
