---
name: task-ledger
description: Create, list, inspect, and update durable orchestration tasks in .orchestration/ledger.json using the task state machine.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/task-ledger *) Bash(python3 .orchestration/scripts/orch.py ledger *)
---

# task-ledger

Use this skill whenever task state must be created, inspected, or changed.

## Commands

Create:

```bash
.orchestration/bin/task-ledger new \
  --title "Implement feature X" \
  --objective "Add X behavior" \
  --acceptance "Tests pass and behavior Y is verified" \
  --paths "src/x,tests/x"
```

`--paths` is required by default. Empty `touched_paths` means unknown blast radius and prevents parallel scheduling. Use `--allow-empty-paths` only for explicitly serialized exploratory tasks.

List:

```bash
.orchestration/bin/task-ledger list
```

Show:

```bash
.orchestration/bin/task-ledger show <task-id>
```

Set status:

```bash
.orchestration/bin/task-ledger set-status <task-id> blocked --reason "Needs user decision"
```

## State machine

`pending -> assigned -> running -> review -> merged`

Failures:

`running -> failed -> assigned`

Escalation:

`failed -> blocked`

Unblock:

`blocked -> pending`
