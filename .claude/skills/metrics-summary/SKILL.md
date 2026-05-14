---
name: metrics-summary
description: Generate an orchestration metrics summary when the user asks about progress, dashboard status, retry counts, Codex review verdicts, bypass use, active worktrees, or recent orchestration timeline.
allowed-tools: Read Bash(.orchestration/bin/stats *) Bash(python3 .orchestration/scripts/orch.py stats *)
---

# metrics-summary

Use this skill when the user asks “今どんな感じ?”, “進捗は?”, “ダッシュボードを見せて”, or asks about retries, merge queue, review verdicts, runtime, or bypasses.

## Procedure

```bash
.orchestration/bin/stats --format text
```

For a machine-readable view:

```bash
.orchestration/bin/stats --format json
```

For a local standalone report:

```bash
.orchestration/bin/stats --format html --output .orchestration/stats.html
```

Summarize status counts, blocked tasks, merge queue length, and next action. Mention bypasses if non-zero.
